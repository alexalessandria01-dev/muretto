from datetime import datetime, timezone

from muretto.radio import PROMPT, clean_text, hot_words, lap_at, recorded_utc
from muretto.state import Lap


def test_clean_text_fixes_f1_words():
    assert clean_text(" I think please pass it to the sewers. Copy. ") == "I think please pass it to the stewards. Copy"


def test_clean_text_drops_noise():
    for raw in ["", "   ", "[BLANK_AUDIO]", "(beeps)", " (static) ", "[Music]", "...", "Uh.", "(trimmer buzzing)", " (alarm blaring) "]:
        assert clean_text(raw) is None, raw


def test_clean_text_drops_prompt_echo():
    # con una clip muta whisper a volte ripete il prompt
    assert clean_text(PROMPT) is None


def test_hot_words():
    assert hot_words("Box this lap, we have a puncture") == ["box", "puncture"]
    assert hot_words("Good job, keep pushing") == []
    assert hot_words("Russell is under investigation") == ["investigation"]


def test_recorded_utc_from_file_name_and_gmt_offset():
    # Monza: file alle 16:11:05 ora locale, GmtOffset +2 → 14:11:05 UTC (il campo Utc diceva 14:11:53)
    t = recorded_utc("TeamRadio/ANT_12_20260906_161105.mp3", "02:00:00", "2026-09-06T14:11:53.42Z")
    assert datetime.fromtimestamp(t, timezone.utc).strftime("%H:%M:%S") == "14:11:05"


def test_recorded_utc_falls_back_to_utc_minus_delay():
    t = recorded_utc("TeamRadio/strano.mp3", "02:00:00", "2026-09-06T14:11:53Z")
    assert datetime.fromtimestamp(t, timezone.utc).strftime("%H:%M:%S") == "14:11:33"


def test_lap_at_is_the_lap_in_progress():
    laps = [Lap(lap=n, seconds=90.0, track_status="1", clean=True, pit=False, ts=100.0 * n) for n in range(1, 4)]
    assert lap_at(laps, 150.0) == 2   # fra la fine del giro 1 (100) e del 2 (200): sta facendo il 2
    assert lap_at(laps, 350.0) == 4   # dopo l'ultimo chiuso: il giro dopo
    assert lap_at([], 10.0) is None


def test_utc_offset_ignores_bursts_and_old_snapshot_messages():
    from muretto.state import RaceState
    st = RaceState()
    st.apply("Heartbeat", {"Utc": "2026-09-06T13:00:00Z"}, 100.0)
    st.apply("Heartbeat", {"Utc": "2026-09-06T13:00:10Z"}, 110.0)
    good = st.utc_offset
    # fine archivio di Monza: tanti Heartbeat allo stesso istante con UTC diversi
    for k in range(80):
        st.apply("Heartbeat", {"Utc": f"2026-09-06T13:{20 + k // 60:02d}:{k % 60:02d}Z"}, 999.0)
    assert abs(st.utc_offset - good) < 1
    # nello snapshot iniziale i messaggi vecchi arrivano tutti ora: non devono contare
    st.apply("__snapshot__", {"RaceControlMessages": {"Messages": [{"Utc": "2026-09-06T11:00:00", "Message": "x"}] * 5}}, 1000.0)
    assert abs(st.utc_offset - good) < 1


def test_clean_text_fixes_what_a_drive():
    from muretto.radio import clean_text
    # Monza 2026: whisper scriveva "water drive" / "water race" nei messaggi di fine gara
    assert clean_text("Yes, give me water drive!") == "Yes, give me what a drive!"
    assert "what a race" in clean_text("George, water race.").lower()
