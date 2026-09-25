"""Confronto giri: i giri migliori di più piloti sovrapposti lungo il circuito.

È l'analisi che fanno a sessione finita gli analisti di telemetria (per esempio
Federico Albano su FormulaPassion): velocità, gas, freno e marcia metro per metro,
e il distacco accumulato. Funziona sull'archivio che la F1 pubblica dopo ogni
sessione — in diretta la telemetria arriva solo agli abbonati F1 TV.

Il giro si ritaglia dalla fine ufficiale (quando arriva ``LastLapTime``) meno il
tempo sul giro, poi si raffina col GPS: la distanza di ogni campione è la sua
proiezione sul tracciato, così i giri di piloti diversi si allineano metro per
metro anche se il feed annuncia i tempi con un po' di ritardo.
"""

from __future__ import annotations

import bisect
import logging
import re
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from . import strategy
from .calibration import Track
from .feed import load_archive
from .state import CH, merge

log = logging.getLogger(__name__)

TOPICS = ["DriverList", "TimingData", "RaceControlMessages", "CarData.z", "Position.z"]
STEP = 10  # metri fra un punto e l'altro delle curve restituite
DELETED = re.compile(r"CAR (\d+) \([A-Z]{3}\) TIME ([\d:.]+) DELETED")


def utc_seconds(s: str) -> float | None:
    """'2026-09-24T11:46:46.6453731Z' → secondi epoch (la F1 usa 7 cifre di frazione)."""
    try:
        base, _, frac = s.rstrip("Z").partition(".")
        dt = datetime.fromisoformat(base).replace(tzinfo=timezone.utc)
        return dt.timestamp() + (float("0." + frac) if frac else 0.0)
    except (ValueError, AttributeError):
        return None


