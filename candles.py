"""Live 5m candle building from ticks, history merge, and 09:15-anchored resampling.

Candle: {"t": epoch_start, "o","h","l","c": float, "v": int|None, "complete": bool,
         "src": "HIST"|"TICK", "partial": bool}
"partial" means the candle was started mid-bucket after connect, so its open and
volume do not cover the full bucket. Missing startup ticks are never reconstructed.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Iterable, Optional

from market_time import (IST, SESSION_MINUTES, day_from_number, epoch, ist_day_minute, open_epoch_of_day,
                         slot_end, slot_index, slot_start, to_ist)

TIMEFRAMES = {"5m": 5, "15m": 15, "30m": 30, "1h": 60}


class SessionCandles:
    def __init__(self, session_date: date):
        self.session_date = session_date
        self.slots: dict[int, dict] = {}
        self.last_cum: Optional[float] = None
        self.last_ts: Optional[datetime] = None

    def on_tick(self, ts: datetime, ltp: float, cum_volume: Optional[float]) -> bool:
        ts = to_ist(ts)
        if ts.date() != self.session_date:
            return False
        idx = slot_index(ts)
        if idx is None:
            return False
        if self.last_ts is not None and ts < self.last_ts:
            return False
        delta = None
        if cum_volume is not None:
            if self.last_cum is not None and cum_volume >= self.last_cum:
                delta = cum_volume - self.last_cum
            if self.last_cum is None or cum_volume >= self.last_cum:
                self.last_cum = cum_volume
        self.last_ts = ts
        c = self.slots.get(idx)
        if c is None:
            # A brand-new bucket opened by a tick: partial if we had no volume reference
            # or if the tick did not arrive at the very start of the bucket.
            starts_clean = (ts - slot_start(self.session_date, idx)).total_seconds() < 60 and delta is not None
            c = {"t": epoch(slot_start(self.session_date, idx)), "o": ltp, "h": ltp, "l": ltp, "c": ltp,
                 "v": int(delta) if delta is not None else None, "src": "TICK", "partial": not starts_clean}
            self.slots[idx] = c
            return True
        c["h"] = max(c["h"], ltp)
        c["l"] = min(c["l"], ltp)
        c["c"] = ltp
        if delta is not None:
            c["v"] = int((c["v"] or 0) + delta)
        return True

    def merge_history(self, candles: Iterable[dict]) -> int:
        """Merge provider historical 5m candles for this session (authoritative OHLC/volume)."""
        merged = 0
        for hc in candles:
            dt = datetime.fromtimestamp(hc["t"], IST)
            if dt.date() != self.session_date:
                continue
            idx = slot_index(dt)
            if idx is None:
                continue
            cur = self.slots.get(idx)
            new = {"t": hc["t"], "o": hc["o"], "h": hc["h"], "l": hc["l"], "c": hc["c"], "v": hc.get("v"),
                   "src": "HIST", "partial": False}
            if cur is not None and cur.get("src") == "TICK":
                # forming bucket: keep latest tick close and widen extremes
                new["h"] = max(new["h"], cur["h"])
                new["l"] = min(new["l"], cur["l"])
                new["c"] = cur["c"]
                if cur.get("v") is not None and (new["v"] is None or cur["v"] > new["v"]):
                    new["v"] = cur["v"]
            self.slots[idx] = new
            merged += 1
        return merged

    def candles(self, now: datetime) -> list[dict]:
        now = to_ist(now)
        out = []
        for idx in sorted(self.slots):
            c = dict(self.slots[idx])
            c["complete"] = slot_end(self.session_date, idx) <= now
            out.append(c)
        return out

    def completed(self, now: datetime) -> list[dict]:
        return [c for c in self.candles(now) if c["complete"]]


def resample(candles: list[dict], tf_minutes: int, now_epoch: Optional[float] = None) -> list[dict]:
    """Aggregate 5m candles into tf buckets anchored at 09:15 IST, per session only.
    OHLC = first/max/min/last, volume summed (None only if all members lack volume)."""
    if tf_minutes not in (5, 15, 30, 60):
        raise ValueError(f"unsupported timeframe {tf_minutes}")
    groups: dict[tuple[int, int], list[dict]] = {}
    for c in sorted(candles, key=lambda x: x["t"]):
        n, m = ist_day_minute(c["t"])
        if m < 0 or m >= SESSION_MINUTES:
            continue  # never aggregate outside 09:15-15:30 or across sessions
        groups.setdefault((n, int(m // tf_minutes)), []).append(c)
    out = []
    for (n, k), members in sorted(groups.items()):
        open_e = open_epoch_of_day(n)
        start = open_e + k * tf_minutes * 60
        end = min(start + tf_minutes * 60, open_e + SESSION_MINUTES * 60)
        vols = [m["v"] for m in members if m.get("v") is not None]
        complete = all(m.get("complete", True) for m in members)
        if now_epoch is not None:
            complete = complete and end <= now_epoch
        out.append({"t": start, "o": members[0]["o"], "h": max(m["h"] for m in members),
                    "l": min(m["l"] for m in members), "c": members[-1]["c"],
                    "v": int(sum(vols)) if vols else None, "complete": complete,
                    "partial": any(m.get("partial") for m in members), "members": len(members)})
    return out


def vwap_series(candles: list[dict]) -> list[Optional[float]]:
    """Session VWAP from typical price * volume; resets every session; no future bars."""
    out: list[Optional[float]] = []
    cur_date = None
    pv = vol = 0.0
    for c in candles:
        d = ist_day_minute(c["t"])[0]
        if d != cur_date:
            cur_date, pv, vol = d, 0.0, 0.0
        v = c.get("v")
        if v:
            pv += (c["h"] + c["l"] + c["c"]) / 3.0 * v
            vol += v
        out.append(round(pv / vol, 4) if vol > 0 else None)
    return out


def session_of(t: int) -> date:
    return day_from_number(ist_day_minute(t)[0])
