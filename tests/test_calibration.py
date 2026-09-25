import math

from muretto.calibration import Track, _circular_median, lap_state
from muretto.state import RaceState


def _sectors(*segs_per_sector):
    return [{"seg": list(s)} for s in segs_per_sector]


def test_lap_state_counts_leading_run():
    assert lap_state(_sectors([2049, 2049, 0], [0, 0])) == (2, 5)


def test_lap_state_ignores_segments_left_from_previous_lap():
    # nuovo giro: acceso il primo, ma il terzo settore è ancora quello del giro prima
    assert lap_state(_sectors([2049, 0], [0, 0], [2051, 2049])) == (1, 6)


def test_lap_state_all_lit_just_after_the_line():
    assert lap_state(_sectors([2049, 2049], [2049])) == (3, 3)


def test_lap_state_without_segments():
    assert lap_state([]) is None


def test_lap_state_matches_state_sectors_format():
    # lap_state deve accettare proprio quello che produce RaceState._sectors
    line = {"Sectors": [{"Segments": [{"Status": 2049}, {"Status": 0}]}, {"Segments": [{"Status": 0}]}]}
    assert lap_state(RaceState()._sectors(line)) == (1, 3)


def _circle(n=360, r=1000.0, clockwise=False):
    """Tracciato circolare; il traguardo (settore commissari 1) a 0°, curva 1 a +20° nel verso di marcia."""
    sign = -1 if clockwise else 1
    xs = [r * math.cos(2 * math.pi * i / n) for i in range(n)]
    ys = [r * math.sin(2 * math.pi * i / n) for i in range(n)]
    c1 = math.radians(20) * sign
    return {
        "x": xs, "y": ys,
        "marshal_sectors": [{"n": 1, "x": r, "y": 0.0}],
        "corners": [{"n": 1, "x": r * math.cos(c1), "y": r * math.sin(c1)}],
    }


def test_track_starts_at_the_line_and_runs_towards_turn_one():
    for clockwise in (False, True):
        t = Track(_circle(clockwise=clockwise))
        x, y = t.point_at(0.0)
        assert math.isclose(x, 1000, abs_tol=1) and math.isclose(y, 0, abs_tol=1)
        # un quarto di giro dopo, nel verso della curva 1
        x, y = t.point_at(0.25)
        assert math.isclose(y, -1000 if clockwise else 1000, abs_tol=20)


def test_track_frac_of_inverts_point_at():
    t = Track(_circle())
    for f in (0.1, 0.5, 0.9):
        assert math.isclose(t.frac_of(*t.point_at(f)), f, abs_tol=0.01)


def test_circular_median_across_the_line():
    assert math.isclose(_circular_median([0.98, 0.99, 0.01, 0.02, 0.0]), 0.0, abs_tol=0.011) \
        or math.isclose(_circular_median([0.98, 0.99, 0.01, 0.02, 0.0]), 1.0, abs_tol=0.011)