class Session:
    """Una sessione in archivio, letta una volta: giri, telemetria e GPS per pilota."""

    def __init__(self, folder: Path, track_map: dict | None):
        self.info, msgs = load_archive(folder, TOPICS)
        self.track = Track(track_map) if track_map and track_map.get("x") else None
        self.corners = (track_map or {}).get("corners") or []
        self.drivers: dict = {}
        self.laps: dict[str, list[dict]] = defaultdict(list)   # num -> [{lap, time, seconds, end}]
        self.car: dict[str, list[tuple]] = defaultdict(list)   # num -> [(utc, speed, throttle, brake, gear, rpm)]
        self.pos: dict[str, list[tuple]] = defaultdict(list)   # num -> [(utc, x, y)]
        deleted: set[tuple[str, str]] = set()
        offsets: list[float] = []
        td: dict = {}
        for ts, topic, data in msgs:
            if topic == "DriverList" and isinstance(data, dict):
                merge(self.drivers, data)
            elif topic == "TimingData":
                for num, line in ((data or {}).get("Lines") or {}).items():
                    if not isinstance(line, dict):
                        continue
                    merge(td.setdefault(num, {}), line)
                    value = (line.get("LastLapTime") or {}).get("Value")
                    secs = strategy.lap_time_seconds(value)
                    if secs:
                        # a fine giro i settori sono ancora quelli del giro appena chiuso
                        secs_now = td[num].get("Sectors") or []
                        if isinstance(secs_now, dict):
                            secs_now = [secs_now.get(str(i), {}) for i in range(3)]
                        sectors = [strategy.lap_time_seconds((s or {}).get("Value")) for s in secs_now]
                        self.laps[num].append({"lap": td[num].get("NumberOfLaps"), "time": value, "seconds": secs,
                                               "sectors": sectors, "end_ts": ts})
            elif topic == "RaceControlMessages":
                ms = (data or {}).get("Messages") or []
                for m in (ms.values() if isinstance(ms, dict) else ms):
                    hit = DELETED.search(str((m or {}).get("Message", "")))
                    if hit:
                        deleted.add((hit.group(1), hit.group(2)))
            elif topic == "CarData.z":
                entries = (data or {}).get("Entries") or []
                for e in entries:
                    t = utc_seconds(e.get("Utc", ""))
                    if t is None:
                        continue
                    for num, car in (e.get("Cars") or {}).items():
                        ch = car.get("Channels") or {}
                        v = {name: ch.get(code) for code, name in CH.items()}
                        self.car[num].append((t, v["speed"], v["throttle"], v["brake"], v["gear"], v["rpm"]))
                if entries:
                    last = utc_seconds(entries[-1].get("Utc", ""))
                    if last is not None:
                        offsets.append(ts - last)
            elif topic == "Position.z":
                for p in (data or {}).get("Position") or []:
                    t = utc_seconds(p.get("Timestamp", ""))
                    for num, e in (p.get("Entries") or {}).items():
                        if t is not None and e.get("X") not in (None, 0):
                            self.pos[num].append((t, e["X"], e["Y"]))
        # tempo della sessione → UTC: i messaggi arrivano un attimo dopo il loro ultimo campione
        self.offset = statistics.median(offsets) if offsets else 0.0
        for num, laps in self.laps.items():
            for lap in laps:
                lap["end"] = lap.pop("end_ts") - self.offset
                lap["deleted"] = (num, lap["time"]) in deleted

    def telemetry_ok(self, num: str, lap: dict) -> bool:
        """A volte il feed F1 congela la telemetria di una macchina: stesso valore per tutto il
        giro (Baku 2026, FP1: Sainz, Norris e Pérez nel giro migliore). In un giro vero circa un
        campione su otto è uguale al precedente; oltre uno su quattro il giro non è credibile."""
        v = [s[1] for s in self.car.get(num, []) if lap["end"] - lap["seconds"] <= s[0] <= lap["end"]]
        if len(v) < 50:
            return False
        repeats = sum(1 for i in range(1, len(v)) if v[i] == v[i - 1])
        return repeats / (len(v) - 1) <= 0.25

    def best_laps(self) -> list[dict]:
        """Per ogni pilota il giro più veloce valido (esclusi i tempi cancellati), dal più veloce,
        e il giro da confrontare: il più veloce con telemetria buona."""
        out = []
        for num, laps in self.laps.items():
            valid = sorted((l for l in laps if not l["deleted"]), key=lambda l: l["seconds"])
            if not valid or not self.car.get(num):
                continue
            best = valid[0]
            usable = next((l for l in valid if self.telemetry_ok(num, l)), None)
            d = self.drivers.get(num) or {}
            out.append({"num": num, "tla": d.get("Tla", num), "colour": "#" + d.get("TeamColour", "888888"),
                        "lap": best["lap"], "time": best["time"], "seconds": best["seconds"], "laps": len(laps),
                        "cmp_lap": usable["lap"] if usable else None, "cmp_time": usable["time"] if usable else None})
        return sorted(out, key=lambda r: r["seconds"])

    # ------------------------------------------------------------------ un giro

    def _position_at(self, num: str, t: float) -> tuple[float, float] | None:
        pts = self.pos.get(num) or []
        i = bisect.bisect_left(pts, (t,))
        if i == 0 or i >= len(pts):
            return None
        (t0, x0, y0), (t1, x1, y1) = pts[i - 1], pts[i]
        if t1 - t0 > 2:
            return None
        k = (t - t0) / ((t1 - t0) or 1)
        return x0 + (x1 - x0) * k, y0 + (y1 - y0) * k

    def sector_marks(self) -> tuple[float, float] | None:
        """Dove finiscono settore 1 e 2, in metri dal traguardo: mediana sui giri migliori di
        tutti i piloti (a Baku 2026 i singoli piloti stanno entro 4-8 m dalla mediana)."""
        if not hasattr(self, "_marks"):
            d1s, d2s = [], []
            for b in self.best_laps():
                lap = next((l for l in self.laps[b["num"]] if l["time"] == b["cmp_time"] and not l["deleted"]), None)
                sec = lap and lap.get("sectors")
                if not sec or None in sec or abs(sum(sec) - lap["seconds"]) > 0.01:
                    continue
                tr = self.lap_trace(b["num"], lap)
                if tr:
                    d1s.append(_interp(tr["t"], tr["d"], sec[0]))
                    d2s.append(_interp(tr["t"], tr["d"], sec[0] + sec[1]))
            self._marks = (statistics.median(d1s), statistics.median(d2s)) if len(d1s) >= 5 else None
        return self._marks

    def crossing(self, num: str, t_guess: float) -> float | None:
        """Istante del passaggio sul traguardo vicino a ``t_guess``.

        I punti GPS della F1 arrivano a scatti (lo stesso punto ripetuto, poi un balzo di
        50 m): presi uno per uno sbaglierebbero il passaggio di decimi. Sul rettilineo del
        traguardo la velocità è quasi costante, quindi una retta ai minimi quadrati sui
        punti dei 3 s attorno compensa gli scatti."""
        if not self.track:
            return None
        n, length = len(self.track.order), self.track.total / 10
        pts, prev = [], None
        for t, x, y in (p for p in self.pos.get(num, []) if t_guess - 3 <= p[0] <= t_guess + 3):
            # cercato solo vicino al traguardo e poi in avanti: dove la pista passa vicino a se
            # stessa la ricerca su tutto il giro aggancerebbe il tratto sbagliato
            prev = self.track.index_of(x, y, prev=n - 60 if prev is None else prev, back=60 if prev is None else 3,
                                       ahead=120 if prev is None else 30)
            d = self.track.cum[prev] / 10
            pts.append((t, d - length if d > length / 2 else d))  # metri dal traguardo, negativi prima
        if len(pts) < 6:
            return None
        mt, md = statistics.fmean(t for t, _ in pts), statistics.fmean(d for _, d in pts)
        var = sum((t - mt) ** 2 for t, _ in pts)
        v = sum((t - mt) * (d - md) for t, d in pts) / (var or 1)
        if not 20 < v < 110:  # m/s: fuori da qui non è un passaggio sul rettilineo
            return None
        return mt - md / v

    def lap_trace(self, num: str, lap: dict) -> dict | None:
        """Campioni del giro con la distanza dal traguardo: {d, t, speed, throttle, brake, gear, rpm}.

        La distanza viene dalla velocità (regolare, ~4 campioni al secondo) e non dal GPS
        (a scatti); il GPS serve solo a trovare i due passaggi sul traguardo."""
        end, secs = lap["end"], lap["seconds"]
        samples = sorted(s for s in self.car.get(num, []) if end - secs - 3 <= s[0] <= end + 3)
        if len(samples) < 20:
            return None
        t0, t1 = self.crossing(num, end - secs), self.crossing(num, end)
        if t0 is None or t1 is None or not 0.9 * secs < t1 - t0 < 1.1 * secs:
            t0, t1 = end - secs, end  # senza GPS: la finestra dei tempi ufficiali (come fa FastF1)
            log.info("confronto giri: %s giro %s senza passaggi GPS, uso i tempi ufficiali", num, lap.get("lap"))
        inside = [s for s in samples if t0 < s[0] < t1]
        if len(inside) < 20:
            return None

        def speed_at(t: float) -> float:
            i = bisect.bisect_left(samples, (t,))
            a, b = samples[max(0, i - 1)], samples[min(i, len(samples) - 1)]
            k = (t - a[0]) / ((b[0] - a[0]) or 1)
            return ((a[1] or 0) + ((b[1] or 0) - (a[1] or 0)) * k) / 3.6

        # distanza integrando la velocità dal passaggio sul traguardo (trapezi)
        pts = [(t0, speed_at(t0))] + [(s[0], (s[1] or 0) / 3.6) for s in inside] + [(t1, speed_at(t1))]
        dist = [0.0]
        for (ta, va), (tb, vb) in zip(pts, pts[1:]):
            dist.append(dist[-1] + (va + vb) / 2 * (tb - ta))
        # lunghezza vera del giro e durata ufficiale: il distacco al traguardo è quello vero
        length = self.track.total / 10 if self.track else dist[-1]
        kd, kt = length / (dist[-1] or 1), secs / ((t1 - t0) or secs)
        first, last = inside[0], inside[-1]
        rows = [(0.0, 0.0, *first[1:])]
        rows += [(dist[i + 1] * kd, (s[0] - t0) * kt, *s[1:]) for i, s in enumerate(inside)]
        rows.append((length, secs, *last[1:]))
        return {"d": [r[0] for r in rows], "t": [r[1] for r in rows], "speed": [r[2] for r in rows],
                "throttle": [r[3] for r in rows], "brake": [r[4] for r in rows], "gear": [r[5] for r in rows],
                "rpm": [r[6] for r in rows]}


