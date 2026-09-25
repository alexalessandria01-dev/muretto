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
    assert gap_seconds("") is None  # vuoto: nessun dato, non "leader"
    assert gap_seconds(None) is None
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


def test_degradation_on_clean_laps_with_fuel_correction():
    laps = [(10, 85.0, True), (11, 85.1, True), (12, 85.2, True), (13, 95.0, False), (14, 85.4, True)]
    d = degradation(laps)
    assert abs(d["raw_slope"] - 0.1) < 1e-6
    assert abs(d["slope"] - (0.1 + d["fuel"])) < 1e-6  # al netto della benzina
    assert d["laps"] == 4
    assert d["last_clean"] == 85.4


def test_degradation_ignores_the_restart_lap_after_a_red_flag():
    # Monza 2026: il giro di ripartenza (~200 s) risultava pulito e dava -40 s/giro
    laps = [(5, 199.0, True), (6, 87.2, True), (7, 87.3, True), (8, 87.3, True), (9, 87.4, True)]
    d = degradation(laps)
    assert d["laps"] == 4 and -0.01 < d["raw_slope"] < 0.2


def test_degradation_needs_four_laps():
    assert degradation([(1, 85.0, True), (2, 85.1, True), (3, 85.2, True)]) is None


def test_undercut_threat_levels():
    assert undercut_threat(interval_behind=0.6, my_tyre_age=20, their_tyre_age=5)["risk"] == "alto"
    assert undercut_threat(interval_behind=1.3, my_tyre_age=20, their_tyre_age=5)["risk"] == "medio"
    far = undercut_threat(interval_behind=23.0, my_tyre_age=20, their_tyre_age=5)
    assert far["risk"] == "basso" and far["window"] is False  # prima risultava "in finestra"
    assert undercut_threat(interval_behind=None, my_tyre_age=1, their_tyre_age=1) is None


def test_compound_rule_needs_two_dry_compounds():
    from muretto.strategy import compound_rule
    assert compound_rule(["MEDIUM", "MEDIUM"]) == {"ok": False, "used": ["MEDIUM"], "wet": False}
    assert compound_rule(["MEDIUM", "HARD"])["ok"]


def test_compound_rule_lifted_in_the_wet():
    from muretto.strategy import compound_rule
    r = compound_rule(["INTERMEDIATE", "MEDIUM"])
    assert r["ok"] and r["wet"] and r["used"] == ["MEDIUM"]


def test_theoretical_best_sums_best_sectors():
    from muretto.strategy import theoretical_best
    r = theoretical_best(["27.225", "28.686", "27.593"], "1:23.504")
    assert r["time"] == "1:23.504" and r["margin"] == 0.0
    r = theoretical_best(["27.225", "28.586", "27.593"], "1:23.504")
    assert r["time"] == "1:23.404" and r["margin"] == 0.1


def test_theoretical_best_needs_all_three_sectors():
    from muretto.strategy import theoretical_best
    assert theoretical_best(["27.225", "", "27.593"], "1:23.504") is None


def test_fill_lapped_gaps_sums_intervals():
    from muretto.strategy import fill_lapped_gaps
    rows = [{"gap_s": 0.0, "interval": ""}, {"gap_s": 78.958, "interval": "+3.349"},
            {"gap_s": None, "interval": "+14.791"}, {"gap_s": None, "interval": "+13.677"},
            {"gap_s": None, "interval": "1L"}]  # l'ultimo è doppiato anche rispetto a chi ha davanti
    fill_lapped_gaps(rows)
    assert rows[2]["gap_s"] == 93.749 and rows[3]["gap_s"] == 107.426
    assert rows[4]["gap_s"] is None


def test_fill_lapped_gaps_skips_retired():
    from muretto.strategy import fill_lapped_gaps
    rows = [{"gap_s": 0.0, "interval": ""}, {"gap_s": None, "interval": "+5.0", "retired": True}]
    fill_lapped_gaps(rows)
    assert rows[1]["gap_s"] is None


def _hist(gaps, pos, clean=True, pit=False, start=10):
    """Storia giri come nello snapshot: [giro, secondi, gap_s, posizione, pulito, box]."""
    return [[start + i, 90.0, g, pos, clean, pit] for i, g in enumerate(gaps)]


def test_catch_forecast_closing_car():
    from muretto.strategy import catch_forecast
    front = _hist([10.0, 10.0, 10.0, 10.0], pos=3)
    back = _hist([13.1, 12.4, 11.7, 11.0], pos=4)  # recupera 0,7 s/giro, è a 1,0... poi 0,3 s in più
    f = catch_forecast(front, back, interval_now=1.7, total_laps=53)
    assert f["rate"] == 0.7 and f["lap"] == 14 and f["in_time"] is True  # ultimo giro 13, 1 giro per scendere sotto 1 s


def test_catch_forecast_needs_four_clean_laps_and_real_closing():
    from muretto.strategy import catch_forecast
    front = _hist([10.0] * 4, pos=3)
    assert catch_forecast(front, _hist([13.0, 12.9, 12.85, 12.8], pos=4), None, 53) is None   # 0,07 s/giro: rumore
    assert catch_forecast(front, _hist([13.1, 12.4, 11.7, 11.0], pos=4, clean=False), None, 53) is None
    assert catch_forecast(front, _hist([27.0, 26.0, 25.0, 24.0], pos=4), None, 53) is None    # 14 s a 1 s/giro: 13 giri, oltre 8


def test_catch_forecast_after_the_end_and_unknown_total():
    from muretto.strategy import catch_forecast
    front = _hist([10.0] * 4, pos=3, start=48)
    back = _hist([14.0, 13.5, 13.0, 12.5], pos=4, start=48)
    assert catch_forecast(front, back, None, 53)["in_time"] is False  # servono ~6 giri, la gara finisce prima
    assert catch_forecast(front, back, None, 0)["in_time"] is None      # TotalLaps a 0: non si dice


def test_catch_forecast_ignores_lapped_cars():
    from muretto.strategy import catch_forecast
    front = _hist([10.0] * 4, pos=3)
    back = [[10 + i, 90.0, None, 4, True, False] for i in range(4)]
    assert catch_forecast(front, back, None, 53) is None


def test_stuck_in_wake():
    from muretto.strategy import stuck_laps
    front = _hist([10.0] * 6, pos=3)
    assert stuck_laps(front, _hist([10.8, 10.7, 10.9, 10.6, 10.8, 10.7], pos=4)) == 6
    assert stuck_laps(front, _hist([10.8, 10.7, 10.9], pos=4)) is None            # meno di 4 giri
    assert stuck_laps(front, _hist([12.5, 12.4, 12.6, 12.5, 12.4, 12.6], pos=4)) is None  # troppo lontano


def test_trains():
    from muretto.strategy import trains
    rows = [{"num": "1", "interval_s": None}, {"num": "2", "interval_s": 0.6}, {"num": "3", "interval_s": 0.9},
            {"num": "4", "interval_s": 2.5}, {"num": "5", "interval_s": 0.5}, {"num": "6", "interval_s": 0.4, "inpit": True}]
    assert trains(rows) == [["1", "2", "3"]]  # 4-5 solo in due, e il 6 è ai box
