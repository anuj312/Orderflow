"""Kite driver exercised end-to-end with fake KiteConnect / KiteTicker objects (no network, no credentials)."""
from datetime import datetime, timedelta

from kite_feed import KiteFeed
from market_store import MarketStore
from market_time import now_ist, previous_trading_day, session_open_dt
from sectors import ALL_SYMBOLS


class FakeKite:
    def __init__(self):
        self.calls = []

    def instruments(self, ex):
        if ex == "NSE":
            return [{"tradingsymbol": s, "exchange": "NSE", "segment": "NSE", "instrument_type": "EQ",
                     "instrument_token": 1000 + i} for i, s in enumerate(ALL_SYMBOLS) if s != "TMPV"]
        today = now_ist().date()
        return [{"name": "INFY", "segment": "NFO-FUT", "instrument_type": "FUT", "expiry": today + timedelta(days=9),
                 "instrument_token": 99, "tradingsymbol": "INFYFUT"}]

    def quote(self, keys):
        ts = datetime.now().replace(microsecond=0)
        out = {}
        for k in keys:
            out[k] = {"last_price": 100.0, "ohlc": {"open": 99.0, "high": 101.0, "low": 98.0, "close": 99.5},
                      "volume": 1000, "average_price": 100.0, "timestamp": ts, "oi": 5000}
        return out

    def historical_data(self, token, frm, to, interval):
        self.calls.append((token, interval))
        rows = []
        d = to.date() if isinstance(to, datetime) else to
        if interval == "day":
            cur = d
            while len(rows) < 25:
                if cur.weekday() < 5 and cur <= d:
                    rows.append({"date": datetime.combine(cur, datetime.min.time()), "open": 100, "high": 104,
                                 "low": 98, "close": 101, "volume": 7500})
                cur -= timedelta(days=1)
            return list(reversed(rows))
        target = now_ist().date()
        cur, made = target, 0
        while made < 20:
            cur = previous_trading_day(cur)
            o = session_open_dt(cur)
            rows.extend({"date": o + timedelta(minutes=5 * k), "open": 100, "high": 101, "low": 99, "close": 100,
                         "volume": 100} for k in range(75))
            made += 1
        return rows


class FakeTicker:
    MODE_QUOTE, MODE_FULL = "quote", "full"

    def __init__(self):
        self.subscribed, self.modes = [], {}

    def connect(self, threaded=False):
        self.on_connect(self, {})

    def subscribe(self, tokens):
        self.subscribed.extend(tokens)

    def set_mode(self, mode, tokens):
        self.modes[mode] = list(tokens)

    def close(self):
        pass


def test_kite_bootstrap_mapping_subscription_history_and_ticks():
    store = MarketStore("kite")
    kite, ticker = FakeKite(), FakeTicker()
    feed = KiteFeed(store, "k", "t", cache_dir="", kite_factory=lambda: kite, ticker_factory=lambda: ticker)
    feed._bootstrap = feed._bootstrap  # run synchronously below (start() would spawn threads)
    import threading
    orig = threading.Thread.start
    threading.Thread.start = lambda self: None  # suppress the background history loop for the test
    try:
        feed._bootstrap()
    finally:
        threading.Thread.start = orig
    assert store.unresolved == ["TMPV"] and len(store.mapped) == 197
    assert store.feed["state"] == "CONNECTED"
    assert set(ticker.modes["quote"]) == {v["token"] for v in feed.eq.values()} and ticker.modes["full"] == [99]
    assert store.quotes["INFY"]["prevClose"] == 99.5 and store.quotes["INFY"]["oi"] == 5000
    target = store.session_date
    feed._load_symbol("INFY", target)
    base = store.baselines["INFY"]
    assert base["historyStatus"] == "READY" and base["intradaySessions"] == 20
    assert ("day" in [c[1] for c in kite.calls]) and ("5minute" in [c[1] for c in kite.calls])
    rev = store.market_revision
    feed._on_ticks(ticker, [{"instrument_token": feed.eq["INFY"]["token"], "last_price": 101.0, "volume_traded": 2000,
                             "ohlc": {"close": 99.5}}, {"instrument_token": 99, "oi": 5100}])
    assert store.quotes["INFY"]["ltp"] == 101.0 and store.quotes["INFY"]["oi"] == 5100
    assert store.market_revision == rev + 1


def test_kite_auth_failure_is_explicit():
    from kiteconnect import exceptions as kex

    class BadKite:
        def instruments(self, ex):
            raise kex.TokenException("Incorrect `api_key` or `access_token`.")

    store = MarketStore("kite")
    feed = KiteFeed(store, "k", "t", kite_factory=lambda: BadKite())
    feed._bootstrap()
    assert store.feed["state"] == "AUTH_ERROR" and "access token" in store.feed["error"]
    assert not store.quotes  # nothing fabricated
