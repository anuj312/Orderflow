"""MOM momentum score (integer 0..100). Separate from freshScore. Heuristic only,
never a win probability. All components are non-negative and auditable."""
from __future__ import annotations

import math
from typing import Optional

MAX_POINTS = {"d5": 25, "d15": 20, "d30": 15, "d60": 10, "rvol": 15, "volAccel": 5, "vwap": 7, "breakout": 3}
STATE_CAPS = {"FADING": 25, "CONSOLIDATING": 42}
ACTIVE_NOT_ELIGIBLE_CAP = 55
MISSING_SHORT_WINDOW_CAP = 35


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def round_half_up(x: float) -> int:
    """Auditable rounding (32.5 -> 33); Python's round() is banker's rounding."""
    return int(math.floor(x + 0.5))


def momentum_score(fresh: dict, *, rvol20: Optional[float], vwap_aligned: Optional[bool],
                   breakout_matches: bool, history_status: str) -> dict:
    """fresh: output of fresh_momentum.compute_fresh."""
    state = fresh.get("momentumState")
    win = fresh.get("windows", {})
    missing: list[str] = []
    parts: dict[str, float] = {}

    def window_part(n: int, key: str):
        w = win.get(str(n), {})
        d, t = w.get("directedPct"), w.get("threshold")
        if d is None or not t:
            parts[key] = 0.0
            missing.append(f"{n}m")
        else:
            parts[key] = MAX_POINTS[key] * _clamp(d / (3.0 * t))

    window_part(5, "d5")
    window_part(15, "d15")
    window_part(30, "d30")
    window_part(60, "d60")
    if rvol20 is None:
        parts["rvol"] = 0.0
        missing.append("RVOL20")
    else:
        parts["rvol"] = MAX_POINTS["rvol"] * _clamp(rvol20 / 4.0)
    rvr = fresh.get("recentVolumeRatio")
    if rvr is None:
        parts["volAccel"] = 0.0
        missing.append("volume acceleration")
    else:
        parts["volAccel"] = MAX_POINTS["volAccel"] * _clamp(rvr / 3.0)
    if vwap_aligned is None:
        missing.append("VWAP")
    parts["vwap"] = float(MAX_POINTS["vwap"]) if vwap_aligned else 0.0
    parts["breakout"] = float(MAX_POINTS["breakout"]) if breakout_matches else 0.0
    parts = {k: round(v, 2) for k, v in parts.items()}

    if state == "STALE":
        return {"score": None, "components": parts, "cap": None, "basis": "STALE", "missing": missing}

    raw = max(0, min(100, round_half_up(sum(parts.values()))))
    caps = []
    if state in STATE_CAPS:
        caps.append(STATE_CAPS[state])
    if state == "ACTIVE" and not fresh.get("pickEligible"):
        caps.append(ACTIVE_NOT_ELIGIBLE_CAP)
    if "5m" in missing or "15m" in missing:
        caps.append(MISSING_SHORT_WINDOW_CAP)
    cap = min(caps) if caps else None
    score = min(raw, cap) if cap is not None else raw

    if state == "WARMING_UP":
        basis = "LIVE_WARMUP"
    elif history_status != "READY":
        basis = "LIVE_PARTIAL_HISTORY"
    elif state == "ACTIVE":
        basis = "LIVE_20D"
    else:
        basis = "LIVE_WATCH"
    return {"score": int(score), "rawScore": int(raw), "components": parts, "cap": cap, "basis": basis,
            "missing": missing}


DEMO_MAX = {"dayMove": 65, "rvol": 21, "oi": 4, "vwap": 10}
DEMO_DAY_MOVE_FULL_SCALE_PCT = 3.0
DEMO_RVOL_FULL_SCALE = 3.0


def demo_momentum_score(day_change_pct: Optional[float], demo_rvol: Optional[float],
                        oi_confirms: bool, vwap_aligned: Optional[bool]) -> dict:
    """Explicitly simulated DEMO score. Does not pretend live rolling readings exist."""
    if day_change_pct is None:
        return {"score": None, "components": {}, "cap": None, "basis": "DEMO", "missing": ["price"]}
    parts = {
        "dayMove": DEMO_MAX["dayMove"] * _clamp(abs(day_change_pct) / DEMO_DAY_MOVE_FULL_SCALE_PCT),
        "rvol": DEMO_MAX["rvol"] * _clamp((demo_rvol or 0.0) / DEMO_RVOL_FULL_SCALE),
        "oi": float(DEMO_MAX["oi"]) if oi_confirms else 0.0,
        "vwap": float(DEMO_MAX["vwap"]) if vwap_aligned else 0.0,
    }
    parts = {k: round(v, 2) for k, v in parts.items()}
    score = max(0, min(100, round_half_up(sum(parts.values()))))
    return {"score": int(score), "rawScore": int(score), "components": parts, "cap": None, "basis": "DEMO",
            "missing": [] if demo_rvol is not None else ["RVOL20"]}
