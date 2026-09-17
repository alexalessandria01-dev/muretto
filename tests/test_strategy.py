from muretto.strategy import (
    degradation,
    gap_seconds,
    lap_time_seconds,
    pit_exit,
    pit_loss_for,
    undercut_threat,
)


def test_gap_seconds_parses_formats():
    assert gap_seconds("+12.345") == 12.345
    assert gap_seconds("LAP 12") == 0.0
    assert gap_seconds("") == 0.0
    assert gap_seconds("1L") is None
    assert gap_seconds("+1 LAP") is None


def test_lap_time_seconds():
    assert lap_time_seconds("1:25.300") == 85.3
    assert lap_time_seconds("59.999") == 59.999
    assert lap_time_seconds("") is None


def test_pit_loss_prefers_observed_median_then_table():
    assert pit_loss_for("Monza", [24.9, 24.2, 27.2]) == 24.9
    assert pit_loss_for("Baku", []) == 20.0
    assert pit_loss_for("Sconosciuto", []) == 22.0


def test_pit_exit_position_and_neighbours():
    # gap al leader in secondi; il pilota 44 è 4° a +30, pit loss 24 → rientra a +54
    field = [
        ("12", 0.0), ("63", 5.0), ("3", 20.0), ("1", 40.0), ("81", 50.0),
        ("44", 30.0), ("10", 56.0), ("41", 70.0),
    ]
    r = pit_exit("44", field, pit_loss=24.0)
    assert r["exit_gap"] == 54.0
    assert r["exit_position"] == 6  # dietro a 12, 63, 3, 1, 81 → 6°
    assert r["behind"] == ("81", 4.0)  # esce 4 s dietro Piastri
    assert r["ahead_of"] == ("10", 2.0)  # 2 s davanti a Gasly
    assert r["positions_lost"] == 2


def test_pit_exit_lapped_cars_ignored():
    field = [("12", 0.0), ("44", 10.0), ("77", None)]
    r = pit_exit("44", field, pit_loss=24.0)
    assert r["exit_position"] == 2
    assert r["ahead_of"] is None


def test_degradation_linear_fit_on_clean_laps():
    laps = [(10, 85.0, True), (11, 85.1, True), (12, 85.2, True), (13, 95.0, False), (14, 85.4, True)]
    d = degradation(laps)
    assert abs(d["slope"] - 0.1) < 1e-6
    assert d["laps"] == 4
    assert d["last_clean"] == 85.4


def test_degradation_needs_three_laps():
    assert degradation([(1, 85.0, True), (2, 85.1, True)]) is None


def test_undercut_threat():
    # chi è dietro entro pit_loss + margine e con gomma più fresca minaccia l'undercut
    t = undercut_threat(interval_behind=1.8, pit_loss=24.0, my_tyre_age=20, their_tyre_age=5)
    assert t["window"] is True
    assert t["needs_per_lap"] > 0
