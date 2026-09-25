"""Posizioni stimate sulla mappa quando il GPS non c'è.

In diretta la F1 manda il GPS (``Position.z``) solo agli abbonati F1 TV: una
connessione anonima riceve i tempi e i minisettori, non le posizioni. Qui si
impara, da una sessione già finita dello stesso circuito, dove stanno davvero le
macchine in ogni *stato* — quanti minisettori di fila hanno acceso nel giro — e
la pagina le mette in quel punto.

Verificato sul replay di Monza 2026 (imparato sulla prima metà di gara, provato
sulla seconda): errore mediano 70 m, 96% delle macchine entro 200 m.
"""

from __future__ import annotations

import asyncio
import bisect
import logging
import math
import statistics
from collections import defaultdict
from pathlib import Path

from .feed import download_session, load_archive, season_index
from .state import RaceState

log = logging.getLogger(__name__)

SAMPLE_EVERY = 2.0  # secondi di sessione fra un campione e l'altro
MIN_SAMPLES = 10    # sotto questa soglia uno stato non si considera imparato


def lap_state(sectors: list[dict]) -> tuple[int, int] | None:
    """(k, n): minisettori accesi di fila dall'inizio del giro, su n totali.

    Contare solo quelli di fila scarta i minisettori rimasti accesi dal giro prima.
    Stessa regola di ``lapState`` in ``web/app.js``: devono restare uguali.
    """
    segs = [s for sec in sectors for s in sec.get("seg", [])]
    if not segs:
        return None
    k = 0
    while k < len(segs) and segs[k]:
        k += 1
    return k, len(segs)


class Track:
    """Tracciato MultiViewer in ordine di marcia, con la distanza progressiva."""

    def __init__(self, m: dict):
        pts = list(zip(m["x"], m["y"]))
        n = len(pts)
        near = lambda x, y: min(range(n), key=lambda i: (pts[i][0] - x) ** 2 + (pts[i][1] - y) ** 2)  # noqa: E731
        ms1 = next((s for s in m.get("marshal_sectors", []) if int(s["n"]) == 1), None)
        c1 = next((c for c in m.get("corners", []) if int(c["n"]) == 1), None)
        start = near(ms1["x"], ms1["y"]) if ms1 else 0
        step = -1 if c1 and (near(c1["x"], c1["y"]) - start) % n > n / 2 else 1
        self.order = [pts[(start + step * k) % n] for k in range(n)]
        self.cum = [0.0]
        for k in range(1, n + 1):
            self.cum.append(self.cum[-1] + math.dist(self.order[k - 1], self.order[k % n]))
        self.total = self.cum[-1] or 1.0

    def frac_of(self, x: float, y: float) -> float:
        return self.cum[self.index_of(x, y)] / self.total

    def index_of(self, x: float, y: float, prev: int | None = None, back: int = 15, ahead: int = 60) -> int:
        """Punto del tracciato più vicino. Con ``prev`` cerca solo poco dietro e poco avanti
        a dove era un istante prima: dove la pista passa vicino a se stessa (a Baku il tratto
        a ~2,3 km sfiora quello a ~4,8 km) la ricerca su tutto il giro salterebbe dall'uno
        all'altro."""
        n = len(self.order)
        idx = range(n) if prev is None else [(prev + k) % n for k in range(-back, ahead + 1)]
        return min(idx, key=lambda j: (self.order[j][0] - x) ** 2 + (self.order[j][1] - y) ** 2)

    def point_at(self, f: float) -> tuple[float, float]:
        d = (f % 1) * self.total
        lo = max(0, bisect.bisect_right(self.cum, d) - 1)
        hi = min(lo + 1, len(self.cum) - 1)
        t = (d - self.cum[lo]) / ((self.cum[hi] - self.cum[lo]) or 1)
        a, b = self.order[lo % len(self.order)], self.order[hi % len(self.order)]
        return a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t


def _circular_median(fs: list[float]) -> float:
    """Mediana di frazioni di giro: attorno al traguardo 0,99 e 0,01 sono vicine."""
    ref = fs[0]
    return (ref + statistics.median(((f - ref + 0.5) % 1) - 0.5 for f in fs)) % 1


def _next_state(k: int, n: int) -> int:
    """Dopo "tutti accesi" (appena passato il traguardo) si riparte da 1, non da 0."""
    return 1 if k == n else k + 1


