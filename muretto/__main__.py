"""CLI.

  python -m muretto live                       # feed live (durante una sessione)
  python -m muretto sessions 2026              # elenca le sessioni in archivio
  python -m muretto replay <Path> [--speed 10] [--start 00:50:00]
  python -m muretto replay 2026 monza race     # ricerca nell'indice per nome

Il <Path> è quello dell'indice F1, es. 2026/2026-09-06_Italian_Grand_Prix/2026-09-06_Race/
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import shutil
import subprocess
import sys

from . import f1tv
from .feed import ArchiveFeed, LiveFeed, download_session, season_index, ts_to_seconds
from .server import serve


def _find_session(index: dict, words: list[str]) -> str | None:
    words = [w.lower() for w in words]
    for m in index.get("Meetings", []):
        for s in m.get("Sessions", []):
            hay = f"{m.get('Name','')} {m.get('Location','')} {s.get('Name','')}".lower()
            if all(w in hay for w in words) and s.get("Path"):
                return s["Path"]
    return None


def cmd_sessions(args):
    idx = asyncio.run(season_index(args.year))
    for m in idx.get("Meetings", []):
        print(f"\n{m.get('Name')} — {m.get('Location')}")
        for s in m.get("Sessions", []):
            print(f"   {s.get('Name'):<12} {s.get('StartDate','')[:16]}  {s.get('Path','(non ancora in archivio)')}")


def cmd_replay(args):
    path = args.path[0]
    if len(args.path) > 1 or "/" not in path:
        idx = asyncio.run(season_index(int(args.path[0]) if args.path[0].isdigit() else 2026))
        words = args.path[1:] if args.path[0].isdigit() else args.path
        path = _find_session(idx, words)
        if not path:
            sys.exit(f"nessuna sessione trovata per {' '.join(args.path)!r} (vedi `sessions`)")
        print("sessione:", path)
    folder = asyncio.run(download_session(path))
    start = ts_to_seconds(args.start) if args.start else 0.0
    serve(ArchiveFeed(folder, speed=args.speed, start=start), port=args.port)


def cmd_live(args):
    # il Mac va in stop dopo 1 minuto di inattività e la pagina sul telefono si ferma: caffeinate lo
    # tiene sveglio finché questo processo è vivo (-w), e si chiude da solo quando si esce
    if shutil.which("caffeinate"):
        subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])
        logging.getLogger(__name__).info("Mac tenuto sveglio finché gira il live (caffeinate)")
    token = f1tv.load()
    status = f1tv.info(token)
    if token and not status["connected"]:
        logging.getLogger(__name__).warning("account F1 TV non usato: %s", status["problem"])
        token = None
    serve(LiveFeed(auth_token=token), port=args.port)


def main(argv=None):
    p = argparse.ArgumentParser(prog="muretto", description="Live timing F1 a budget zero")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("live", help="collegati al feed live")
    s.add_argument("--port", type=int, default=8765)
    s.set_defaults(fn=cmd_live)

    s = sub.add_parser("sessions", help="elenca le sessioni dell'anno")
    s.add_argument("year", type=int, nargs="?", default=2026)
    s.set_defaults(fn=cmd_sessions)

    s = sub.add_parser("replay", help="riproduci una sessione dall'archivio")
    s.add_argument("path", nargs="+", help="Path dell'indice oppure parole chiave (es. 2026 italian race)")
    s.add_argument("--speed", type=float, default=1.0, help="fattore velocità (0 = istantaneo)")
    s.add_argument("--start", help="salta a HH:MM:SS dall'inizio del feed")
    s.add_argument("--port", type=int, default=8765)
    s.set_defaults(fn=cmd_replay)

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
    args.fn(args)


if __name__ == "__main__":
    main()