def _interp(xs: list[float], ys: list, x: float, step: bool = False):
    i = bisect.bisect_left(xs, x)
    if i <= 0:
        return ys[0]
    if i >= len(xs):
        return ys[-1]
    if step:
        return ys[i - 1]
    a, b = ys[i - 1], ys[i]
    if a is None or b is None:
        return a if b is None else b
    k = (x - xs[i - 1]) / ((xs[i] - xs[i - 1]) or 1)
    return a + (b - a) * k


def _pin_sectors(tr: dict, lap: dict, marks: tuple[float, float] | None) -> dict:
    """Aggancia il tempo del giro ai settori ufficiali: alla fine di ogni settore il tempo
    diventa esattamente quello cronometrato dalla F1, e in mezzo si stira in proporzione.
    Senza, a Baku FP1 il distacco a fine settore sbagliava fino a 0,15 s (pochi metri di
    errore di posizione in un punto lento della pista)."""
    sec = lap.get("sectors") or []
    if not marks or len(sec) != 3 or None in sec or abs(sum(sec) - lap["seconds"]) > 0.01:
        return tr
    ds = [0.0, marks[0], marks[1], tr["d"][-1]]
    ts = [0.0] + [_interp(tr["d"], tr["t"], d) for d in ds[1:]]
    official = [0.0, sec[0], sec[0] + sec[1], lap["seconds"]]
    if any(b <= a for a, b in zip(ts, ts[1:])):
        return tr
    out = []
    for d, t in zip(tr["d"], tr["t"]):
        k = min(bisect.bisect_right(ds, d), 3) - 1
        out.append(official[k] + (t - ts[k]) * (official[k + 1] - official[k]) / (ts[k + 1] - ts[k]))
    return {**tr, "t": out}


