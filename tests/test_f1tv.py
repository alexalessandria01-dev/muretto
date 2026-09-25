import asyncio
import base64
import json
from urllib.parse import quote

from aiohttp.test_utils import TestClient, TestServer

from muretto import f1tv, server
from muretto.feed import LiveFeed


def _jwt(**claims):
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return f"{enc({'alg': 'RS256'})}.{enc(claims)}.firma"


GOOD = _jwt(exp=4_000_000_000, SubscriptionStatus="active", SubscribedProduct="F1 TV Pro")


def test_parse_token_from_cookie_value_or_bare_token():
    cookie = quote(json.dumps({"data": {"subscriptionToken": GOOD}}))
    assert f1tv.parse_token(cookie) == GOOD
    assert f1tv.parse_token("login-session=" + cookie) == GOOD
    assert f1tv.parse_token(f"  {GOOD}  ") == GOOD
    assert f1tv.parse_token("ciao") is None
    assert f1tv.parse_token(quote(json.dumps({"data": {}}))) is None


def test_info_never_contains_the_token_and_flags_problems():
    ok = f1tv.info(GOOD, now=1_000)
    assert ok["connected"] and ok["product"] == "F1 TV Pro" and GOOD not in json.dumps(ok)
    assert not f1tv.info(_jwt(exp=10, SubscribedProduct="F1 TV Pro"), now=1_000)["connected"]
    assert "non include" in f1tv.info(_jwt(exp=4_000_000_000, SubscribedProduct="F1 TV Free"), now=1_000)["problem"]
    assert f1tv.info(None) == {"connected": False}


def test_api_connects_and_disconnects(tmp_path, monkeypatch):
    monkeypatch.setattr(f1tv, "config_file", lambda: tmp_path / "f1tv.json")
    feed = LiveFeed()
    hub_app = server.make_app(feed)
    hub_app.on_startup.clear()  # niente feed vero durante il test
    hub_app.on_cleanup.clear()

    async def go():
        async with TestClient(TestServer(hub_app)) as c:
            r = await c.post("/api/f1tv", json={"token": "sbagliato"})
            assert r.status == 400 and feed.auth_token is None
            r = await c.post("/api/f1tv", json={"token": GOOD})
            body = await r.json()
            assert r.status == 200 and body["connected"] and GOOD not in json.dumps(body)
            assert feed.auth_token == GOOD and f1tv.load() == GOOD
            state = await (await c.get("/api/state")).json()
            assert state["live"] and state["f1tv"]["connected"]
            r = await c.delete("/api/f1tv")
            assert not (await r.json())["connected"] and feed.auth_token is None and f1tv.load() is None
    asyncio.run(go())
