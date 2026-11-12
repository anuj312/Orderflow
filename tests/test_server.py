import json

import pytest
from fastapi.testclient import TestClient

from helpers import at
from server import Config, create_app

SECRET_KEY = "APIKEY_SHOULD_NEVER_LEAK_123"
SECRET_TOKEN = "ACCESSTOKEN_SHOULD_NEVER_LEAK_456"
FEED_TOKEN = "feed-token-for-tests"


@pytest.fixture(scope="module")
def demo():
    app = create_app(Config(mode="demo", start_feeds=False, cache_dir=""))
    rt = app.state.runtime
    rt.demo.bootstrap()
    for _ in range(3):
        rt.demo.step()
    rt.publish_once()
    with TestClient(app) as c:
        yield c, rt


@pytest.fixture()
def live():
    app = create_app(Config(mode="live", feed_token=FEED_TOKEN, start_feeds=False, cache_dir=""))
    rt = app.state.runtime
    fixed = at(10, 30)
    rt.store.clock = lambda: fixed
    rt.scanner.wall_clock = lambda: fixed
    with TestClient(app) as c:
        yield c, rt


def test_index_and_security_headers(demo):
    c, _ = demo
    r = c.get("/")
    assert r.status_code == 200 and "ORDERFLOW STUDIO" in r.text
    assert r.headers["x-frame-options"] == "DENY" and "script-src 'self'" in r.headers["content-security-policy"]
    assert c.get("/does-not-exist").status_code == 404
    assert c.get("/api/nope").json()["error"] == "not_found"


def test_universe_and_version(demo):
    c, _ = demo
    u = c.get("/api/universe").json()
    assert u["count"] == 198 and len(u["primarySectors"]) == 14 and u["watchlists"] == ["NIFTY_50"]
    v = c.get("/api/version").json()
    assert v["features"]["orderPlacement"] is False and v["features"]["ingestEnabled"] is False


def test_state_shape_demo_labels_and_counts(demo):
    c, _ = demo
    s = c.get("/api/state").json()
    assert s["market"]["status"] == "DEMO" and s["demo"] is True
    assert len(s["sectors"]) == 14 and all(x["sector"] != "NIFTY_50" for x in s["sectors"])
    assert len(s["leaders"]["bullish"]) == 6 and len(s["leaders"]["bearish"]) == 6
    assert len(s["picks"]["bullish"]["slots"]) == 3 and len(s["picks"]["bearish"]["slots"]) == 3
    u = s["universe"]
    assert u["bullish"] + u["bearish"] == u["available"] == 198
    assert sum(x["total"] for x in s["sectors"]) + len(s["watchlistOnly"]["symbols"]) == 198
    recs = s["records"]
    assert all(r["momentumState"] == "DEMO" and r["momBasis"] == "DEMO" for r in recs.values() if r["available"])
    assert all(not slot.get("pickConfirmed") for side in ("bullish", "bearish") for slot in s["picks"][side]["slots"])
    json.dumps(s, allow_nan=False)


def test_http_polling_never_advances_market_revision_or_picks(demo):
    c, rt = demo
    adv, rev = rt.scanner.picks.advances, rt.store.market_revision
    for _ in range(4):
        c.get("/api/state")
        c.get("/api/symbol/INFY")
    assert rt.scanner.picks.advances == adv and rt.store.market_revision == rev
    rt.publish_once()
    rt.publish_once()  # periodic publish without new data
    assert rt.scanner.picks.advances == adv
    rt.demo.step()
    rt.publish_once()
    assert rt.scanner.picks.advances == adv + 1 and rt.store.market_revision == rev + 1


def test_symbol_detail_timeframes_change_aggregation(demo):
    c, _ = demo
    r5 = c.get("/api/symbol/CDSL?timeframe=5m").json()
    r15 = c.get("/api/symbol/CDSL?timeframe=15m").json()
    r1h = c.get("/api/symbol/CDSL?timeframe=1h").json()
    assert 0 < len(r5["candles"]) <= 90 and len(r15["candles"]) <= 90
    assert r15["candles"][1]["t"] - r15["candles"][0]["t"] in (900,) or len(r15["candles"]) < 2
    open_offsets = {(b["t"] + 19800 - (9 * 3600 + 15 * 60)) % 3600 for b in r1h["candles"]}
    assert open_offsets == {0}                 # 1h buckets start at 09:15, 10:15, ...
    assert r5["dashboardOb"]["obQualityScore"] == r5["summary"]["obQualityScore"]
    assert r5["ob"]["obQualityScore"] == r5["summary"]["obQualityScore"]   # list and chart reconcile on 5m
    assert r5["profile"]["basis"] == "5M CANDLE EST." and "aggressor" in r5["details"]["obDeltaSource"].lower()
    assert r5["timing"]["maxCandles"] == 90


def test_symbol_validation(demo):
    c, _ = demo
    assert c.get("/api/symbol/NOTREAL").status_code == 404
    assert c.get("/api/symbol/infy").status_code == 404           # exact match only
    assert c.get("/api/symbol/INFY?timeframe=2h").status_code == 400
    assert c.get("/api/symbol/M%26M?timeframe=30m").status_code == 200
    assert c.get("/api/symbol/BAJAJ-AUTO").json()["symbol"] == "BAJAJ-AUTO"


def test_ingest_disabled_outside_live_mode(demo):
    c, _ = demo
    r = c.post("/api/ingest", json={"quotes": []}, headers={"X-Feed-Token": "x"})
    assert r.status_code == 403