def learn(track_map: dict, folder: Path) -> dict | None:
    """Da una sessione in archivio, per ogni stato: dove comincia, quanto dura e il punto tipico.

    Restituisce ``{"n": minisettori, "states": [...]}`` con n+1 voci (lo stato n è
    "tutti accesi", cioè appena passato il traguardo). Ogni voce è None oppure
    ``{"point": [x, y], "path": [[x, y], ...], "dur": secondi}``: la pagina fa
    avanzare la macchina lungo ``path`` col tempo passato nello stato, e usa
    ``point`` quando non sa da quanto c'è entrata.

    Misurato a Baku 2026 (imparato su FP2, provato su FP1): errore mediano 25 m,
    75% entro 74 m. Nelle libere ~1 caso su 5 resta lontano: giri lenti ed
    entrate/uscite dai box, dove i minisettori non avanzano ma la macchina sì.
    """
    track = Track(track_map)
    info, msgs = load_archive(folder, ["TimingData", "Position.z"])
    st = RaceState()
    st.apply("SessionInfo", info, 0.0)
    where: dict[int, list[float]] = defaultdict(list)   # posizione durante lo stato
    starts: dict[int, list[float]] = defaultdict(list)  # posizione entrando nello stato
    durs: dict[int, list[float]] = defaultdict(list)    # quanto si resta nello stato
    cur: dict[str, tuple[int, float]] = {}
    n_segs, next_sample = None, 0.0
    for ts, topic, data in msgs:
        st.apply(topic, data, ts)
        lines = st.data.get("TimingData", {}).get("Lines") or {}
        if topic == "TimingData":
            for num in (data.get("Lines") or {}) if isinstance(data, dict) else ():
                line = lines.get(num)
                if not isinstance(line, dict):
                    continue
                ks = lap_state(st._sectors(line))
                if not ks or (n_segs and ks[1] != n_segs):
                    continue
                n_segs = n_segs or ks[1]
                k = ks[0]
                prev = cur.get(num)
                if prev and prev[0] == k:
                    continue
                cur[num] = (k, ts)
                if line.get("InPit"):
                    continue
                p = st.positions.get(num)
                if p and p["x"] and ts - p["t"] < 0.6:
                    starts[k].append(track.frac_of(p["x"], p["y"]))
                # oltre il minuto non è un minisettore: è una sosta ai box o una bandiera rossa
                if prev and k == _next_state(prev[0], n_segs) and ts - prev[1] < 60:
                    durs[prev[0]].append(ts - prev[1])
        if ts < next_sample:
            continue
        next_sample = ts + SAMPLE_EVERY
        st.drain_telemetry()  # i campioni pendenti qui non servono: non farli accumulare
        for num, line in lines.items():
            if not isinstance(line, dict) or line.get("InPit") or line.get("Retired") or line.get("Stopped"):
                continue
            p = st.positions.get(num)
            if not p or p["status"] != "OnTrack" or not p["x"] or ts - p["t"] > 1.0 or num not in cur:
                continue
            where[cur[num][0]].append(track.frac_of(p["x"], p["y"]))
    if not n_segs:
        return None

    def med(d: dict, k: int) -> float | None:
        return _circular_median(d[k]) if len(d.get(k, [])) >= MIN_SAMPLES else None

    states = []
    for k in range(n_segs + 1):
        w, a, b = med(where, k), med(starts, k), med(starts, _next_state(k, n_segs))
        if w is None:
            states.append(None)
            continue
        s: dict = {"point": [round(c) for c in track.point_at(w)]}
        if a is not None and b is not None and len(durs.get(k, [])) >= MIN_SAMPLES:
            span = (b - a) % 1
            s["path"] = [[round(c) for c in track.point_at(a + span * i / 8)] for i in range(9)]
            s["dur"] = round(statistics.median(durs[k]), 2)
        states.append(s)
    learned = sum(s is not None for s in states)
    moving = sum(bool(s and s.get("path")) for s in states)
    log.info("posizioni stimate: %d stati su %d imparati (%d con avanzamento) da %s", learned, n_segs + 1, moving, folder.name)
    return {"n": n_segs, "states": states} if learned else None


async def reference_session(current_path: str, circuit_key: int, year: int) -> str | None:
    """La sessione già finita più recente sullo stesso circuito: prima lo stesso
    weekend, altrimenti l'anno prima."""
    for y in (year, year - 1):
        try:
            idx = await season_index(y)
        except Exception as e:  # noqa: BLE001
            log.warning("indice %s non disponibile: %s", y, e)
            continue
        done = []
        for m in idx.get("Meetings", []):
            if int(((m.get("Circuit") or {}).get("Key")) or 0) != circuit_key:
                continue
            for s in m.get("Sessions", []):
                p = s.get("Path")
                if p and p.strip("/") != current_path.strip("/"):
                    done.append((s.get("StartDate") or "", p))
        if done:
            return max(done)[1]
    return None


async def calibrate(track_map: dict, current_path: str, circuit_key: int, year: int) -> dict | None:
    """Trova, scarica e impara. Il calcolo gira in un thread per non fermare il feed."""
    ref = await reference_session(current_path, circuit_key, year)
    if not ref:
        log.info("posizioni stimate: nessuna sessione di riferimento per questo circuito")
        return None
    folder = await download_session(ref, ["TimingData", "Position.z"])
    result = await asyncio.to_thread(learn, track_map, folder)
    if result:
        result["source"] = ref.strip("/").split("/")[-1].split("_", 1)[-1].replace("_", " ")
    return result
