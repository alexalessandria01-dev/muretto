from muretto.stewards import analyse


def _m(text, lap=1):
    return {"Message": text, "Lap": lap, "Category": "Other"}


# messaggi veri, dal Gran Premio d'Italia 2026
LAW_HUL = [
    _m("TURN 4 INCIDENT INVOLVING CARS 30 (LAW) AND 27 (HUL) NOTED (15:53:37)", 14),
    _m("FIA STEWARDS: TURN 4 INCIDENT INVOLVING CARS 30 (LAW) AND 27 (HUL) UNDER INVESTIGATION (15:53:37)", 19),
    _m("BLACK AND WHITE FLAG FOR CAR 30 (LAW) (15:53:37)", 22),
]
PER_ESCAPE = [
    _m("TURN 1 INCIDENT INVOLVING CAR 11 (PER) NOTED - FAILING TO FOLLOW RACE DIRECTORS INSTRUCTIONS – ESCAPE ROAD INSTRUCTIONS", 25),
    _m("FIA STEWARDS: TURN 1 INCIDENT INVOLVING CAR 11 (PER) UNDER INVESTIGATION - FAILING TO FOLLOW RACE DIRECTORS INSTRUCTIONS – ESCAPE ROAD INSTRUCTIONS (16:09:43)", 28),
    _m("FIA STEWARDS: 5 SECOND TIME PENALTY FOR CAR 11 (PER) - FAILING TO FOLLOW RACE DIRECTORS INSTRUCTIONS – ESCAPE ROAD INSTRUCTIONS", 30),
]


def test_follows_one_incident_through_its_time_tag():
    r = analyse(LAW_HUL)
    assert len(r["incidents"]) == 1
    inc = r["incidents"][0]
    assert inc["cars"] == ["30", "27"] and inc["turn"] == 4
    assert inc["history"] == ["notato", "in esame", "bandiera bianco-nera"]
    assert inc["closed"] and inc["lap"] == 22


def test_penalty_closes_the_incident_with_the_same_reason():
    r = analyse(PER_ESCAPE)
    assert len(r["incidents"]) == 1
    assert r["incidents"][0]["status"] == "5 s di penalità" and r["incidents"][0]["closed"]
    assert r["drivers"]["11"]["penalties"] == ["5 s di penalità"]
    assert r["drivers"]["11"]["open"] == 0


def test_open_investigation_counts_for_every_car_involved():
    r = analyse(LAW_HUL[:2])
    assert r["drivers"]["30"]["open"] == 1 and r["drivers"]["27"]["open"] == 1


def test_different_reasons_are_different_incidents():
    r = analyse([
        _m("INCIDENT INVOLVING CAR 11 (PER) NOTED - FAILING TO FOLLOW RACE DIRECTORS INSTRUCTIONS – PRACTICE START INFRINGEMENT (14:20:30)"),
        PER_ESCAPE[0],
    ])
    assert len(r["incidents"]) == 2


def test_track_limits_are_counted_per_driver():
    r = analyse([
        _m("CAR 55 (SAI) LAP DELETED - TRACK LIMITS AT TURN 5 LAP 1 15:04:11"),
        _m("CAR 23 (ALB) TIME 2:12.275 DELETED - TRACK LIMITS AT TURN 1 LAP 6 15:46:00"),
        _m("CAR 23 (ALB) TIME 1:27.287 DELETED - TRACK LIMITS AT TURN 1 LAP 21 16:08:00"),
    ])
    assert r["drivers"]["23"]["track_limits"] == 2 and r["drivers"]["55"]["track_limits"] == 1
    assert r["incidents"] == []


def test_ignores_messages_that_are_not_about_stewards():
    r = analyse([_m("CLEAR IN TRACK SECTOR 6"), _m("DRS ENABLED"), _m("GREEN LIGHT - PIT EXIT OPEN")])
    assert r == {"incidents": [], "drivers": {}}
