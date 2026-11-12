"""Fresh intraday momentum from four rolling same-session windows (5/15/30/60 min).

One observation per minute per stock (max 68). The current minute's observation is
replaced by newer ticks. References come only from the current session tape: the
sample at the exact target minute or up to one minute earlier. Nothing is backfilled.
"""
from __future__ import annotations

from collections import OrderedDict
from datetime import date, datetime, timedelta
from typing import Optional

from market_time import minute_floor, to_ist, in_session_hours

WINDOWS = (5, 15, 30, 60)
TAPE_MAX_POINTS = 68
STALE_SECONDS = 120
WEIGHTS = {5: 20, 15: 16, 30: 12, 60: 10}


def thresholds(adr20: Optional[float]) -> dict[int, float]:
    a = adr20 if adr20 is not None else 0.0
    return {
        5: max(0.10, a * 0.055),
        15: max(0.22, a * 0.12),
        30: max(0.32, a * 0.18),
        60: max(0.48, a * 0.28),
    }


class MinuteTape:
    """Per-symbol one-sample-per-minute tape for a single NSE session."""

    def __init__(self, session_date: Optional[date] = None, max_points: int = TAPE_MAX_POINTS):
        self.session_date = session_date
        self.max_points = max_points
        self._points: "OrderedDict[datetime, tuple[float, Optional[float], datetime]]" = OrderedDict()

    def reset(self, session_date: Optional[date]) -> None:
        self.session_date = session_date
        self._points.clear()

    def add(self, ts: datetime, ltp: float, cum_volume: Optional[float]) -> bool:
        """Record a tick. Returns False if rejected (other session, out-of-order, off-hours)."""
        ts = to_ist(ts)
        if self.session_date is None:
            self.session_date = ts.date()
        if ts.date() != self.session_date or not in_session_hours(ts):
            return False
        key = minute_floor(ts)
        if self._points:
            last_key = next(reversed(self._points))
            last_ts = self._points[last_key][2]
            if ts < last_ts:
                return False  # out-of-order tick; never rewrite history
            if key < last_key:
                return False
        prev = self._points.get(key)
        if prev is not None and cum_volume is None:
            cum_volume = prev[1]
        self._points[key] = (float(ltp), cum_volume, ts)
        self._points.move_to_end(key)
        while len(self._points) > self.max_points:
            self._points.popitem(last=False)
        return True

    def latest(self) -> Optional[tuple[datetime, float, Optional[float], datetime]]:
        if not self._points:
            return None
        k = next(reversed(self._points))
        p = self._points[k]
        return k, p[0], p[1], p[2]

    def reference(self, minute: datetime) -> Optional[tuple[float, Optional[float]]]:
        for m in (minute, minute - timedelta(minutes=1)):
            p = self._points.get(m)
            if p is not None:
                return p[0], p[1]
        return None

    def __len__(self) -> int:
        return len(self._points)


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def compute_fresh(tape: MinuteTape, *, now: datetime, ltp: Optional[float], prev_close: Optional[float],
                  adr20: Optional[float], rvol20: Optional[float], session_date: date) -> dict:
    now = to_ist(now)
    th = thresholds(adr20)
    out = {
        "windows": {str(n): {"pct": None, "directedPct": None, "threshold": round(th[n], 4), "status": "pending"}
                    for n in WINDOWS},
        "readyCount": 0,
        "momentumState": "WARMING_UP",
        "volumeConfirmed": rvol20 is not None and rvol20 >= 1.0,
        "pickEligible": False,
        "recentVolumeRatio": None,
        "freshScore": 0.0,
        "freshReason": "",
        "direction": None,
        "thresholdBasis": "ADR20" if adr20 is not None else "FLOOR_ONLY_NO_ADR20",
    }
    if ltp is None or not prev_close:
        out["momentumState"] = "STALE"
        out["freshReason"] = "No current quote or previous close available."
        return out
    day_change = 100.0 * (ltp / prev_close - 1.0)
    sign = 1.0 if day_change >= 0 else -1.0
    out["direction"] = "BULLISH" if sign > 0 else "BEARISH"

    latest = tape.latest()
    if latest is None or tape.session_date != session_date:
        out["momentumState"] = "STALE"
        out["freshReason"] = "No minute sample from the current NSE session."
        return out
    cur_minute, _, cur_vol, cur_ts = latest
    if cur_ts.date() != session_date or (now - cur_ts).total_seconds() > STALE_SECONDS:
        out["momentumState"] = "STALE"
        out["freshReason"] = "Latest minute-sampled quote is older than 2 minutes."
        return out

    directed: dict[int, Optional[float]] = {}
    for n in WINDOWS:
        ref = tape.reference(cur_minute - timedelta(minutes=n))
        w = out["windows"][str(n)]
        if ref is None or ref[0] <= 0:
            directed[n] = None
            continue
        d = 100.0 * (ltp / ref[0] - 1.0)
        directed[n] = d * sign
        w.update(pct=round(d, 4), directedPct=round(d * sign, 4), status="ok")
    out["readyCount"] = sum(1 for v in directed.values() if v is not None)

    # recent volume acceleration from cumulative-volume increments
    r5 = tape.reference(cur_minute - timedelta(minutes=5))
    r10 = tape.reference(cur_minute - timedelta(minutes=10))
    if cur_vol is not None and r5 and r10 and r5[1] is not None and r10[1] is not None:
        denom = r5[1] - r10[1]
        num = cur_vol - r5[1]
        if denom > 0 and num >= 0:
            out["recentVolumeRatio"] = round(num / denom, 4)

    if directed[5] is None or directed[15] is None:
        out["momentumState"] = "WARMING_UP"
        missing = [f"{n}m" for n in (5, 15) if directed[n] is None]
        out["freshReason"] = f"Warming up: current-session {'/'.join(missing)} rolling return not yet available."
        return out

    reversals = [n for n in WINDOWS if directed[n] is not None and directed[n] <= -th[n]]
    if reversals:
        out["momentumState"] = "FADING"
        out["freshReason"] = "Strong reversal against day direction in " + ", ".join(f"{n}m" for n in reversals) + "."
        return out

    short_ok = directed[5] >= th[5] and directed[15] >= th[15]
    longer_bad = [n for n in (30, 60) if directed[n] is not None and directed[n] < 0.25 * th[n]]
    pending = [n for n in (30, 60) if directed[n] is None]
    if short_ok and not longer_bad:
        out["momentumState"] = "ACTIVE"
        reason = "5m and 15m moves exceed ADR-scaled thresholds"
        if pending:
            reason += "; " + "/".join(f"{n}m" for n in pending) + " window pending"
        out["freshReason"] = reason + "."
    else:
        out["momentumState"] = "CONSOLIDATING"
        if not short_ok:
            weak = [f"{n}m" for n in (5, 15) if directed[n] < th[n]]
            out["freshReason"] = "Short-term momentum weak (" + ", ".join(weak) + " below threshold)."
        else:
            out["freshReason"] = "Longer window disagrees (" + ", ".join(f"{n}m" for n in longer_bad) + " below 25% of threshold)."

    out["pickEligible"] = out["momentumState"] == "ACTIVE" and out["volumeConfirmed"]
    if out["momentumState"] == "ACTIVE":
        score = 0.0
        for n in WINDOWS:
            if directed[n] is not None:
                score += WEIGHTS[n] * min(3.0, max(0.0, directed[n]) / th[n])
        if rvol20 is not None:
            score += 4.0 * min(3.0, rvol20)
        if out["recentVolumeRatio"] is not None:
            score += 3.0 * min(3.0, out["recentVolumeRatio"])
        out["freshScore"] = round(score, 3)
    return out
