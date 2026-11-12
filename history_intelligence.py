"""Previous-20-completed-session intelligence: ADR20, ATR14, 20D high/low and
time-matched cumulative-volume RVOL. Pure functions; no feed or network access.

Candle shape (5m): {"t": epoch_seconds_of_candle_start, "o","h","l","c": float, "v": int|None}
Daily bar shape:   {"date": "YYYY-MM-DD" | date, "o","h","l","c": float, "v": int|None}
"""
from __future__ import annotations

import gzip
import json
import math
import os
from collections import defaultdict
from datetime import date, datetime
from statistics import mean
from typing import Iterable, Optional

from market_time import (SESSION_SLOTS, day_from_number, day_number, ist_day_minute, minutes_since_open,
                         to_ist)

LOOKBACK_SESSIONS = 20
ATR_PERIOD = 14
MIN_CANDLES_PER_VALID_SESSION = 60
MIN_SESSIONS_FOR_RVOL = 10
CACHE_VERSION = 3


def _bar_date(bar: dict) -> date:
    d = bar["date"]
    if isinstance(d, datetime):
        return to_ist(d).date()
    if isinstance(d, date):
        return d
    return date.fromisoformat(str(d)[:10])


def _finite(x) -> bool:
    return isinstance(x, (int, float)) and math.isfinite(x)


def completed_daily_bars(daily: Iterable[dict], target_date: date) -> list[dict]:
    """Only bars strictly before target_date (never today's unfinished bar, never future)."""
    bars = [b for b in daily if _bar_date(b) < target_date and all(_finite(b.get(k)) for k in ("h", "l", "c"))]
    bars.sort(key=_bar_date)
    return bars


def compute_adr20(bars: list[dict]) -> Optional[float]:
    vals = [100.0 * (b["h"] - b["l"]) / b["c"] for b in bars[-LOOKBACK_SESSIONS:] if b["c"] > 0]
    return round(mean(vals), 4) if vals else None


def true_ranges(bars: list[dict]) -> list[float]:
    out = []
    for prev, cur in zip(bars, bars[1:]):
        pc = prev["c"]
        out.append(max(cur["h"] - cur["l"], abs(cur["h"] - pc), abs(cur["l"] - pc)))
    return out


def compute_atr14(bars: list[dict]) -> Optional[float]:
    trs = true_ranges(bars)
    if len(trs) < ATR_PERIOD:
        return None
    return round(mean(trs[-ATR_PERIOD:]), 4)


def compute_high_low20(bars: list[dict]) -> tuple[Optional[float], Optional[float]]:
    window = bars[-LOOKBACK_SESSIONS:]
    if not window:
        return None, None
    return max(b["h"] for b in window), min(b["l"] for b in window)


