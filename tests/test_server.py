import asyncio

from muretto import server
from muretto.state import Lap


class _Live:
    """Finto feed live: non produce niente (i test guidano lo stato a mano)."""

    def __aiter__(self):
        return self

    async def __anext__(self):
        await asyncio.sleep(3600)


def _run_broadcast(hub, seconds):
    async def go():
        task = asyncio.create_task(hub.broadcast())
        await asyncio.sleep(seconds)
        task.cancel()
    asyncio.run(go())


def test_broadcast_survives_a_failing_snapshot(monkeypatch):
    monkeypatch.setattr(server, "TICK", 0.05)
    hub = server.Hub(_Live())
    real, calls = hub.state.snapshot, {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("dato storto")
        return real()
    monkeypatch.setattr(hub.state, "snapshot", flaky)
    _run_broadcast(hub, 0.4)
    assert len(hub.history) >= 2  # dopo l'errore gli aggiornamenti continuano


def test_slow_phone_is_dropped_without_blocking_the_others(monkeypatch):
    monkeypatch.setattr(server, "TICK", 0.05)
    hub = server.Hub(_Live())
    hub.PUSH_TIMEOUT = 0.1
    sent = []

    class Phone:
        def __init__(self, slow):
            self.slow = slow

        async def close(self):
            pass

    async def push(ws, c, now):
        if ws.slow:
            await asyncio.sleep(10)
        sent.append(now)
    monkeypatch.setattr(hub, "_push", push)
    fast, slow = Phone(False), Phone(True)
    hub.clients = {fast: {}, slow: {}}
    _run_broadcast(hub, 0.6)
    assert slow not in hub.clients and fast in hub.clients
    assert len(sent) >= 3  # il telefono veloce ha continuato a ricevere


def test_lap_history_survives_a_restart(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "cache_dir", lambda: tmp_path)
    hub = server.Hub(_Live())
    hub.state.apply("SessionInfo", {"Path": "2026/x/race/"}, 0.0)
    hub.state.laps["16"].append(Lap(lap=5, seconds=81.2, track_status="1", clean=True, pit=False, ts=10.0))
    hub._save_laps()
    again = server.Hub(_Live())
    again.state.apply("SessionInfo", {"Path": "2026/x/race/"}, 0.0)
    again._load_laps()
    assert [l.seconds for l in again.state.laps["16"]] == [81.2]
    other = server.Hub(_Live())
    other.state.apply("SessionInfo", {"Path": "2026/x/fp1/"}, 0.0)  # sessione diversa: niente
    other._load_laps()
    assert not other.state.laps.get("16")
