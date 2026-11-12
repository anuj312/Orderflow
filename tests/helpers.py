"""Shared builders for tests (no network, no credentials)."""
from datetime import date, datetime, timedelta

from market_time import IST, epoch, session_open_dt

D = date(2026, 11, 10)  # a Tuesday


def at(hh: int, mm: int, ss: int = 0, d: date = D) -> datetime:
    return datetime(d.year, d.month, d.day, hh, mm, ss, tzinfo=IST)


def candles_from(rows, d: date = D, start_slot: int = 0, tf: int = 5):
    t0 = epoch(session_open_dt(d))
    return [{"t": t0 + (start_slot + i) * tf * 60, "o": o, "h": h, "l": l, "c": c, "v": v}
            for i, (o, h, l, c, v) in enumerate(rows)]


def bullish_ob_rows():
    """14 completed 5m candles: base range, red anchor (idx 9) with a liquidity sweep,
    displacement impulse (idx 10, BOS), FVG (9->11), pullback and a rejection retest (idx 13)."""
    base = [(100.0, 100.5, 99.5, 100.2, 1000)] * 9
    return base + [
        (100.3, 100.4, 99.3, 99.8, 1000),    # 9 red anchor, wicks below 99.5 and closes back above
        (99.9, 101.7, 99.85, 101.6, 3000),   # 10 impulse: close > 100.5 structure high
        (101.6, 102.2, 101.5, 102.0, 1500),  # 11 continuation (low 101.5 > anchor high 100.4 -> FVG)
        (102.0, 102.1, 101.0, 101.2, 1200),  # 12 pullback, no zone overlap
        (100.6, 101.0, 100.2, 100.9, 1400),  # 13 retest into zone [99.3, 100.3], bullish close above
    ]


def mirror_rows(rows, pivot: float = 200.0):
    return [(pivot - o, pivot - l, pivot - h, pivot - c, v) for (o, h, l, c, v) in rows]


BULL_HTF = [100.0, 100.5, 101.0, 101.5, 102.0]
BEAR_HTF = [200.0 - x for x in BULL_HTF]


def daily_bars(n: int, end_before: date, h=110.0, l=100.0, c=105.0):
    out, d = [], end_before
    while len(out) < n:
        d -= timedelta(days=1)
        if d.weekday() < 5:
            out.append({"date": d, "o": c, "h": h, "l": l, "c": c, "v": 1000})
    return list(reversed(out))


def intraday_sessions(n: int, end_before: date, per_candle: int = 100, candles: int = 75):
    out, d, made = [], end_before, 0
    while made < n:
        d -= timedelta(days=1)
        if d.weekday() >= 5:
            continue
        t0 = epoch(session_open_dt(d))
        out.extend({"t": t0 + k * 300, "o": 1, "h": 1, "l": 1, "c": 1, "v": per_candle} for k in range(candles))
        made += 1
    return out
