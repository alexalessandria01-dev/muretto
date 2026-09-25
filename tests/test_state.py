from muretto.state import RaceState, merge


def test_merge_dict_recursive():
    base = {"Lines": {"16": {"Position": "5", "Sectors": [{"Value": "28.1"}]}}}
    merge(base, {"Lines": {"16": {"Position": "4"}}})
    assert base["Lines"]["16"] == {"Position": "4", "Sectors": [{"Value": "28.1"}]}


def test_merge_list_indexed_by_dict_keys():
    base = {"Sectors": [{"Value": "1"}, {"Value": "2"}, {"Value": "3"}]}
    merge(base, {"Sectors": {"1": {"Value": "9"}}})
    assert [s["Value"] for s in base["Sectors"]] == ["1", "9", "3"]


def test_merge_list_extends_when_index_is_new():
    base = {"Captures": [{"Path": "a.mp3"}]}
    merge(base, {"Captures": {"1": {"Path": "b.mp3"}}})
    assert [c["Path"] for c in base["Captures"]] == ["a.mp3", "b.mp3"]


def test_merge_replaces_scalar_and_ignores_kf():
    base = {"Status": "1", "Message": "AllClear"}
    merge(base, {"Status": "4", "Message": "SCDeployed", "_kf": True})
    assert base == {"Status": "4", "Message": "SCDeployed"}


def test_merge_list_into_missing_key_kept_as_list():
    base = {}
    merge(base, {"Messages": [{"Message": "GREEN"}]})
    assert base["Messages"] == [{"Message": "GREEN"}]


def _state_with_driver():
    st = RaceState()
    st.apply("DriverList", {"16": {"Tla": "LEC", "TeamName": "Ferrari", "TeamColour": "E80020", "Line": 1}}, 0)
    st.apply("TrackStatus", {"Status": "1", "Message": "AllClear"}, 0)
    return st


def test_lap_history_records_last_lap_when_lap_count_increments():
    st = _state_with_driver()
    st.apply("TimingData", {"Lines": {"16": {"NumberOfLaps": 1, "LastLapTime": {"Value": "1:25.300"}}}}, 100)
    st.apply("TimingData", {"Lines": {"16": {"NumberOfLaps": 2, "LastLapTime": {"Value": "1:24.900"}}}}, 185)
    laps = st.laps["16"]
    assert [(l.lap, l.seconds) for l in laps] == [(1, 85.3), (2, 84.9)]
    assert laps[-1].track_status == "1"


def test_lap_history_flags_laps_under_safety_car():
    st = _state_with_driver()
    st.apply("TrackStatus", {"Status": "4", "Message": "SCDeployed"}, 50)
    st.apply("TimingData", {"Lines": {"16": {"NumberOfLaps": 3, "LastLapTime": {"Value": "1:50.000"}}}}, 100)
    assert st.laps["16"][-1].track_status == "4"
    assert st.laps["16"][-1].clean is False


def test_car_data_keeps_recent_samples_per_driver():
    st = _state_with_driver()
    st.apply("CarData.z", {"Entries": [{"Utc": "2026-09-06T13:05:01.229Z", "Cars": {"16": {"Channels": {"0": 11516, "2": 330, "3": 8, "4": 100, "5": 0, "45": 12}}}}]}, 10)
    s = st.telemetry["16"][-1]
    assert s == {"t": 10, "rpm": 11516, "speed": 330, "gear": 8, "throttle": 100, "brake": 0, "drs": 12}


def test_snapshot_contains_sorted_leaderboard():
    st = _state_with_driver()
    st.apply("DriverList", {"44": {"Tla": "HAM", "TeamName": "Ferrari", "TeamColour": "E80020", "Line": 2}}, 0)
    st.apply("TimingData", {"Lines": {
        "16": {"Position": "2", "Line": 2, "GapToLeader": "+1.234", "IntervalToPositionAhead": {"Value": "+1.234"}},
        "44": {"Position": "1", "Line": 1, "GapToLeader": "LAP 12", "IntervalToPositionAhead": {"Value": ""}},
    }}, 0)
    snap = st.snapshot()
    assert [d["tla"] for d in snap["drivers"]] == ["HAM", "LEC"]
    assert snap["drivers"][1]["gap"] == "+1.234"


def test_new_session_info_resets_state():
    st = _state_with_driver()
    st.apply("SessionInfo", {"Name": "Race", "Path": "a/"}, 0)
    st.apply("TimingData", {"Lines": {"16": {"Position": "1"}}}, 0)
    st.apply("SessionInfo", {"Name": "Race", "Path": "b/", "Meeting": {"Name": "X"}}, 0)
    assert st.data.get("TimingData") is None
    assert st.data["SessionInfo"]["Path"] == "b/"


def test_merge_honours_deleted():
    from muretto.state import merge
    base = {"PitTimes": {"23": {"Duration": "1846.2"}, "1": {"Duration": "24.5"}}}
    merge(base, {"PitTimes": {"_deleted": ["23"]}})
    assert base == {"PitTimes": {"1": {"Duration": "24.5"}}}


def test_pit_lane_passes_survive_deletion_and_skip_red_flag():
    from muretto.state import RaceState
    st = RaceState()
    st.apply("PitLaneTimeCollection", {"PitTimes": {"1": {"RacingNumber": "1", "Duration": "1840.7", "Lap": "3"}}}, 10.0)
    st.apply("PitLaneTimeCollection", {"PitTimes": {"_deleted": ["1"]}}, 20.0)
    st.apply("PitLaneTimeCollection", {"PitTimes": {"1": {"RacingNumber": "1", "Duration": "24.9", "Lap": "28"}}}, 30.0)
    st.apply("PitLaneTimeCollection", {"PitTimes": {"_deleted": ["1"]}}, 40.0)
    assert st.pit_times() == {"1": {"duration": "24.9", "lap": "28"}}  # la sosta vera, non la rossa
    assert sorted(st.observed_pit_losses()) == [24.9, 1840.7]  # pit_loss_for poi scarta la rossa


def test_snapshot_applies_session_info_first():
    from muretto.state import RaceState
    st = RaceState()
    st.apply("SessionInfo", {"Path": "2026/a/fp1/"}, 0.0)
    # sessione nuova: SessionInfo arriva dopo i piloti nel dizionario, ma va applicata prima
    st.apply("__snapshot__", {"DriverList": {"1": {"Tla": "NOR"}}, "SessionInfo": {"Path": "2026/a/fp2/"}}, 1.0)
    assert "1" in st.data.get("DriverList", {})


def test_red_flag_stays_red_when_f1_sends_all_clear():
    from muretto.state import RaceState
    st = RaceState()
    st.apply("SessionInfo", {"Type": "Race"}, 0.0)
    st.apply("SessionStatus", {"Status": "Aborted"}, 10.0)
    st.apply("TrackStatus", {"Status": "1", "Message": "AllClear"}, 20.0)  # a Monza un minuto dopo la rossa
    assert st.snapshot()["session"]["track"] == "red"
    st.apply("SessionStatus", {"Status": "Started"}, 30.0)
    assert st.snapshot()["session"]["track"] == "green"