def compare(session: Session, nums: list[str]) -> dict:
    """I giri migliori dei piloti scelti, ricampionati ogni STEP metri, col distacco dal primo."""
    best = {b["num"]: b for b in session.best_laps()}
    traces = []
    skipped = []
    for num in nums:
        b = best.get(num)
        if b and not b["cmp_time"]:
            skipped.append(b["tla"])  # nessun giro con telemetria buona
            continue
        lap = next((l for l in session.laps.get(num, []) if b and l["time"] == b["cmp_time"] and not l["deleted"]), None)
        tr = session.lap_trace(num, lap) if lap else None
        if tr:
            tr = _pin_sectors(tr, lap, session.sector_marks())
        if tr and len(tr["d"]) > 20:
            traces.append(({**b, "lap": b["cmp_lap"], "time": b["cmp_time"],
                            "note": "" if b["cmp_time"] == b["time"] else
                            f"il giro migliore ({b['time']}) ha la telemetria F1 guasta: uso il {b['cmp_time']}"}, tr))
    if not traces:
        return {"distance": [], "drivers": [], "corners": [], "skipped": skipped}
    # senza tracciato ogni giro ha la sua lunghezza stimata dalla velocità: si riportano tutti
    # a quella del primo, così al traguardo il distacco resta quello ufficiale
    length = traces[0][1]["d"][-1]
    for _, tr in traces:
        k = length / (tr["d"][-1] or length)
        tr["d"] = [d * k for d in tr["d"]]
    grid = [float(d) for d in range(0, int(length), STEP)] + [length]
    ref_t = [_interp(traces[0][1]["d"], traces[0][1]["t"], d) for d in grid]
    out = []
    for b, tr in traces:
        t = [_interp(tr["d"], tr["t"], d) for d in grid]
        out.append({
            **{k: b[k] for k in ("num", "tla", "colour", "lap", "time", "note")},
            "top_speed": max((s for s in tr["speed"] if s is not None), default=None),
            "speed": [round(_interp(tr["d"], tr["speed"], d)) for d in grid],
            "throttle": [round(_interp(tr["d"], tr["throttle"], d) or 0) for d in grid],
            "brake": [100 if _interp(tr["d"], tr["brake"], d, step=True) else 0 for d in grid],
            "gear": [_interp(tr["d"], tr["gear"], d, step=True) for d in grid],
            "delta": [round(a - r, 3) for a, r in zip(t, ref_t)],
        })
    corners = []
    if session.track:
        for c in session.corners:
            corners.append({"n": c["n"], "d": round(session.track.frac_of(c["x"], c["y"]) * session.track.total / 10)})
    return {"distance": grid, "drivers": out, "corners": corners, "skipped": skipped}
