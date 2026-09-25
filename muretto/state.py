"""Stato della sessione: fonde i delta del feed e produce le viste per la pagina.

``RaceState.apply(topic, data, ts)`` è l'unico ingresso, identico per live e
replay. ``snapshot()`` è ciò che il browser riceve ogni mezzo secondo;
``drain_telemetry()`` restituisce i campioni di telemetria/GPS arrivati
dall'ultima chiamata.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass

from . import strategy
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

    # ------------------------------------------------------------------ ingresso

    def apply(self, topic: str, data, ts: float):
        self.last_ts = ts
        if topic == "__snapshot__":
            for t, d in data.items():
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
            self.laps[num].append(Lap(lap=n, seconds=secs, track_status=status, clean=clean, pit=pit, ts=ts,
                                      gap_s=strategy.gap_seconds(gap_txt), position=position))
            self._pit_flag[num] = False
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
        plc = self.data.get("PitLaneTimeCollection", {}).get("PitTimes") or {}
        out = []
        for v in plc.values():
            if isinstance(v, dict):
                secs = strategy.lap_time_seconds(str(v.get("Duration", "")))
                if secs:
                    out.append(secs)
        return out

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
        """Tempo passato in pit lane, per pilota: quello vero della sessione, non la stima."""
        plc = self.data.get("PitLaneTimeCollection", {}).get("PitTimes") or {}
        out = {}
        for num, v in plc.items():
            if isinstance(v, dict) and v.get("Duration"):
                out[str(num)] = {"duration": v.get("Duration", ""), "lap": v.get("Lap")}
        return out

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
        pit_loss = loss_sc if track == "sc" else loss_vsc if track in ("vsc", "vsc_ending") else loss_normal
        session_type = info.get("Type", "")
        part = self.data.get("TimingData", {}).get("SessionPart")
        stats = self.data.get("TimingStats", {}).get("Lines", {})
        pit_times = self.pit_times()

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
                "gap_s": strategy.gap_seconds(line.get("GapToLeader")),
                "interval": int_txt or "",
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
            })
        rows.sort(key=lambda r: r["pos"])

        # strategia per tutti (il browser sceglie il team a fuoco)
        field = [(r["num"], None if r["retired"] or r["stopped"] else r["gap_s"]) for r in rows]
        by_pos = {r["pos"]: r for r in rows}
        for r in rows:
            r["exit"] = None if r["retired"] else strategy.pit_exit(r["num"], field, pit_loss)
            r["deg"] = strategy.degradation(self._current_stint_laps(r["num"])[-10:])
            behind = by_pos.get(r["pos"] + 1)
            r["undercut"] = None
            if behind and not r["retired"]:
                iv = strategy.gap_seconds(behind["interval"]) if behind["interval"] else None
                r["undercut"] = strategy.undercut_threat(iv, pit_loss, r["age"], behind["age"])
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
        lc = self.data.get("LapCount", {})
        clock = self.data.get("ExtrapolatedClock", {})
        return {
            "session": {
                "meeting": (info.get("Meeting") or {}).get("Name", ""),
                "name": info.get("Name", ""),
                "circuit": circuit,
                "type": session_type,
                "part": part,
                "path": info.get("Path", ""),
                "status": self.data.get("SessionStatus", {}).get("Status", ""),
                "track": track,
                "track_msg": ts_data.get("Message", ""),
                "lap": lc.get("CurrentLap"),
                "total_laps": lc.get("TotalLaps"),
                "remaining": self.remaining(),
                "extrapolating": bool(clock.get("Extrapolating")),
                "replay_position": self.replay_position,
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
        }
