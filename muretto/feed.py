"""Sorgenti dati del live timing F1.

Due sorgenti, stesso formato dei messaggi ``(topic, data, ts)``:

- :class:`LiveFeed` — hub SignalR Core ``livetiming.formula1.com/signalrcore``
- :class:`ArchiveFeed` — file ``<Topic>.jsonStream`` di una sessione passata,
  riprodotti alla velocità voluta (replay)

I canali ``.z`` arrivano come base64 di JSON compresso (deflate raw).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import uuid
import zlib
from pathlib import Path
from typing import AsyncIterator, Iterable

import aiohttp

log = logging.getLogger(__name__)

LIVE_HOST = "livetiming.formula1.com"
STATIC_BASE = f"https://{LIVE_HOST}/static/"
RECORD_SEP = "\x1e"

# Canali del feed. Stessi nomi nel live (Subscribe) e nell'archivio (<Topic>.jsonStream).
TOPICS = [
    "Heartbeat",
    "SessionInfo",
    "SessionStatus",
    "SessionData",
    "TrackStatus",
    "ExtrapolatedClock",
    "LapCount",
    "WeatherData",
    "DriverList",
    "TimingData",
    "TimingAppData",
    "TimingStats",
    "TyreStintSeries",
    "PitLaneTimeCollection",
    "RaceControlMessages",
    "TeamRadio",
    "CarData.z",
    "Position.z",
    # LapSeries arriva sempre; gli altri tre solo in gara (verificati nell'archivio,
    # in diretta senza abbonamento da confermare alla prima gara)
    "PitStopSeries",
    "ChampionshipPrediction",
    "OvertakeSeries",
    "LapSeries",
]


# --------------------------------------------------------------------------- parsing


def inflate_z(payload: str):
    """Decodifica un canale ``.z``: base64 → deflate raw → JSON."""
    raw = zlib.decompress(base64.b64decode(payload), -zlib.MAX_WBITS)
    return json.loads(raw)


def ts_to_seconds(ts: str) -> float:
    h, m, s = ts.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def decode_topic(topic: str, data):
    """I canali ``.z`` viaggiano come stringa compressa, gli altri come JSON."""
    if topic.endswith(".z") and isinstance(data, str):
        return inflate_z(data)
    return data


def parse_stream_line(line: str, topic: str):
    """Riga di un ``.jsonStream``: ``HH:MM:SS.mmm{json}`` (la prima può avere il BOM)."""
    line = line.lstrip("﻿").rstrip("\r\n")
    ts, body = line[:12], line[12:]
    return ts_to_seconds(ts), decode_topic(topic, json.loads(body))


def split_frames(raw: str) -> list[dict]:
    """Frame SignalR Core separati da ``\\x1e``."""
    out = []
    for part in raw.split(RECORD_SEP):
        if part.strip():
            out.append(json.loads(part))
    return out


# --------------------------------------------------------------------------- live


class LiveFeed:
    """Client SignalR Core del live timing. Itera ``(topic, data, ts)``.

    Il primo elemento prodotto è lo snapshot della Subscribe, come
    ``("__snapshot__", {topic: data, ...}, ts)``; poi i delta man mano che
    arrivano. Riconnette da solo con backoff se la connessione cade.
    """

    def __init__(self, topics: Iterable[str] = TOPICS):
        self.topics = list(topics)
        self.base = f"{LIVE_HOST}/signalrcore"

    async def _negotiate(self, session: aiohttp.ClientSession) -> tuple[str, str]:
        cookie = ""
        async with session.options(f"https://{self.base}/negotiate") as r:
            c = r.cookies.get("AWSALBCORS")
            if c:
                cookie = f"AWSALBCORS={c.value}"
        async with session.post(f"https://{self.base}/negotiate?negotiateVersion=1", headers={"Cookie": cookie}) as r:
            r.raise_for_status()
            neg = await r.json()
        return neg["connectionToken"], cookie

    async def _run_once(self) -> AsyncIterator[tuple[str, object, float]]:
        async with aiohttp.ClientSession() as session:
            token, cookie = await self._negotiate(session)
            headers = {"User-Agent": "BestHTTP", "Accept-Encoding": "gzip,identity", "Cookie": cookie}
            async with session.ws_connect(f"wss://{self.base}?id={token}", headers=headers, heartbeat=None) as ws:
                await ws.send_str(json.dumps({"protocol": "json", "version": 1}) + RECORD_SEP)
                hs = await ws.receive()
                if hs.type != aiohttp.WSMsgType.TEXT or json.loads(hs.data.rstrip(RECORD_SEP) or "{}").get("error"):
                    raise ConnectionError(f"handshake SignalR fallito: {hs.data!r}")
                inv = str(uuid.uuid4())
                await ws.send_str(json.dumps({"type": 1, "invocationId": inv, "target": "Subscribe", "arguments": [self.topics]}) + RECORD_SEP)
                log.info("connesso al live timing, in attesa dello snapshot")
                loop = asyncio.get_event_loop()
                async for msg in ws:
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        break
                    for frame in split_frames(msg.data):
                        t = frame.get("type")
                        if t == 3 and frame.get("invocationId") == inv:
                            if frame.get("error"):
                                raise ConnectionError(f"Subscribe rifiutata: {frame['error']}")
                            snap = {k: decode_topic(k, v) for k, v in (frame.get("result") or {}).items()}
                            yield "__snapshot__", snap, loop.time()
                        elif t == 1 and frame.get("target") == "feed":
                            topic, data, _utc = frame["arguments"]
                            yield topic, decode_topic(topic, data), loop.time()
                        elif t == 6:  # ping del server
                            await ws.send_str(json.dumps({"type": 6}) + RECORD_SEP)
                        elif t == 7:
                            raise ConnectionError(f"chiusura dal server: {frame.get('error')}")
                raise ConnectionError("websocket chiuso")

    async def __aiter__(self) -> AsyncIterator[tuple[str, object, float]]:
        delay = 2
        while True:
            try:
                async for item in self._run_once():
                    delay = 2
                    yield item
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - qualunque errore di rete: si riconnette
                log.warning("feed live caduto (%s), riconnetto tra %ss", e, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30)


# --------------------------------------------------------------------------- archivio / replay


def cache_dir() -> Path:
    return Path.home() / ".cache" / "muretto"


async def fetch_static(session: aiohttp.ClientSession, rel: str, dest: Path) -> Path | None:
    """Scarica ``static/<rel>`` in ``dest`` (una volta sola). None se 404."""
    if dest.exists():
        return dest
    async with session.get(STATIC_BASE + rel) as r:
        if r.status == 404:
            return None
        r.raise_for_status()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(await r.read())
    return dest


async def season_index(year: int) -> dict:
    async with aiohttp.ClientSession() as s:
        async with s.get(f"{STATIC_BASE}{year}/Index.json") as r:
            r.raise_for_status()
            return json.loads((await r.text()).lstrip("﻿"))


async def download_session(path: str, topics: Iterable[str] = TOPICS) -> Path:
    """Scarica ``SessionInfo.json`` e i ``.jsonStream`` di una sessione nella cache."""
    path = path.strip("/") + "/"
    dest = cache_dir() / path
    async with aiohttp.ClientSession() as s:
        await fetch_static(s, path + "SessionInfo.json", dest / "SessionInfo.json")
        for t in topics:
            got = await fetch_static(s, f"{path}{t}.jsonStream", dest / f"{t}.jsonStream")
            log.info("%-24s %s", t, "ok" if got else "assente")
    return dest


def load_archive(folder: Path, topics: Iterable[str] = TOPICS) -> tuple[dict, list[tuple[float, str, object]]]:
    """Legge la cartella scaricata: (SessionInfo, messaggi ordinati per tempo)."""
    info_file = folder / "SessionInfo.json"
    info = json.loads(info_file.read_text(encoding="utf-8-sig")) if info_file.exists() else {}
    messages: list[tuple[float, str, object]] = []
    for t in topics:
        f = folder / f"{t}.jsonStream"
        if not f.exists():
            continue
        with f.open(encoding="utf-8") as fh:
            for n, line in enumerate(fh):
                if not line.strip():
                    continue
                try:
                    ts, data = parse_stream_line(line, t)
                except Exception as e:  # noqa: BLE001
                    log.warning("%s riga %d non parsabile: %s", t, n, e)
                    continue
                messages.append((ts, t, data))
    messages.sort(key=lambda m: m[0])
    return info, messages


class ArchiveFeed:
    """Replay di una sessione: produce ``(topic, data, ts)`` rispettando i tempi.

    ``speed`` è il fattore (1 = tempo reale, 0 = più veloce possibile);
    ``start`` salta all'istante indicato (secondi dall'inizio del feed)
    applicando istantaneamente tutto ciò che viene prima.
    """

    def __init__(self, folder: Path, speed: float = 1.0, start: float = 0.0):
        self.info, self.messages = load_archive(folder)
        self.speed = speed
        self.start = start
        self.position = 0.0  # istante corrente del replay, per la UI

    async def __aiter__(self) -> AsyncIterator[tuple[str, object, float]]:
        if self.info:
            yield "SessionInfo", self.info, 0.0
        loop = asyncio.get_event_loop()
        t0 = loop.time()
        for ts, topic, data in self.messages:
            if ts > self.start and self.speed > 0:
                due = t0 + (ts - self.start) / self.speed
                delay = due - loop.time()
                if delay > 0:
                    await asyncio.sleep(delay)
            self.position = ts
            yield topic, data, ts
        log.info("replay terminato")
        while True:  # tiene vivo il server a fine replay
            await asyncio.sleep(3600)
