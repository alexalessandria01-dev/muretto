import base64
import json
import zlib

from muretto.feed import inflate_z, parse_stream_line, split_frames, ts_to_seconds


def _deflate(obj):
    raw = zlib.compress(json.dumps(obj).encode())[2:-4]  # deflate raw, senza header/checksum zlib
    return base64.b64encode(raw).decode()


def test_parse_stream_line_plain():
    ts, data = parse_stream_line('00:00:08.731{"Lines":{"10":{"Position":"1"}}}', "TimingData")
    assert ts == 8.731
    assert data == {"Lines": {"10": {"Position": "1"}}}


def test_parse_stream_line_strips_bom():
    ts, data = parse_stream_line('﻿00:00:07.637{"Lines":{}}', "TimingData")
    assert ts == 7.637
    assert data == {"Lines": {}}


def test_parse_stream_line_inflates_z_topics():
    payload = {"Entries": [{"Utc": "x", "Cars": {"16": {"Channels": {"2": 331}}}}]}
    line = "00:02:09.481" + json.dumps(_deflate(payload))
    ts, data = parse_stream_line(line, "CarData.z")
    assert ts == 129.481
    assert data == payload


def test_inflate_z_roundtrip():
    assert inflate_z(_deflate({"a": [1, 2]})) == {"a": [1, 2]}


def test_ts_to_seconds_hours():
    assert ts_to_seconds("02:05:18.245") == 2 * 3600 + 5 * 60 + 18.245


def test_split_frames_signalr():
    raw = '{"type":6}\x1e{"type":1,"target":"feed","arguments":["TrackStatus",{"Status":"4"},"t"]}\x1e'
    frames = split_frames(raw)
    assert len(frames) == 2
    assert frames[1]["arguments"][0] == "TrackStatus"
