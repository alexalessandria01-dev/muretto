import math

from muretto.compare import Session, _interp, utc_seconds


def test_utc_seconds_reads_f1_seven_digit_fractions():
    a = utc_seconds("2026-09-24T11:46:46.6453731Z")
    b = utc_seconds("2026-09-24T11:46:46Z")
    assert math.isclose(a - b, 0.6453731, abs_tol=1e-6)
    assert utc_seconds("non è una data") is None


def test_interp_linear_and_stepped():
    xs, ys = [0, 10, 20], [100, 200, 300]
    assert _interp(xs, ys, 5) == 150
    assert _interp(xs, ys, 5, step=True) == 100
    assert _interp(xs, ys, -1) == 100 and _interp(xs, ys, 99) == 300


def _bare_session(speeds):
    s = Session.__new__(Session)  # senza leggere un archivio: serve solo la telemetria
    s.car = {"1": [(float(i) * 0.27, v, 100, 0, 7, 11000) for i, v in enumerate(speeds)]}
    return s


def test_telemetry_ok_rejects_a_frozen_lap():
    lap = {"end": 400 * 0.27, "seconds": 400 * 0.27}
    assert not _bare_session([184] * 400).telemetry_ok("1", lap)  # Baku FP1: stesso valore per tutto il giro


def test_telemetry_ok_accepts_a_real_lap():
    real = [int(200 + 100 * math.sin(i / 7)) for i in range(400)]
    lap = {"end": 400 * 0.27, "seconds": 400 * 0.27}
    assert _bare_session(real).telemetry_ok("1", lap)



def test_compare_keeps_official_gap_without_track_map():
    # senza tracciato le lunghezze stimate dalla velocità differiscono: il distacco finale
    # deve restare comunque la differenza fra i tempi ufficiali
    from muretto.compare import compare

    class Fake:
        track, corners = None, []
        laps = {"1": [{"time": "1:00.000", "seconds": 60.0, "deleted": False, "sectors": [None] * 3}],
                "2": [{"time": "1:00.500", "seconds": 60.5, "deleted": False, "sectors": [None] * 3}]}

        def best_laps(self):
            return [{"num": n, "tla": n, "colour": "#fff", "lap": 1, "time": l[0]["time"], "cmp_lap": 1,
                     "cmp_time": l[0]["time"]} for n, l in self.laps.items()]

        def sector_marks(self):
            return None

        def lap_trace(self, num, lap):
            L = 5000.0 if num == "1" else 5200.0  # lunghezze diverse, come dalla velocità
            n = 200
            return {"d": [L * i / n for i in range(n + 1)], "t": [lap["seconds"] * i / n for i in range(n + 1)],
                    "speed": [300] * (n + 1), "throttle": [100] * (n + 1), "brake": [0] * (n + 1),
                    "gear": [8] * (n + 1), "rpm": [11000] * (n + 1)}

    r = compare(Fake(), ["1", "2"])
    assert abs(r["drivers"][1]["delta"][-1] - 0.5) < 1e-6