def group_intraday_sessions(candles: Iterable[dict], target_date: date) -> dict[date, dict[int, int]]:
    """Group 5m candles by IST session date (only dates < target_date) -> {slot: volume}."""
    target_n = day_number(target_date)
    by_day: dict[int, dict[int, int]] = defaultdict(dict)
    for c in candles:
        n, m = ist_day_minute(c["t"])
        if n >= target_n or m < 0 or m >= SESSION_SLOTS * 5:
            continue
        v = c.get("v")
        if v is None or not _finite(v) or v < 0:
            continue
        by_day[n][int(m // 5)] = int(v)
    return {day_from_number(n): slots for n, slots in by_day.items()}


def cumulative_volume_profile(sessions: dict[date, dict[int, int]]) -> tuple[Optional[list[float]], int, list[str]]:
    """Mean cumulative volume at the end of each 5m bucket across up to 20 most-recent
    valid sessions (>=60 of 75 candles). Missing candles inside a valid session add 0
    volume; whole missing sessions are never imputed."""
    valid_dates = sorted(d for d, slots in sessions.items() if len(slots) >= MIN_CANDLES_PER_VALID_SESSION)
    valid_dates = valid_dates[-LOOKBACK_SESSIONS:]
    if not valid_dates:
        return None, 0, []
    sums = [0.0] * SESSION_SLOTS
    for d in valid_dates:
        running = 0
        slots = sessions[d]
        for k in range(SESSION_SLOTS):
            running += slots.get(k, 0)
            sums[k] += running
    n = len(valid_dates)
    return [round(s / n, 2) for s in sums], n, [d.isoformat() for d in valid_dates]


def expected_cumulative_volume(profile: list[float], now: datetime) -> Optional[float]:
    """Historical mean cumulative volume at the same IST session time.
    Bucket k covers [09:15+5k, 09:15+5k+5). The expected value is the mean cumulative
    volume through bucket k-1 plus the elapsed fraction of bucket k's mean volume, so at
    exactly 10:00 it equals the mean 09:15-10:00 cumulative volume."""
    m = minutes_since_open(now)
    if m <= 0:
        return None
    if m >= SESSION_SLOTS * 5:
        return profile[-1]
    k = int(m // 5)
    frac = (m - 5 * k) / 5.0
    before = profile[k - 1] if k > 0 else 0.0
    bucket_vol = profile[k] - before
    return before + frac * bucket_vol


def compute_rvol(cum_volume: Optional[float], profile: Optional[list[float]], valid_sessions: int,
                 now: datetime) -> Optional[float]:
    if cum_volume is None or profile is None or valid_sessions < MIN_SESSIONS_FOR_RVOL:
        return None
    expected = expected_cumulative_volume(profile, now)
    if not expected or expected <= 0:
        return None
    return round(cum_volume / expected, 4)


def build_baseline(symbol: str, daily: Iterable[dict], intraday: Iterable[dict], target_date: date) -> dict:
    bars = completed_daily_bars(daily, target_date)
    bars20 = bars[-LOOKBACK_SESSIONS:]
    sessions = group_intraday_sessions(intraday, target_date)
    profile, n_valid, valid_dates = cumulative_volume_profile(sessions)
    high20, low20 = compute_high_low20(bars)
    prev = bars[-1] if bars else None
    history_sessions = len(bars20)
    if history_sessions >= LOOKBACK_SESSIONS and n_valid >= LOOKBACK_SESSIONS:
        status = "READY"
    elif history_sessions == 0 and n_valid == 0:
        status = "UNAVAILABLE"
    else:
        status = "PARTIAL"
    return {
        "symbol": symbol,
        "version": CACHE_VERSION,
        "cacheDate": target_date.isoformat(),
        "historySessions": history_sessions,
        "intradaySessions": n_valid,
        "intradaySessionDates": valid_dates,
        "historyStatus": status,
        "adr20": compute_adr20(bars),
        "atr14": compute_atr14(bars),
        "high20": high20,
        "low20": low20,
        "cumProfile": profile,
        "prevSession": ({"date": _bar_date(prev).isoformat(), "h": prev["h"], "l": prev["l"], "c": prev["c"]}
                        if prev else None),
    }


def live_history_metrics(baseline: Optional[dict], ltp: Optional[float], cum_volume: Optional[float],
                         day_high: Optional[float], day_low: Optional[float], now: datetime) -> dict:
    """Combine a cached baseline with the current quote. Missing values stay None."""
    if not baseline:
        return {"historySessions": 0, "intradaySessions": 0, "historyStatus": "LOADING", "adr20": None,
                "atr14": None, "high20": None, "low20": None, "rvol": None, "breakout20": None,
                "rangeMultiple": None, "cacheDate": None}
    rvol = compute_rvol(cum_volume, baseline.get("cumProfile"), baseline.get("intradaySessions", 0), now)
    breakout = None
    if ltp is not None:
        if baseline.get("high20") is not None and ltp > baseline["high20"]:
            breakout = "ABOVE_20D_HIGH"
        elif baseline.get("low20") is not None and ltp < baseline["low20"]:
            breakout = "BELOW_20D_LOW"
    range_multiple = None
    adr = baseline.get("adr20")
    if adr and ltp and day_high is not None and day_low is not None and ltp > 0:
        range_multiple = round((100.0 * (day_high - day_low) / ltp) / adr, 3)
    return {
        "historySessions": baseline.get("historySessions", 0),
        "intradaySessions": baseline.get("intradaySessions", 0),
        "historyStatus": baseline.get("historyStatus", "PARTIAL"),
        "adr20": adr,
        "atr14": baseline.get("atr14"),
        "high20": baseline.get("high20"),
        "low20": baseline.get("low20"),
        "rvol": rvol,
        "breakout20": breakout,
        "rangeMultiple": range_multiple,
        "cacheDate": baseline.get("cacheDate"),
    }


# ---------------------------------------------------------------- cache (derived only)

def cache_path(cache_dir: str, target_date: date, namespace: str) -> str:
    return os.path.join(cache_dir, f"baselines_{namespace}_{target_date.isoformat()}.json.gz")


def save_cache(cache_dir: str, target_date: date, namespace: str, baselines: dict[str, dict]) -> Optional[str]:
    """Persist derived indicators only (no candles, no credentials)."""
    if not cache_dir:
        return None
    os.makedirs(cache_dir, exist_ok=True)
    path = cache_path(cache_dir, target_date, namespace)
    tmp = path + ".tmp"
    payload = {"version": CACHE_VERSION, "targetDate": target_date.isoformat(), "baselines": baselines}
    with gzip.open(tmp, "wt", encoding="utf-8") as fh:
        json.dump(payload, fh, separators=(",", ":"), allow_nan=False)
    os.replace(tmp, path)
    return path


def load_cache(cache_dir: str, target_date: date, namespace: str) -> dict[str, dict]:
    if not cache_dir:
        return {}
    path = cache_path(cache_dir, target_date, namespace)
    if not os.path.exists(path):
        return {}
    try:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, ValueError):
        return {}
    if payload.get("version") != CACHE_VERSION or payload.get("targetDate") != target_date.isoformat():
        return {}
    return payload.get("baselines", {})
