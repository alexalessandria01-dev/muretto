"""Server locale: pagina statica + WebSocket ``/ws`` che spinge lo stato ai browser.

Un solo task legge il feed (live o replay) e mette i messaggi in coda; un
secondo li applica a ``RaceState`` dopo il ritardo scelto dall'utente (per
sincronizzarsi con la TV). Ogni ``TICK`` secondi tutti i client ricevono
``state`` (vista compatta) più i campioni ``car``/``pos`` arrivati.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from pathlib import Path

import aiohttp
from aiohttp import WSMsgType, web

from .feed import STATIC_BASE, ArchiveFeed
from .state import RaceState

log = logging.getLogger(__name__)
WEB_DIR = Path(__file__).resolve().parent.parent / "web"
TICK = 0.5
MULTIVIEWER = "https://api.multiviewer.app/api/v1/circuits/{key}/{year}"


async def fetch_circuit(key: int, year: int) -> dict:
    """Tracciato, curve, settori dei commissari e pit loss (normale/SC/VSC) da MultiViewer."""
    async with aiohttp.ClientSession() as s:
        async with s.get(MULTIVIEWER.format(key=key, year=year), timeout=aiohttp.ClientTimeout(total=20)) as r:
            r.raise_for_status()
            d = await r.json()
    pl = d.get("pitLoss") or {}
    return {
        "pit_loss": {k: float(pl[k]) for k in ("normal", "sc", "vsc") if pl.get(k)},
        "map": {
            "x": d.get("x", []), "y": d.get("y", []), "rotation": d.get("rotation", 0),
            "corners": [{"n": c["number"], "x": c["trackPosition"]["x"], "y": c["trackPosition"]["y"], "angle": c.get("angle", 0)} for c in d.get("corners", [])],
            "marshal_sectors": [{"n": m["number"], "x": m["trackPosition"]["x"], "y": m["trackPosition"]["y"]} for m in d.get("marshalSectors", [])],
        },
        "name": d.get("circuitName"),
    }


class Hub:
    def __init__(self, feed):
        self.feed = feed
        self.state = RaceState()
        self.clients: set[web.WebSocketResponse] = set()
        self.delay = 0.0  # secondi di ritardo per allinearsi alla TV
        self._queue: deque = deque()
        self._circuit_path: str | None = None

    async def ingest(self):
        loop = asyncio.get_event_loop()
        async for topic, data, ts in self.feed:
            self._queue.append((loop.time(), topic, data, ts))

    async def apply_loop(self):
        loop = asyncio.get_event_loop()
        while True:
            if not self._queue or self._queue[0][0] + self.delay > loop.time():
                await asyncio.sleep(0.05)
                continue
            _, topic, data, ts = self._queue.popleft()
            try:
                self.state.apply(topic, data, ts)
                if isinstance(self.feed, ArchiveFeed):
                    self.state.replay_position = ts
                if topic in ("SessionInfo", "__snapshot__"):
                    self._maybe_load_circuit()
            except Exception:  # noqa: BLE001 - un messaggio storto non deve fermare il feed
                log.exception("errore applicando %s", topic)

    def _maybe_load_circuit(self):
        info = self.state.data.get("SessionInfo") or {}
        path = info.get("Path")
        key = ((info.get("Meeting") or {}).get("Circuit") or {}).get("Key")
        if not path or not key or path == self._circuit_path:
            return
        self._circuit_path = path
        year = int(path.split("/")[0]) if path[:4].isdigit() else 2026
        asyncio.create_task(self._load_circuit(int(key), year))

    async def _load_circuit(self, key: int, year: int):
        try:
            self.state.circuit_info = await fetch_circuit(key, year)
            log.info("circuito %s: pit loss %s", self.state.circuit_info.get("name"), self.state.circuit_info.get("pit_loss"))
        except Exception as e:  # noqa: BLE001
            log.warning("MultiViewer non raggiungibile (%s): uso la tabella pit loss e la mappa dai GPS", e)

    async def broadcast(self):
        while True:
            await asyncio.sleep(TICK)
            car, pos = self.state.drain_telemetry()
            if not self.clients:
                continue
            msgs = [json.dumps({"type": "state", "delay": self.delay, **self.state.snapshot()})]
            if car:
                msgs.append(json.dumps({"type": "car", "samples": car}))
            if pos:
                msgs.append(json.dumps({"type": "pos", "samples": pos}))
            for ws in list(self.clients):
                try:
                    for m in msgs:
                        await ws.send_str(m)
                except Exception:  # noqa: BLE001
                    self.clients.discard(ws)

    async def ws_handler(self, request: web.Request):
        ws = web.WebSocketResponse(heartbeat=20, max_msg_size=0)
        await ws.prepare(request)
        self.clients.add(ws)
        try:
            await ws.send_str(json.dumps({"type": "state", "delay": self.delay, **self.state.snapshot()}))
            history = {n: list(s) for n, s in self.state.telemetry.items()}
            await ws.send_str(json.dumps({"type": "car_history", "drivers": history}))
            await ws.send_str(json.dumps({"type": "pos", "samples": [{"n": n, **p} for n, p in self.state.positions.items()]}))
            async for msg in ws:
                if msg.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
                    break
                if msg.type == WSMsgType.TEXT:
                    self.on_client_message(msg.data)
        finally:
            self.clients.discard(ws)
        return ws

    def on_client_message(self, raw: str):
        """Comandi dal browser: {"pit_loss": 21.5|null}, {"delay": 12}."""
        try:
            cmd = json.loads(raw)
        except ValueError:
            return
        if "pit_loss" in cmd:
            v = cmd["pit_loss"]
            self.state.pit_loss_override = float(v) if v is not None else None
        if "delay" in cmd:
            try:
                self.delay = max(0.0, float(cmd["delay"] or 0))
            except (TypeError, ValueError):
                pass

    async def api_state(self, _request):
        return web.json_response(self.state.snapshot())

    async def api_map(self, _request):
        return web.json_response(self.state.circuit_info.get("map") or {})

    async def audio(self, request: web.Request):
        """Proxy dei team radio: /audio?p=<Path relativo a static/>."""
        rel = request.query.get("p", "")
        if not rel or ".." in rel or not rel.endswith(".mp3"):
            raise web.HTTPBadRequest()
        headers = {}
        if "Range" in request.headers:
            headers["Range"] = request.headers["Range"]
        async with aiohttp.ClientSession() as s:
            async with s.get(STATIC_BASE + rel, headers=headers) as r:
                body = await r.read()
                resp = web.Response(status=r.status, body=body, content_type="audio/mpeg")
                for h in ("Content-Range", "Accept-Ranges"):
                    if h in r.headers:
                        resp.headers[h] = r.headers[h]
                return resp


async def index(_request):
    return web.FileResponse(WEB_DIR / "index.html")


def make_app(feed) -> web.Application:
    hub = Hub(feed)
    app = web.Application()
    app["hub"] = hub
    app.router.add_get("/", index)
    app.router.add_get("/ws", hub.ws_handler)
    app.router.add_get("/api/state", hub.api_state)
    app.router.add_get("/api/map", hub.api_map)
    app.router.add_get("/audio", hub.audio)
    app.router.add_static("/web", WEB_DIR)

    async def start_tasks(app):
        app["tasks"] = [asyncio.create_task(hub.ingest()), asyncio.create_task(hub.apply_loop()), asyncio.create_task(hub.broadcast())]

    async def stop_tasks(app):
        for t in app["tasks"]:
            t.cancel()

    app.on_startup.append(start_tasks)
    app.on_cleanup.append(stop_tasks)
    return app


def serve(feed, host: str = "0.0.0.0", port: int = 8765):
    app = make_app(feed)
    log.info("dashboard su http://localhost:%d  (in rete: http://<ip-del-mac>:%d)", port, port)
    web.run_app(app, host=host, port=port, print=None)
