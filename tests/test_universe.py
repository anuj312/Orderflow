from datetime import date, datetime

from kite_feed import nearest_futures, resolve_equities, tick_to_updates
from market_time import IST
from sectors import ALL_SYMBOLS, PRIMARY_SECTORS, SECTOR_DEFINITIONS, SYMBOL_TO_SECTOR

EXPECTED_COUNTS = {"METAL": 13, "REALTY": 8, "ENERGY": 24, "AUTO": 17, "IT": 17, "PHARMA": 16, "FMCG": 24,
                   "CEMENT": 7, "FINSERVICE": 34, "BANK": 14, "PSUBANK": 8, "DURABLES": 11, "LOGISTICS": 11,
                   "DEFENCE": 13, "NIFTY_50": 50}
UNIVERSE_SIZE = 218   # NSE F&O stocks, Nov-2026 contract list


def test_exact_unique_symbol_count():
    assert len(ALL_SYMBOLS) == UNIVERSE_SIZE
    assert len(set(ALL_SYMBOLS)) == UNIVERSE_SIZE
    assert ALL_SYMBOLS == sorted(ALL_SYMBOLS)


def test_nov_2026_fno_refresh():
    for gone in ("BAJAJHLDNG", "DALBHARAT", "IRCTC", "ICICIPRULI"):
        assert gone not in ALL_SYMBOLS
    for new, sec in (("ICICILIFE", "FINSERVICE"), ("YESBANK", "BANK"), ("MAHABANK", "PSUBANK"), ("GVT&D", "ENERGY"),
                     ("NAM-INDIA", "FINSERVICE"), ("DATAPATTNS", "DEFENCE"), ("IDEA", "DURABLES")):
        assert SYMBOL_TO_SECTOR[new] == sec


def test_fourteen_primary_sectors_plus_nifty50_watchlist():
    assert list(PRIMARY_SECTORS) == ["METAL", "REALTY", "ENERGY", "AUTO", "IT", "PHARMA", "FMCG", "CEMENT",
                                     "FINSERVICE", "BANK", "PSUBANK", "DURABLES", "LOGISTICS", "DEFENCE"]
    assert "NIFTY_50" not in PRIMARY_SECTORS
    assert {k: len(v) for k, v in SECTOR_DEFINITIONS.items()} == EXPECTED_COUNTS
    assert len(set(SECTOR_DEFINITIONS["NIFTY_50"])) == 50


def test_literal_symbols_preserved():
    for s in ("M&M", "BAJAJ-AUTO", "360ONE", "TMPV", "ETERNAL", "LTM", "BHARTIARTL"):
        assert s in ALL_SYMBOLS


def test_no_double_counting_across_primary_sectors():
    seen = {}
    for sec, syms in PRIMARY_SECTORS.items():
        for s in syms:
            assert s not in seen, f"{s} in {seen.get(s)} and {sec}"
            seen[s] = sec
    assert len(seen) == UNIVERSE_SIZE - 1  # + BHARTIARTL (NIFTY_50 only)


def test_symbol_to_sector_primary_and_watchlist_default():
    assert SYMBOL_TO_SECTOR["ADANIENT"] == "METAL"
    assert SYMBOL_TO_SECTOR["M&M"] == "AUTO"
    assert SYMBOL_TO_SECTOR["BHARTIARTL"] == "NIFTY_50"
    assert set(SYMBOL_TO_SECTOR) == set(ALL_SYMBOLS)


def _eq(sym, token, seg="NSE", typ="EQ"):
    return {"tradingsymbol": sym, "exchange": "NSE", "segment": seg, "instrument_type": typ, "instrument_token": token}


def test_missing_kite_symbols_are_reported_not_substituted():
    instruments = [_eq("INFY", 1), _eq("M&M", 2), _eq("TCS", 3, typ="BE"), _eq("RELIANCE-X", 4)]
    found, missing = resolve_equities(instruments, ["INFY", "M&M", "TCS", "RELIANCE"])
    assert set(found) == {"INFY", "M&M"}
    assert missing == ["RELIANCE", "TCS"]


def test_nearest_non_expired_future():
    today = date(2026, 11, 10)
    nfo = [
        {"name": "INFY", "segment": "NFO-FUT", "instrument_type": "FUT", "expiry": date(2026, 10, 28), "instrument_token": 9, "tradingsymbol": "INFY26OCTFUT"},
        {"name": "INFY", "segment": "NFO-FUT", "instrument_type": "FUT", "expiry": date(2026, 12, 29), "instrument_token": 11, "tradingsymbol": "INFY26DECFUT"},
        {"name": "INFY", "segment": "NFO-FUT", "instrument_type": "FUT", "expiry": date(2026, 11, 24), "instrument_token": 10, "tradingsymbol": "INFY26NOVFUT"},
        {"name": "INFY", "segment": "NFO-OPT", "instrument_type": "CE", "expiry": date(2026, 11, 12), "instrument_token": 12, "tradingsymbol": "INFY26NOV1500CE"},
        {"name": "TCS", "segment": "NFO-FUT", "instrument_type": "FUT", "expiry": date(2026, 11, 1), "instrument_token": 13, "tradingsymbol": "TCSOLD"},
    ]
    fut = nearest_futures(nfo, ["INFY", "TCS"], today)
    assert fut["INFY"]["tradingsymbol"] == "INFY26NOVFUT"
    assert "TCS" not in fut  # only expired contract available


def test_tick_conversion_quote_mode_uses_received_time_and_full_mode_oi():
    received = datetime(2026, 11, 10, 10, 0, tzinfo=IST)
    tmap = {1: ("INFY", "EQ"), 2: ("INFY", "FUT")}
    ticks = [{"instrument_token": 1, "last_price": 1500.0, "volume_traded": 1000, "average_traded_price": 1498.0,
              "ohlc": {"open": 1490, "high": 1505, "low": 1488, "close": 1480}},
             {"instrument_token": 2, "last_price": 1502.0, "oi": 5000, "exchange_timestamp": received},
             {"instrument_token": 99, "last_price": 1.0}]
    eq, oi = tick_to_updates(ticks, tmap, received)
    assert eq[0]["tsSource"] == "RECEIVED" and eq[0]["prevClose"] == 1480
    assert oi == [{"symbol": "INFY", "oi": 5000, "ts": received}]
