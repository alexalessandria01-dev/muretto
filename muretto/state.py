"""Stato della sessione: fonde i delta del feed e produce le viste per la pagina.

``RaceState.apply(topic, data, ts)`` è l'unico ingresso, identico per live e
replay. ``snapshot()`` è ciò che il browser riceve ogni mezzo secondo;
``drain_telemetry()`` restituisce i campioni di telemetria/GPS arrivati
dall'ultima chiamata.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass

from . import stewards, strategy
from .feed import STATIC_BASE

TRACK_STATUS = {"1": "green", "2": "yellow", "4": "sc", "5": "red", "6": "vsc", "7": "vsc_ending"}
# canali di CarData.z
CH = {"0": "rpm", "2": "speed", "3": "gear", "4": "throttle", "5": "brake", "45": "drs"}
TELEMETRY_KEEP = 600  # campioni per pilota (~2,5 min a 3,7 Hz)


def merge(base, delta):
    """Fusione ricorsiva in stile F1: dict su dict, dict-indice su liste, scalari sostituiti."""
    for k, v in delta.items():
        if k == "_kf":
            continue
        if k == "_deleted":
            # la F1 toglie voci così: {"PitTimes": {"_deleted": ["23"]}}. Ignorarlo lasciava in scheda
            # per sempre i "30:46 min" di pit lane della bandiera rossa di Monza
            for key in v if isinstance(v, list) else [v]:
                if isinstance(base, dict):
                    base.pop(str(key), None)
            continue
        cur = base.get(k)
        if isinstance(v, dict) and isinstance(cur, dict):
            merge(cur, v)
        elif isinstance(v, dict) and isinstance(cur, list):
            for idx, item in v.items():
                try:
                    i = int(idx)
                except ValueError:
                    continue
                if i < len(cur):
                    if isinstance(item, dict) and isinstance(cur[i], dict):
                        merge(cur[i], item)
                    else:
                        cur[i] = item
                else:
                    while len(cur) < i:
                        cur.append({})
                    cur.append(item)
        elif isinstance(v, dict):
            base[k] = {}
            merge(base[k], v)
        else:
            base[k] = v
    return base


@dataclass
class Lap:
    lap: int
    seconds: float | None
    track_status: str
    clean: bool  # senza SC/VSC/gialla e senza ingresso/uscita box
    pit: bool
    ts: float = 0.0
    gap_s: float | None = None  # gap dal leader a fine giro
    position: int | None = None


class RaceState:
    def __init__(self):
        self.reset()

    def reset(self):
        self.data: dict = {}
        self.laps: dict[str, list[Lap]] = defaultdict(list)
        self.telemetry: dict[str, deque] = defaultdict(lambda: deque(maxlen=TELEMETRY_KEEP))
        self.positions: dict[str, dict] = {}
        self._pending_car: list = []
        self._pending_pos: list = []
        self._status_log: list[tuple[float, str]] = []  # (ts, codice stato pista)
        self._pit_flag: dict[str, bool] = defaultdict(bool)
        self._lap_start: dict[str, float] = {}
        self.replay_position: float | None = None
        self.circuit_info: dict = {}  # da MultiViewer: pit_loss {normal, sc, vsc}, tracciato, curve
        self.last_ts: float = 0.0     # istante dell'ultimo messaggio, nel tempo del feed
        self._clock_at: float | None = None  # quando è arrivato l'ultimo ExtrapolatedClock
        self._pit_lane: dict[str, list[dict]] = defaultdict(list)  # passaggi in pit lane, anche quelli già cancellati dalla F1

    # ------------------------------------------------------------------ ingresso

    def apply(self, topic: str, data, ts: float):
        self.last_ts = ts
        if topic == "__snapshot__":
            # SessionInfo per primo: se cambia sessione azzera lo stato, e non deve cancellare
            # quello che è appena arrivato nello stesso snapshot
            for t, d in sorted(data.items(), key=lambda kv: kv[0] != "SessionInfo"):
                self.apply(t, d, ts)
            return
        if topic == "ExtrapolatedClock":
            self._clock_at = ts
        if topic == "SessionInfo":
            new_path = (data or {}).get("Path")
            old_path = self.data.get("SessionInfo", {}).get("Path")
            if new_path and old_path and new_path != old_path:
                self.reset()
        if topic == "CarData.z":
            self._apply_car(data, ts)
            return
        if topic == "Position.z":
            self._apply_pos(data, ts)
            return
        if topic == "TrackStatus" and isinstance(data, dict) and "Status" in data:
            self._status_log.append((ts, str(data["Status"])))
        if topic == "TimingData":
            self._track_laps(data, ts)
        if topic == "PitLaneTimeCollection":
            self._record_pit_lane(data)
        if isinstance(data, dict):
            merge(self.data.setdefault(topic, {}), data)
        else:
            self.data[topic] = data

    def _status_at(self, ts: float) -> str:
        cur = "1"
        for t, s in self._status_log:
            if t <= ts:
                cur = s
            else:
                break
        return cur

    def _clean_between(self, t0: float, t1: float) -> bool:
        if self._status_at(t0) != "1":
            return False
        return all(s == "1" for t, s in self._status_log if t0 < t <= t1)

    def _track_laps(self, delta: dict, ts: float):
        """Registra un giro quando NumberOfLaps aumenta; segna i giri con box o bandiere."""
        lines = delta.get("Lines") or {}
        for num, d in lines.items():
            if not isinstance(d, dict):
                continue
            if d.get("InPit") or d.get("PitOut"):
                self._pit_flag[num] = True
            # sosta vista solo dal contatore (per esempio dopo un buco di rete: lo snapshot arriva a pilota
            # già uscito, senza InPit/PitOut). Senza, il degrado mescola due stint (a Monza -1,33 invece di -0,07)
            prev_line = self.data.get("TimingData", {}).get("Lines", {}).get(num) or {}
            if (d.get("NumberOfPitStops") or 0) > (prev_line.get("NumberOfPitStops") or 0):
                self._pit_flag[num] = True
            n = d.get("NumberOfLaps")
            if n is None:
                continue
            prev = (self.data.get("TimingData", {}).get("Lines", {}).get(num) or {}).get("NumberOfLaps", 0)
            if n <= prev:
                continue
            last = d.get("LastLapTime", {}).get("Value") if isinstance(d.get("LastLapTime"), dict) else None
            if last is None:
                cur = self.data.get("TimingData", {}).get("Lines", {}).get(num, {})
                last = (cur.get("LastLapTime") or {}).get("Value")
            secs = strategy.lap_time_seconds(last)
            t0 = self._lap_start.get(num, ts)
            status = self._status_at(ts)
            pit = self._pit_flag[num]
            clean = (not pit) and secs is not None and self._clean_between(t0, ts)
            cur_line = self.data.get("TimingData", {}).get("Lines", {}).get(num, {})
            gap_txt = d.get("GapToLeader", cur_line.get("GapToLeader"))
            try:
                position = int(d.get("Position") or cur_line.get("Position") or 0) or None
            except ValueError:
                position = None
            gap_s = strategy.gap_seconds(gap_txt)
            if gap_s is None and position == 1:
                gap_s = 0.0
            self.laps[num].append(Lap(lap=n, seconds=secs, track_status=status, clean=clean, pit=pit, ts=ts,
                                      gap_s=gap_s, position=position))
            # PitOut nello stesso messaggio del giro chiuso: il giro che comincia ora è l'uscita dai box
            # (nelle libere di Baku 62 volte in FP2: giri da 7 minuti risultavano "puliti")
            self._pit_flag[num] = bool(d.get("PitOut"))
            self._lap_start[num] = ts

    def _apply_car(self, data, ts: float):
        for e in (data or {}).get("Entries") or []:
            for num, car in (e.get("Cars") or {}).items():
                ch = car.get("Channels") or {}
                sample = {"t": ts, **{name: ch.get(code) for code, name in CH.items()}}
                self.telemetry[num].append(sample)
                self._pending_car.append({"n": num, **sample})

    def _apply_pos(self, data, ts: float):
        for p in (data or {}).get("Position") or []:
            for num, e in (p.get("Entries") or {}).items():
                pos = {"t": ts, "x": e.get("X"), "y": e.get("Y"), "status": e.get("Status")}
                self.positions[num] = pos
                self._pending_pos.append({"n": num, **pos})

    def drain_telemetry(self) -> tuple[list, list]:
        car, pos = self._pending_car, self._pending_pos
        self._pending_car, self._pending_pos = [], []
        return car, pos

    # ------------------------------------------------------------------ viste

    @property
    def drivers(self) -> dict:
        return {k: v for k, v in self.data.get("DriverList", {}).items() if isinstance(v, dict)}

    def _stints(self, num: str) -> list[dict]:
        st = (self.data.get("TimingAppData", {}).get("Lines", {}).get(num) or {}).get("Stints") or []
        if isinstance(st, dict):
            st = [st[k] for k in sorted(st, key=int)]
        out = []
        for s in st:
            if not isinstance(s, dict):
                continue
            out.append({
                "compound": (s.get("Compound") or "UNKNOWN").upper(),
                "laps": s.get("TotalLaps", 0),
                "start_laps": s.get("StartLaps", 0),
                "new": str(s.get("New", "true")).lower() == "true",
            })
        return out

    def _current_stint_laps(self, num: str) -> list[tuple[int, float, bool]]:
        """Giri dello stint corrente come (giro, secondi, pulito) per il degrado."""
        hist = self.laps.get(num, [])
        # lo stint corrente inizia dopo l'ultimo giro con box
        start = 0
        for i, l in enumerate(hist):
            if l.pit:
                start = i + 1
        return [(l.lap, l.seconds, l.clean) for l in hist[start:] if l.seconds]

    def observed_pit_losses(self) -> list[float]:
        """Tutti i passaggi in pit lane visti nella sessione (la F1 li cancella poco dopo)."""
        return [p["seconds"] for passes in self._pit_lane.values() for p in passes]

    @staticmethod
    def _hms_to_s(v) -> float | None:
        try:
            h, m, s = (float(x) for x in str(v).split(":"))
        except ValueError:
            return None
        return h * 3600 + m * 60 + s

    @staticmethod
    def _s_to_hms(s: float) -> str:
        s = max(0, int(s))
        return f"{s // 3600:02d}:{s // 60 % 60:02d}:{s % 60:02d}"

    def remaining(self) -> str:
        """Tempo che resta. La F1 lo manda una volta sola con ``Extrapolating``:
        da lì in poi il conto alla rovescia tocca a noi, altrimenti resta fermo."""
        clock = self.data.get("ExtrapolatedClock", {})
        left = clock.get("Remaining", "")
        if not clock.get("Extrapolating") or self._clock_at is None:
            return left
        base = self._hms_to_s(left)
        if base is None:
            return left
        return self._s_to_hms(base - (self.last_ts - self._clock_at))

    def pit_times(self) -> dict[str, dict]:
        """Ultimo passaggio in pit lane per pilota: quello vero della sessione, non la stima.
        Esclusi quelli da bandiera rossa (ore ferme in pit lane, non un pit stop)."""
        out = {}
        for num, passes in self._pit_lane.items():
            real = [p for p in passes if p["seconds"] < 120]
            if real:
                out[num] = {"duration": real[-1]["duration"], "lap": real[-1]["lap"]}
        return out

    def _record_pit_lane(self, data):
        """PitLaneTimeCollection: la F1 manda ogni passaggio e lo cancella poco dopo (_deleted).
        Si tiene qui, altrimenti a metà gara non resterebbe niente da mostrare."""
        for num, v in ((data or {}).get("PitTimes") or {}).items():
            if num == "_deleted" or not isinstance(v, dict) or not v.get("Duration"):
                continue
            secs = strategy.lap_time_seconds(str(v["Duration"]))
            passes = self._pit_lane[str(num)]
            if secs and not any(p["lap"] == v.get("Lap") and p["duration"] == v["Duration"] for p in passes):
                passes.append({"duration": v["Duration"], "lap": v.get("Lap"), "seconds": secs})

    @staticmethod
    def _items(v) -> list:
        """Liste del feed: arrivano come lista o come dict-indice {"0": ..., "1": ...}."""
        if isinstance(v, dict):
            return [v[k] for k in sorted(v, key=lambda k: int(k) if str(k).isdigit() else 0)]
        return v if isinstance(v, list) else []

    def _qualifying(self, rows: list[dict], lines: dict, part: int) -> dict:
        """Qualifica: taglio della parte in corso, margine di ognuno e previsione del giro lanciato.

        - il taglio si legge da TimingData.NoEntries (a Monza 2026 [22, 16, 10]: in Q1 passano 16,
          in Q2 10). Prima era scritto a mano a 15 e il 16° risultava eliminato;
        - "cut_gap": per chi è dentro il margine sul primo eliminato, per chi è fuori quanto gli
          manca per il tempo dell'ultimo che passa;
        - "flying": appena un pilota chiude S1 (o S1+S2) di un giro nuovo la F1 svuota i settori
          dopo; tempo previsto = settori fatti + suoi migliori settori per il resto. Solo per giri
          veloci (entro 1,5 s dal migliore della parte) e scritta come stima.
        """
        no_entries = self.data.get("TimingData", {}).get("NoEntries") or []
        cut = no_entries[part] if part < len(no_entries) else None

        def part_best(num: str) -> float | None:
            blt = (lines.get(num) or {}).get("BestLapTimes") if isinstance(lines.get(num), dict) else None
            items = self._items(blt)
            if part - 1 < len(items):
                return strategy.lap_time_seconds((items[part - 1] or {}).get("Value"))
            return None

        times = {r["num"]: part_best(r["num"]) for r in rows}
        ranked = [r for r in rows if times.get(r["num"])]
        ranked.sort(key=lambda r: times[r["num"]])
        fastest = times[ranked[0]["num"]] if ranked else None
        out: dict = {"cut": cut}
        cut_time = first_out = None
        if cut and len(ranked) >= cut:
            cut_time = times[ranked[cut - 1]["num"]]
            out.update(cut_time=strategy.format_lap(cut_time), cut_tla=ranked[cut - 1]["tla"])
            if len(ranked) > cut:
                first_out = times[ranked[cut]["num"]]
        for r in rows:
            mine = times.get(r["num"])
            r["part_best"] = strategy.format_lap(mine) if mine else ""
            r["cut_gap"] = None
            if cut and mine and cut_time:
                if r["pos"] <= cut and first_out:
                    r["cut_gap"] = round(first_out - mine, 3)   # dentro: margine sul primo eliminato
                elif r["pos"] > cut:
                    r["cut_gap"] = round(cut_time - mine, 3)    # fuori: negativo, quanto gli manca
            r["flying"] = None
            vals = [strategy.lap_time_seconds(sx.get("v")) for sx in r["sectors"]]
            best = [strategy.lap_time_seconds(b.get("v")) for b in r["bests"]["sectors"]]
            if len(vals) != 3 or len(best) != 3 or r["inpit"] or r["retired"] or r["knocked_out"]:
                continue
            done = 2 if vals[0] and vals[1] and not vals[2] else 1 if vals[0] and not vals[1] else 0
            if not done or None in best[done:]:
                continue
            pred = sum(vals[:done]) + sum(best[done:])
            # niente previsioni sui giri di lancio: serve un tempo di riferimento nella parte, un
            # tempo previsto entro 1,5 s dal migliore e settori fatti non oltre 1 s dai suoi migliori
            if not fastest or pred > fastest + 1.5 or sum(vals[:done]) - sum(best[:done]) > 1.0:
                continue
            others = [t for n, t in times.items() if t and n != r["num"]]
            pos = 1 + sum(1 for t in others if t < min(pred, mine or pred))
            verdict = None
            if cut and cut_time:
                target = cut_time if (r["pos"] > cut or not first_out) else first_out
                verdict = "si salva" if pred < target - 0.15 else "non basta" if pred > target + 0.15 else "in bilico"
            r["flying"] = {"after": done, "pred": strategy.format_lap(pred), "pred_s": round(pred, 3), "pos": pos, "verdict": verdict}
        return out

    def pit_stops(self) -> dict[str, list[dict]]:
        """Soste di gara con il tempo da fermo (PitStopSeries): solo in gara."""
        out = {}
        for num, stops in (self.data.get("PitStopSeries", {}).get("PitTimes") or {}).items():
            rows = []
            for s in self._items(stops):
                p = (s or {}).get("PitStop") or {}
                if p:
                    rows.append({"lap": p.get("Lap"), "stop": p.get("PitStopTime", ""), "lane": p.get("PitLaneTime", "")})
            out[str(num)] = rows
        return out

    def overtakes(self) -> dict[str, int]:
        """Sorpassi in pista (OvertakeSeries), doppiaggi compresi: la F1 li conta allo stesso modo.

        Il campo "count" vale 1 (a volte 2) per un sorpasso, ma per alcuni piloti dopo ogni
        sorpasso arriva una voce con count 21 (a Monza Russell: 1, 21, 1, 21...): non è un
        sorpasso, si scarta. Verificato a Monza che dopo un evento la posizione migliora nel 74%
        dei casi e peggiora nel 7%."""
        out = {}
        for num, evs in (self.data.get("OvertakeSeries", {}).get("Overtakes") or {}).items():
            counts = [int((e or {}).get("count") or 0) for e in self._items(evs)]
            out[str(num)] = sum(c for c in counts if 0 < c <= 3)
        return out

    def _lap_positions(self, entry) -> list[int]:
        """Posizione a ogni giro (LapSeries): arriva anche nelle libere."""
        out = []
        for p in self._items((entry or {}).get("LapPosition")):
            try:
                out.append(int(p))
            except (TypeError, ValueError):
                out.append(None)
        return out

    def _bests(self, stat: dict, best_secs: list, best_lap: str) -> dict:
        """Migliori settori e velocità con la posizione in classifica, e il giro teorico."""
        sectors = [{"v": (b or {}).get("Value", ""), "pos": (b or {}).get("Position")} for b in best_secs]
        speeds = {k.lower(): {"v": (v or {}).get("Value", ""), "pos": (v or {}).get("Position")}
                  for k, v in (stat.get("BestSpeeds") or {}).items() if isinstance(v, dict)}
        return {"sectors": sectors, "speeds": speeds,
                "theoretical": strategy.theoretical_best([s["v"] for s in sectors], best_lap)}

    def championship(self) -> dict:
        """Campionato "se finisse adesso" (ChampionshipPrediction): solo in gara."""
        cp = self.data.get("ChampionshipPrediction") or {}

        def rows(d: dict, name_key: str) -> list[dict]:
            out = []
            for key, v in (d or {}).items():
                if not isinstance(v, dict) or v.get("PredictedPoints") is None:
                    continue
                out.append({"key": str(v.get(name_key) or key), "pos": v.get("CurrentPosition"),
                            "pred_pos": v.get("PredictedPosition"), "points": v.get("CurrentPoints"),
                            "pred_points": v.get("PredictedPoints")})
            return sorted(out, key=lambda r: (r["pred_pos"] or 99, -(r["pred_points"] or 0)))

        return {"drivers": rows(cp.get("Drivers"), "RacingNumber"), "teams": rows(cp.get("Teams"), "TeamName")}

    @staticmethod
    def _speeds(line: dict) -> dict:
        """Le quattro rilevazioni di velocità: due intermedie, trappola e traguardo."""
        sp = line.get("Speeds") or {}
        out = {}
        for k in ("I1", "I2", "ST", "FL"):
            v = sp.get(k) if isinstance(sp, dict) else None
            v = v if isinstance(v, dict) else {}
            out[k.lower()] = {"v": v.get("Value", ""), "pf": bool(v.get("PersonalFastest")), "of": bool(v.get("OverallFastest"))}
        return out

    def _sectors(self, line: dict) -> list[dict]:
        out = []
        secs = line.get("Sectors") or []
        if isinstance(secs, dict):
            secs = [secs.get(str(i), {}) for i in range(3)]
        for s in secs:
            if not isinstance(s, dict):
                continue
            segs = s.get("Segments") or []
            if isinstance(segs, dict):
                segs = [segs[k] for k in sorted(segs, key=int)]
            out.append({
                "v": s.get("Value", ""),
                "pf": bool(s.get("PersonalFastest")),
                "of": bool(s.get("OverallFastest")),
                "seg": [x.get("Status", 0) if isinstance(x, dict) else 0 for x in segs],
            })
        return out

    def snapshot(self) -> dict:
        info = self.data.get("SessionInfo", {})
        lines = self.data.get("TimingData", {}).get("Lines", {})
        circuit = ((info.get("Meeting") or {}).get("Circuit") or {}).get("ShortName")
        pit_losses = self.observed_pit_losses()
        observed = strategy.pit_loss_for(circuit, pit_losses) if pit_losses else None
        mv = self.circuit_info.get("pit_loss") or {}
        loss_normal = mv.get("normal") or strategy.pit_loss_for(circuit, [])
        loss_sc = mv.get("sc") or round(loss_normal * 0.63, 1)
        loss_vsc = mv.get("vsc") or round(loss_normal * 0.72, 1)
        ts_data = self.data.get("TrackStatus", {})
        track = TRACK_STATUS.get(str(ts_data.get("Status", "1")), "green")
        # gara sospesa: un minuto dopo la rossa la F1 manda "AllClear" (a Monza a 3770 s, con la gara
        # ferma fino a 5545 s) e la pagina tornava VERDE. Conta SessionStatus
        if self.data.get("SessionStatus", {}).get("Status") == "Aborted":
            track = "red"
        pit_loss = loss_sc if track == "sc" else loss_vsc if track in ("vsc", "vsc_ending") else loss_normal
        session_type = info.get("Type", "")
        part = self.data.get("TimingData", {}).get("SessionPart")
        stats = self.data.get("TimingStats", {}).get("Lines", {})
        pit_times = self.pit_times()
        pit_stops = self.pit_stops()
        overtakes = self.overtakes()
        lap_series = self.data.get("LapSeries") or {}
        rc_all = self.data.get("RaceControlMessages", {}).get("Messages") or []
        if isinstance(rc_all, dict):
            rc_all = [rc_all[k] for k in sorted(rc_all, key=int)]
        steward = stewards.analyse([m for m in rc_all if isinstance(m, dict)])

        rows = []
        for num, drv in self.drivers.items():
            line = lines.get(num, {}) if isinstance(lines.get(num), dict) else {}
            stints = self._stints(num)
            cur = stints[-1] if stints else {}
            last = line.get("LastLapTime") or {}
            best = line.get("BestLapTime") or {}
            try:
                pos = int(line.get("Position") or drv.get("Line") or 99)
            except ValueError:
                pos = 99
            # qualifica/prove: i distacchi stanno in Stats[parte] o TimeDiffToFastest
            gap_txt, int_txt = line.get("GapToLeader"), (line.get("IntervalToPositionAhead") or {}).get("Value", "")
            st_list = line.get("Stats")
            if isinstance(st_list, dict):
                st_list = [st_list.get(str(i), {}) for i in range(len(st_list))]
            if session_type != "Race":
                if st_list and part:
                    stp = st_list[min(part, len(st_list)) - 1] or {}
                    gap_txt, int_txt = stp.get("TimeDiffToFastest", ""), stp.get("TimeDifftoPositionAhead", "")
                elif line.get("TimeDiffToFastest") is not None:
                    gap_txt, int_txt = line.get("TimeDiffToFastest", ""), line.get("TimeDiffToPositionAhead", "")
            app_line = self.data.get("TimingAppData", {}).get("Lines", {}).get(num) or {}
            try:
                grid = int(app_line.get("GridPos") or 0) or None
            except ValueError:
                grid = None
            car = self.telemetry[num][-1] if self.telemetry.get(num) else {}
            best_secs = (stats.get(num) or {}).get("BestSectors") or []
            if isinstance(best_secs, dict):
                best_secs = [best_secs.get(str(i), {}) for i in range(3)]
            rows.append({
                "num": num,
                "tla": drv.get("Tla", num),
                "name": drv.get("BroadcastName") or drv.get("FullName", ""),
                "team": drv.get("TeamName", ""),
                "colour": "#" + drv.get("TeamColour", "888888"),
                "pos": pos,
                "gap": gap_txt or "",
                "gap_s": 0.0 if pos == 1 and not line.get("GapToLeader") else strategy.gap_seconds(line.get("GapToLeader")),
                "interval": int_txt or "",
                "interval_s": strategy.gap_seconds(int_txt) if int_txt else None,
                "catching": bool((line.get("IntervalToPositionAhead") or {}).get("Catching")),
                "last": last.get("Value", ""),
                "last_pf": bool(last.get("PersonalFastest")),
                "last_of": bool(last.get("OverallFastest")),
                "best": best.get("Value", ""),
                "sectors": self._sectors(line),
                "compound": cur.get("compound", "UNKNOWN"),
                "age": cur.get("laps", 0),
                "new": cur.get("new", True),
                "stops": line.get("NumberOfPitStops", 0),
                # soste fatte a gara sospesa (cambio gomme gratis): la F1 le conta nelle soste. Si riconoscono
                # dal passaggio in pit lane lungo quanto la sospensione (a Monza ~1840 s); verificato su 21 piloti su 22
                "free_stops": sum(1 for p in self._pit_lane.get(num, []) if p["seconds"] > 300),
                "laps": line.get("NumberOfLaps", 0),
                "inpit": bool(line.get("InPit")),
                "pitout": bool(line.get("PitOut")),
                "retired": bool(line.get("Retired")),
                "stopped": bool(line.get("Stopped")),
                "speed_trap": ((line.get("Speeds") or {}).get("ST") or {}).get("Value", ""),
                "speeds": self._speeds(line),
                "best_speeds": {k.lower(): (v or {}).get("Value", "") for k, v in ((stats.get(num) or {}).get("BestSpeeds") or {}).items() if isinstance(v, dict)},
                "pit_time": pit_times.get(num, {}),
                "stints": stints,
                "grid": grid,
                "gained": (grid - pos) if grid and pos < 99 else None,
                "knocked_out": bool(line.get("KnockedOut")),
                "car": {k: car.get(k) for k in ("speed", "gear", "throttle", "brake", "rpm", "drs")} if car else None,
                "best_sector_pos": [(b or {}).get("Position") for b in best_secs] if best_secs else [],
                "lap_history": [[l.lap, l.seconds, l.gap_s, l.position, l.clean, l.pit] for l in self.laps.get(num, [])],
                "bests": self._bests(stats.get(num) or {}, best_secs, best.get("Value", "")),
                "pit_stops": pit_stops.get(num, []),
                "overtakes": overtakes.get(num, 0),
                "compounds": strategy.compound_rule([st["compound"] for st in stints]),
                "stewards": steward["drivers"].get(num, {"track_limits": 0, "penalties": [], "open": 0}),
                "lap_positions": self._lap_positions(lap_series.get(num)),
            })
        rows.sort(key=lambda r: r["pos"])
        lc = self.data.get("LapCount", {})
        quali = self._qualifying(rows, lines, part) if part else {}
        if session_type == "Race":
            strategy.fill_lapped_gaps(rows)  # i doppiati hanno "1 L": il distacco si ricava dagli intervalli

        # strategia per tutti (il browser sceglie il team a fuoco)
        field = [(r["num"], None if r["retired"] or r["stopped"] else r["gap_s"]) for r in rows]
        by_pos = {r["pos"]: r for r in rows}
        racing = session_type == "Race" and track in ("green", "yellow")  # con SC/VSC/rossa le previsioni non valgono
        total_laps = lc.get("TotalLaps")
        for r in rows:
            r["exit"] = None if r["retired"] else strategy.pit_exit(r["num"], field, pit_loss)
            # battaglia con chi sta davanti: tendenza dell'intervallo, "lo prende tra N giri", "bloccato in scia"
            ahead = by_pos.get(r["pos"] - 1)
            r["ahead_trend"] = r["chase"] = r["stuck"] = None
            if session_type == "Race" and ahead and not r["retired"] and not r["stopped"]:
                fh, bh = ahead["lap_history"], r["lap_history"]
                r["ahead_trend"] = strategy.interval_trend(fh, bh)
                if racing:
                    r["chase"] = strategy.catch_forecast(fh, bh, r.get("interval_s"), total_laps)
                    if r["chase"]:
                        r["chase"]["on"] = ahead["tla"]
                    n = strategy.stuck_laps(fh, bh)
                    if n:
                        r["stuck"] = {"behind": ahead["tla"], "laps": n}
            r["deg"] = strategy.degradation(self._current_stint_laps(r["num"])[-10:])
            behind = by_pos.get(r["pos"] + 1)
            r["undercut"] = None
            if behind and not r["retired"]:
                iv = strategy.gap_seconds(behind["interval"]) if behind["interval"] else None
                r["undercut"] = strategy.undercut_threat(iv, r["age"], behind["age"])
                if r["undercut"]:
                    r["undercut"]["by"] = behind["tla"]

        radio = []
        caps = self.data.get("TeamRadio", {}).get("Captures") or []
        if isinstance(caps, dict):
            caps = [caps[k] for k in sorted(caps, key=int)]
        for c in caps:
            if isinstance(c, dict) and c.get("Path"):
                num = str(c.get("RacingNumber", ""))
                rel = info.get("Path", "") + c["Path"]
                radio.append({"utc": c.get("Utc", ""), "num": num, "tla": self.drivers.get(num, {}).get("Tla", num),
                              "url": STATIC_BASE + rel, "path": rel})
        rc = self.data.get("RaceControlMessages", {}).get("Messages") or []
        if isinstance(rc, dict):
            rc = [rc[k] for k in sorted(rc, key=int)]
        rc = [m for m in rc if isinstance(m, dict)][-60:]

        w = self.data.get("WeatherData", {})
        clock = self.data.get("ExtrapolatedClock", {})
        return {
            "session": {
                "meeting": (info.get("Meeting") or {}).get("Name", ""),
                "name": info.get("Name", ""),
                "circuit": circuit,
                "type": session_type,
                "part": part,
                **quali,  # taglio della parte in corso (qualifica): cut, cut_time, cut_tla
                "path": info.get("Path", ""),
                "status": self.data.get("SessionStatus", {}).get("Status", ""),
                "track": track,
                "track_msg": ts_data.get("Message", ""),
                "lap": lc.get("CurrentLap"),
                "total_laps": lc.get("TotalLaps"),
                "remaining": self.remaining(),
                "extrapolating": bool(clock.get("Extrapolating")),
                "replay_position": self.replay_position,
                "map_rev": self.circuit_info.get("rev", 0),  # cambia quando arriva la calibrazione
            },
            "weather": {
                "air": w.get("AirTemp"), "track": w.get("TrackTemp"), "humidity": w.get("Humidity"),
                "rain": w.get("Rainfall"), "wind": w.get("WindSpeed"), "wind_dir": w.get("WindDirection"),
                "pressure": w.get("Pressure"),
            },
            "pit_loss": {"value": pit_loss, "normal": loss_normal, "sc": loss_sc, "vsc": loss_vsc,
                         "source": "MultiViewer" if mv else "tabella",
                         "observed_median": observed, "observed_n": len(pit_losses), "circuit": circuit},
            "drivers": rows,
            "radio": radio[-40:],
            "race_control": rc,
            "trains": strategy.trains(rows) if session_type == "Race" else [],  # gruppi di 3+ entro 1 s
            "race_control_total": len([m for m in rc_all if isinstance(m, dict)]),
            "incidents": steward["incidents"][-30:],
            "championship": self.championship(),
        }