def _quote(**kw):
    q = {"symbol": "INFY", "ltp": 1500.0, "prevClose": 1480.0, "open": 1485.0, "high": 1502.0, "low": 1483.0,
         "volume": 100000, "oi": 5000, "timestamp": "2026-11-10T10:29:50+05:30"}
    q.update(kw)
    return q


def test_ingest_authentication_and_validation(live):
    c, rt = live
    assert c.post("/api/ingest", json={"quotes": [_quote()]}).status_code == 401
    assert c.post("/api/ingest", json={"quotes": [_quote()]}, headers={"X-Feed-Token": "wrong"}).status_code == 401
    h = {"X-Feed-Token": FEED_TOKEN}
    assert c.post("/api/ingest", json={"quotes": [_quote(symbol="FAKE")]}, headers=h).status_code == 422
    assert c.post("/api/ingest", json={"quotes": [_quote(ltp=-1)]}, headers=h).status_code == 422
    assert c.post("/api/ingest", json={"quotes": [_quote(extra=1)]}, headers=h).status_code == 422
    assert c.post("/api/ingest", content=b'{"quotes":[{"symbol":"INFY","ltp":NaN}]}',
                  headers={**h, "content-type": "application/json"}).status_code == 422
    assert c.post("/api/ingest", json={"quotes": []}, headers=h).status_code == 422
    rev = rt.store.market_revision
    r = c.post("/api/ingest", json={"quotes": [_quote(), _quote(symbol="TCS", timestamp="2026-11-10T10:45:00+05:30")]},
               headers=h).json()
    assert r["accepted"] == 1 and r["rejected"][0]["reason"] == "timestamp in the future"
    assert rt.store.market_revision == rev + 1


def test_live_mode_labels_and_status(live):
    c, rt = live
    s = c.get("/api/state").json()
    assert s["market"]["status"] == "WAITING" and s["universe"]["available"] == 0  # nothing invented
    c.post("/api/ingest", json={"quotes": [_quote()]}, headers={"X-Feed-Token": FEED_TOKEN})
    rt.publish_once()
    s = c.get("/api/state").json()
    assert s["market"]["status"] == "LIVE"
    infy = s["records"]["INFY"]
    assert infy["available"] and infy["momentumState"] == "WARMING_UP" and infy["mom"] <= 35
    assert infy["rvol"] is None  # no history baseline -> N/A, not zero
    assert s["universe"]["available"] == 1


def test_kite_mode_without_credentials_reports_error_not_demo():
    app = create_app(Config(mode="kite", start_feeds=False, cache_dir=""))
    with TestClient(app) as c:
        s = c.get("/api/state").json()
        assert s["market"]["status"] == "ERROR" and s["demo"] is False
        assert s["universe"]["available"] == 0


def test_no_credential_leakage_anywhere():
    app = create_app(Config(mode="kite", kite_api_key=SECRET_KEY, kite_access_token=SECRET_TOKEN,
                            feed_token=FEED_TOKEN, start_feeds=False, cache_dir=""))
    rt = app.state.runtime
    rt.kite._error("test", Exception(f"boom {SECRET_TOKEN} {SECRET_KEY}"))
    with TestClient(app) as c:
        bodies = [c.get(p).text for p in ("/", "/api/state", "/api/health", "/api/version", "/api/universe",
                                          "/static/app.js", "/static/index.html")]
    for b in bodies:
        assert SECRET_KEY not in b and SECRET_TOKEN not in b and FEED_TOKEN not in b
    assert "***" in json.dumps(list(rt.kite.errors))
    assert SECRET_KEY not in repr(rt.cfg) and SECRET_TOKEN not in repr(rt.cfg)


def test_sse_stream_sends_retry_and_snapshot(demo):
    import asyncio
    c, rt = demo

    class FakeRequest:
        async def is_disconnected(self):
            return False

    endpoint = next(r.endpoint for r in c.app.routes if getattr(r, "path", "") == "/api/events")

    async def first_chunks():
        resp = await endpoint(FakeRequest())
        it = resp.body_iterator
        chunks = [await it.__anext__(), await it.__anext__()]
        await it.aclose()
        return chunks

    chunks = asyncio.run(first_chunks())
    assert chunks[0].startswith("retry:") and chunks[1].startswith("event: snapshot")
    assert len(rt.subscribers) == 0  # subscriber queue released on close


def test_session_rollover_clears_tapes_and_picks(demo):
    _, rt = demo
    rt.demo._rollover()
    snap = rt.scanner.build_snapshot()
    assert snap["market"]["sessionDate"] == rt.demo.vdate.isoformat()
    assert all(len(t) <= 1 for t in rt.store.tapes.values())
    filled = [s for side in ("bullish", "bearish") for s in snap["picks"][side]["slots"] if not s["empty"]]
    assert all(s["obQualityScore"] is not None for s in filled)


def test_one_feed_message_is_one_market_revision():
    from market_store import MarketStore
    st = MarketStore("live", clock=lambda: at(10, 30))
    st.ensure_session()
    st.apply_market_update([{"symbol": "INFY", "ltp": 100.0, "prevClose": 99.0, "ts": at(10, 29, 50)}],
                           [{"symbol": "INFY", "oi": 10, "ts": at(10, 29, 50)}])
    assert st.market_revision == 1
    st.apply_market_update([{"symbol": "INFY", "ltp": 100.0, "prevClose": 99.0, "ts": at(10, 29, 50)}],
                           [{"symbol": "INFY", "oi": 10, "ts": at(10, 29, 50)}])
    assert st.market_revision == 1  # identical data: no new observation
