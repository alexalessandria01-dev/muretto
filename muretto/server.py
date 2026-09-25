"""Server locale: pagina statica + WebSocket ``/ws`` che spinge lo stato ai browser.

Un solo task legge il feed (live o replay) e aggiorna ``RaceState``. Ogni
``TICK`` secondi lo snapshot e i campioni ``car``/``pos`` finiscono in uno
storico di ``HISTORY`` secondi; ogni client li riceve con il proprio ritardo
(per allinearsi alla TV senza spoiler), scelto dal browser.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from pathlib import Path

import aiohttp
from aiohttp import WSMsgType, web

import re
from dataclasses import asdict

from . import calibration, compare, f1tv, radio
from .feed import STATIC_BASE, ArchiveFeed, LiveFeed, cache_dir, download_session, season_index
from .state import Lap, RaceState

log = logging.getLogger(__name__)
WEB_DIR = Path(__file__).resolve().parent.parent / "web"
TICK = 0.5
HISTORY = 300  # secondi di storico tenuti in RAM per il ritardo TV
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
    PUSH_TIMEOUT = 5.0  # secondi concessi a un telefono per ricevere un aggiornamento

    def __init__(self, feed):
        self.feed = feed
        self.state = RaceState()
        self.clients: dict[web.WebSocketResponse, dict] = {}  # ws -> {"delay", "sent_state", "sent_tel"}
        self.history: deque = deque(maxlen=int(HISTORY / TICK))  # (t, snapshot_json, car, pos)
        self._circuit_path: str | None = None
        self._cmp_cache: dict[str, compare.Session] = {}  # sessioni d'archivio lette per il confronto giri
        self._cmp_locks: dict[str, asyncio.Lock] = {}
        self.transcriber = radio.Transcriber()  # team radio → testo, con whisper se c'è
        if self.transcriber.enabled:
            self.state.radio_text = self.transcriber.results
        self._laps_saved = 0      # giri già scritti su disco
        self._laps_loaded = False  # storico ripreso (una volta, al primo snapshot)

    async def ingest(self):
        async for topic, data, ts in self.feed:
            try:
                self.state.apply(topic, data, ts)
                if isinstance(self.feed, ArchiveFeed):
                    self.state.replay_position = ts
                if topic in ("SessionInfo", "__snapshot__", "TeamRadio"):
                    self._queue_radio()
                if topic in ("SessionInfo", "__snapshot__"):
                    self._maybe_load_circuit()
                    if not self._laps_loaded:
                        self._laps_loaded = True
                        self._load_laps()
            except Exception:  # noqa: BLE001 - un messaggio storto non deve fermare il feed
                log.exception("errore applicando %s", topic)

    def _queue_radio(self):
        """Mette in coda di trascrizione i radio della sessione non ancora trascritti."""
        info = self.state.data.get("SessionInfo") or {}
        self.transcriber.use_session(info.get("Path"))
        caps = self.state.data.get("TeamRadio", {}).get("Captures") or []
        for c in (caps.values() if isinstance(caps, dict) else caps):
            if isinstance(c, dict) and c.get("Path") and info.get("Path"):
                self.transcriber.enqueue(info["Path"] + c["Path"])

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
            return
        # in diretta il GPS non arriva: si impara dove stanno le macchine da una sessione già finita
        if not isinstance(self.feed, ArchiveFeed) and self.state.circuit_info.get("map", {}).get("x"):
            asyncio.create_task(self._calibrate(key, year, self._circuit_path or ""))

    async def _calibrate(self, key: int, year: int, path: str):
        try:
            cal = await calibration.calibrate(self.state.circuit_info["map"], path, key, year)
        except Exception:  # noqa: BLE001 - senza calibrazione la pagina stima comunque, a grandi linee
            log.exception("calibrazione delle posizioni stimate fallita")
            return
        if cal and path == self._circuit_path:
            self.state.circuit_info["map"]["seg_points"] = cal
            self.state.circuit_info["rev"] = self.state.circuit_info.get("rev", 0) + 1

    async def broadcast(self):
        """Ogni TICK: snapshot nello storico e invio a tutti i telefoni.

        Un errore nello snapshot salta quel giro invece di fermare per sempre gli aggiornamenti,
        e i telefoni si servono in parallelo con un tempo limite: uno lento (o col WiFi che va
        e viene) non deve far aspettare gli altri."""
        loop = asyncio.get_event_loop()
        while True:
            await asyncio.sleep(TICK)
            try:
                car, pos = self.state.drain_telemetry()
                now = loop.time()
                self.history.append((now, json.dumps({"type": "state", **self.snapshot()}), car, pos))
            except Exception:  # noqa: BLE001
                log.exception("snapshot non riuscito, riprovo al prossimo giro")
                continue
            self._save_laps()
            clients = list(self.clients.items())
            results = await asyncio.gather(*(asyncio.wait_for(self._push(ws, c, now), self.PUSH_TIMEOUT) for ws, c in clients),
                                           return_exceptions=True)
            for (ws, _), r in zip(clients, results):
                if isinstance(r, Exception):  # troppo lento o già chiuso: la pagina si ricollega da sola
                    self.clients.pop(ws, None)
                    asyncio.create_task(ws.close())

    # ------------------------------------------------------------ storico dei giri su disco
    def _laps_file(self) -> Path | None:
        path = (self.state.data.get("SessionInfo") or {}).get("Path")
        return cache_dir() / "live" / (path.strip("/").replace("/", "__") + ".json") if path else None

    def _save_laps(self):
        """Lo storico dei giri serve a degrado, battaglie e gap: se il server riparte a metà gara
        la F1 rimanda lo stato attuale ma non i giri passati. Si salva a ogni giro nuovo."""
        total = sum(len(v) for v in self.state.laps.values())
        if isinstance(self.feed, ArchiveFeed) or total == self._laps_saved:
            return
        f = self._laps_file()
        if not f:
            return
        try:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps({num: [asdict(l) for l in laps] for num, laps in self.state.laps.items()}))
            self._laps_saved = total
        except OSError as e:
            log.warning("storico giri non salvato: %s", e)

    def _load_laps(self):
        f = self._laps_file()
        if not f or not f.exists() or isinstance(self.feed, ArchiveFeed):
            return
        try:
            saved = json.loads(f.read_text())
        except (OSError, ValueError) as e:
            log.warning("storico giri illeggibile: %s", e)
            return
        n = 0
        for num, laps in saved.items():
            have = {l.lap for l in self.state.laps.get(num, [])}
            old = [Lap(**l) for l in laps if l["lap"] not in have]
            self.state.laps[num] = sorted(old + list(self.state.laps.get(num, [])), key=lambda l: l.lap)
            n += len(old)
        self._laps_saved = sum(len(v) for v in self.state.laps.values())
        log.info("storico giri ripreso dal disco: %d giri", n)

    async def _push(self, ws, c: dict, now: float):
        """Manda al client lo snapshot e la telemetria fino a ``now - delay``."""
        target = now - c["delay"]
        latest = None
        car, pos = [], []
        for t, snap, cs, ps in self.history:
            if t > target:
                break
            if t > c["sent_state"]:
                latest = (t, snap)
            if t > c["sent_tel"]:
                car.extend(cs)
                pos.extend(ps)
                c["sent_tel"] = t
        if latest:
            c["sent_state"] = latest[0]
            await ws.send_str(latest[1])
        if car:
            await ws.send_str(json.dumps({"type": "car", "samples": car}))
        if pos:
            await ws.send_str(json.dumps({"type": "pos", "samples": pos}))

    async def ws_handler(self, request: web.Request):
        # compressione del WebSocket (permessage-deflate, la negoziano da soli i browser): a fine gara
        # lo stato pesa ~110 KB due volte al secondo, compresso ~20 KB. Per un telefono per due ore
        # sono 1,6 GB contro 0,3
        ws = web.WebSocketResponse(heartbeat=20, max_msg_size=0, compress=True)
        await ws.prepare(request)
        try:
            delay = max(0.0, min(HISTORY - 1, float(request.query.get("delay", 0) or 0)))
        except ValueError:
            delay = 0.0
        loop = asyncio.get_event_loop()
        c = {"delay": delay, "sent_state": 0.0, "sent_tel": loop.time() - delay}
        self.clients[ws] = c
        try:
            history = {n: list(s) for n, s in self.state.telemetry.items()}
            await ws.send_str(json.dumps({"type": "car_history", "drivers": history}))
            await ws.send_str(json.dumps({"type": "pos", "samples": [{"n": n, **p} for n, p in self.state.positions.items()]}))
            if delay == 0 or not self.history:
                await ws.send_str(json.dumps({"type": "state", **self.snapshot()}))
                c["sent_state"] = loop.time()
            else:
                await self._push(ws, c, loop.time())
            async for msg in ws:
                if msg.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
                    break
                if msg.type == WSMsgType.TEXT:
                    self.on_client_message(c, msg.data)
        finally:
            self.clients.pop(ws, None)
        return ws

    def on_client_message(self, c: dict, raw: str):
        """Comandi dal browser: {"delay": 12} — ritardo in secondi, solo per quel browser."""
        try:
            cmd = json.loads(raw)
        except ValueError:
            return
        if "delay" in cmd:
            try:
                c["delay"] = max(0.0, min(HISTORY - 1, float(cmd["delay"] or 0)))
                c["sent_state"] = 0.0  # rimanda subito lo snapshot al nuovo istante
            except (TypeError, ValueError):
                pass

    def snapshot(self) -> dict:
        """Lo stato della gara più quello che la pagina deve sapere del collegamento."""
        live = isinstance(self.feed, LiveFeed)
        return {**self.state.snapshot(), "live": live,
                "f1tv": self._f1tv_info() if live else {"connected": False}}

    def _f1tv_info(self) -> dict:
        if self.feed.auth_rejected:
            return {"connected": False, "problem": "la F1 ha rifiutato il token: rifai il login e incollane uno nuovo"}
        return f1tv.info(self.feed.auth_token)

    # ------------------------------------------------------------ account F1 TV
    async def api_f1tv(self, request: web.Request):
        """GET: stato dell'account. POST {"token": ...}: collega. DELETE: scollega.
        Il token non torna mai indietro alla pagina."""
        if not isinstance(self.feed, LiveFeed):
            raise web.HTTPConflict(text="l'account F1 TV serve solo in diretta")
        if request.method == "POST":
            try:
                raw = (await request.json()).get("token", "")
            except ValueError:
                raw = ""
            token = f1tv.parse_token(raw)
            if not token:
                raise web.HTTPBadRequest(text="non è un token F1 TV: copia il valore del cookie login-session")
            status = f1tv.info(token)
            if not status["connected"]:
                raise web.HTTPBadRequest(text=status["problem"])
            f1tv.save(token)
            self.feed.set_auth_token(token)
        elif request.method == "DELETE":
            f1tv.save(None)
            self.feed.set_auth_token(None)
        return web.json_response(self._f1tv_info())

    async def api_state(self, _request):
        return web.json_response(self.snapshot())

    # ------------------------------------------------------------ confronto giri (archivio)
    _SESSION_PATH = re.compile(r"^\d{4}/[\w-]+/[\w-]+/?$")

    async def api_sessions(self, _request):
        """Sessioni dello stesso weekend già in archivio: quelle su cui si possono confrontare i giri."""
        info = self.state.data.get("SessionInfo") or {}
        path = info.get("Path") or ""
        meeting = (info.get("Meeting") or {}).get("Key")
        out = []
        if path[:4].isdigit() and meeting:
            try:
                idx = await season_index(int(path[:4]))
            except Exception as e:  # noqa: BLE001
                log.warning("indice della stagione non disponibile: %s", e)
                idx = {}
            for m in idx.get("Meetings", []):
                if m.get("Key") == meeting:
                    out = [{"path": s["Path"], "name": s.get("Name", "")} for s in m.get("Sessions", []) if s.get("Path")]
        if isinstance(self.feed, ArchiveFeed) and path and all(o["path"] != path for o in out):
            out.append({"path": path, "name": info.get("Name", "") + " (replay)"})
        return web.json_response(out)

    async def _compare_session(self, path: str) -> compare.Session:
        """Scarica (una volta) e legge la sessione. Ne tiene in memoria al massimo tre."""
        async with self._cmp_locks.setdefault(path, asyncio.Lock()):
            track_map = (self.state.circuit_info or {}).get("map")
            cached = self._cmp_cache.get(path)
            # letta nei primi secondi dopo l'avvio, prima che arrivasse il tracciato: va riletta
            if cached is not None and cached.track is None and track_map and track_map.get("x"):
                del self._cmp_cache[path]
            if path not in self._cmp_cache:
                folder = await download_session(path, compare.TOPICS)
                self._cmp_cache[path] = await asyncio.to_thread(compare.Session, folder, (self.state.circuit_info or {}).get("map"))
                while len(self._cmp_cache) > 3:
                    self._cmp_cache.pop(next(iter(self._cmp_cache)))
            return self._cmp_cache[path]

    async def api_laps(self, request: web.Request):
        path = request.query.get("path", "")
        if not self._SESSION_PATH.match(path):
            raise web.HTTPBadRequest(text="sessione non valida")
        try:
            sess = await self._compare_session(path)
        except aiohttp.ClientError as e:
            raise web.HTTPBadGateway(text=f"archivio F1 non raggiungibile: {e}")
        return web.json_response({"drivers": sess.best_laps()})

    async def api_compare(self, request: web.Request):
        path = request.query.get("path", "")
        nums = [n for n in request.query.get("drivers", "").split(",") if n.isdigit()][:3]
        if not self._SESSION_PATH.match(path) or not nums:
            raise web.HTTPBadRequest(text="sessione o piloti non validi")
        sess = await self._compare_session(path)
        return web.json_response(await asyncio.to_thread(compare.compare, sess, nums))

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
    app.router.add_get("/api/sessions", hub.api_sessions)
    app.router.add_get("/api/laps", hub.api_laps)
    app.router.add_get("/api/compare", hub.api_compare)
    app.router.add_get("/audio", hub.audio)
    for method in ("GET", "POST", "DELETE"):
        app.router.add_route(method, "/api/f1tv", hub.api_f1tv)
    app.router.add_static("/web", WEB_DIR)

    async def start_tasks(app):
        app["tasks"] = [asyncio.create_task(hub.ingest()), asyncio.create_task(hub.broadcast()),
                        asyncio.create_task(hub.transcriber.run())]

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
