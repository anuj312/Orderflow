"""Completed-candle order-block / BOS / FVG / liquidity-sweep heuristics.

Everything here is derived from completed OHLCV candles. None of it is evidence of
real institutional limit orders, executed stops, or exchange-classified aggressor flow.
Candle shape: {"t": epoch_start, "o","h","l","c": float, "v": int|None}
"""
from __future__ import annotations

import math
from statistics import median
from typing import Optional

MIN_COMPLETED_CANDLES = 12
FIRST_ANCHOR_INDEX = 8
STRUCTURE_LOOKBACK = 9
IMPULSE_WINDOW = 3
MIN_DISPLACEMENT = 1.15
MAX_ZONE_AGE_BARS = 36
READY_MIN_SCORE = 60
INVALID_SCORE_CAP = 25
EPSILON_RANGE_FRACTION = 0.10
SWEEP_LOOKBACK = 5
STALE_GRACE_SECONDS = 120
DELTA_SOURCE = ("UNAVAILABLE: Kite quotes/candles carry no aggressor-side trade classification. "
                "Detail view shows only a candle close-location volume proxy.")

REASON_NO_ZONE = "Waiting for completed candles and a confirmed structure break."
REASON_INVALID = "Zone invalidated by a completed close through it; wait for a new structure break."
REASON_EXPIRED = "Order block is too old; wait for a new structure break."
REASON_STALE = "Latest completed candle is not recent; waiting for fresh candles."
REASON_READY = "Recent retest rejection with 15m trend agreement (heuristic, not an instruction)."
REASON_FRESH = "Fresh zone identified; awaiting first retest and rejection close."
REASON_RETEST_NO_CONFIRM = "Retest lacks a recent confirmation; wait for a rejection close from the zone."
REASON_REJECTION_UNALIGNED = "Recent rejection, but 15m trend or quality score does not confirm."
REASON_FEED_NOT_LIVE = "Feed is not LIVE; READY suppressed until fresh exchange data confirms."


def _range(c: dict) -> float:
    return c["h"] - c["l"]


def find_candidates(candles: list[dict]) -> list[dict]:
    """All anchor+BOS candidates (both directions) in chronological order."""
    n = len(candles)
    ranges = [_range(c) for c in candles]
    out: list[dict] = []
    for i in range(FIRST_ANCHOR_INDEX, n - 1):
        a = candles[i]
        prior = candles[max(0, i - STRUCTURE_LOOKBACK):i]
        if not prior:
            continue
        s_high = max(c["h"] for c in prior)
        s_low = min(c["l"] for c in prior)
        for direction in ("BULLISH", "BEARISH"):
            if direction == "BULLISH":
                if not a["c"] < a["o"]:
                    continue
                z_low, z_high = a["l"], a["o"]
            else:
                if not a["c"] > a["o"]:
                    continue
                z_low, z_high = a["o"], a["h"]
            if not z_high > z_low:
                continue
            for j in range(i + 1, min(i + 1 + IMPULSE_WINDOW, n)):
                b = candles[j]
                if direction == "BULLISH":
                    broke = b["c"] > s_high and b["c"] > b["o"]
                else:
                    broke = b["c"] < s_low and b["c"] < b["o"]
                if not broke:
                    continue
                ref = ranges[max(0, j - STRUCTURE_LOOKBACK):j]
                med = median(ref) if ref else 0.0
                if med <= 0:
                    continue
                disp = abs(b["c"] - b["o"]) / med
                if disp >= MIN_DISPLACEMENT:
                    out.append({"direction": direction, "anchorIdx": i, "breakIdx": j,
                                "zoneLow": z_low, "zoneHigh": z_high, "displacement": round(disp, 4),
                                "structureLevel": s_high if direction == "BULLISH" else s_low})
                    break
    return out


def select_candidate(cands: list[dict]) -> Optional[dict]:
    """Most recent structure break, then most recent anchor. Never swaps a latest
    (possibly invalid) zone for an older favourable one."""
    if not cands:
        return None
    return max(cands, key=lambda c: (c["breakIdx"], c["anchorIdx"]))


def find_fvg(candles: list[dict], direction: str, break_idx: int) -> Optional[dict]:
    n = len(candles)
    for first in (break_idx - 1, break_idx - 2):
        third = first + 2
        if first < 0 or third >= n:
            continue
        c1, c3 = candles[first], candles[third]
        if direction == "BULLISH" and c3["l"] > c1["h"]:
            return {"firstIdx": first, "thirdIdx": third, "low": c1["h"], "high": c3["l"],
                    "t1": c1["t"], "t3": c3["t"]}
        if direction == "BEARISH" and c3["h"] < c1["l"]:
            return {"firstIdx": first, "thirdIdx": third, "low": c3["h"], "high": c1["l"],
                    "t1": c1["t"], "t3": c3["t"]}
    return None


def find_sweep(candles: list[dict], direction: str, anchor_idx: int, break_idx: int) -> Optional[dict]:
    found = None
    for k in range(max(1, anchor_idx - 3), min(len(candles), break_idx + 1)):
        look = candles[max(0, k - SWEEP_LOOKBACK):k]
        if not look:
            continue
        c = candles[k]
        if direction == "BULLISH":
            level = min(x["l"] for x in look)
            if c["l"] < level and c["c"] > level:
                found = {"idx": k, "t": c["t"], "level": level, "extreme": c["l"]}
        else:
            level = max(x["h"] for x in look)
            if c["h"] > level and c["c"] < level:
                found = {"idx": k, "t": c["t"], "level": level, "extreme": c["h"]}
    return found


def scan_retests(candles: list[dict], cand: dict, eps: float) -> dict:
    zl, zh, d = cand["zoneLow"], cand["zoneHigh"], cand["direction"]
    retests = 0
    in_touch = False
    last_rejection = None
    invalid_idx = None
    touches: list[int] = []
    for k in range(cand["breakIdx"] + 1, len(candles)):
        c = candles[k]
        if d == "BULLISH" and c["c"] < zl - eps:
            invalid_idx = k
            break
        if d == "BEARISH" and c["c"] > zh + eps:
            invalid_idx = k
            break
        overlap = c["l"] <= zh and c["h"] >= zl
        if overlap:
            if not in_touch:
                retests += 1
                touches.append(k)
            in_touch = True
            if d == "BULLISH" and c["c"] > c["o"] and c["c"] > zh:
                last_rejection = k
            if d == "BEARISH" and c["c"] < c["o"] and c["c"] < zl:
                last_rejection = k
        else:
            in_touch = False
    return {"retests": retests, "lastRejectionIdx": last_rejection, "invalidIdx": invalid_idx, "touches": touches}


def htf_trend(htf_closes: Optional[list[float]]) -> Optional[str]:
    if not htf_closes or len(htf_closes) < 5:
        return None
    last = htf_closes[-1]
    avg5 = sum(htf_closes[-5:]) / 5.0
    three_back = htf_closes[-4]
    if last > avg5 and last > three_back:
        return "BULLISH"
    if last < avg5 and last < three_back:
        return "BEARISH"
    return None


def _no_zone(reason: str, basis: str) -> dict:
    return {"obQualityScore": None, "obState": "WAIT", "obDirection": None, "obReason": reason,
            "obBreakdown": None, "obZone": None, "obFvg": None, "obSweep": None, "obBos": None,
            "obBasis": basis, "obDeltaSource": DELTA_SOURCE, "obRetests": None, "obAgeBars": None,
            "obHtfTrend": None, "zones": []}


def analyze_order_blocks(candles: list[dict], *, htf_closes: Optional[list[float]] = None,
                         rvol20: Optional[float] = None, vwap: Optional[float] = None,
                         ltp: Optional[float] = None, now_epoch: Optional[float] = None,
                         tf_minutes: int = 5, feed_live: bool = True, demo: bool = False,
                         include_zones: bool = False) -> dict:
    """candles must be COMPLETED candles of ONE session, chronological."""
    basis = f"{tf_minutes}m completed candles · current session" + (" · DEMO synthetic" if demo else "")
    n = len(candles)
    if n < MIN_COMPLETED_CANDLES:
        return _no_zone(REASON_NO_ZONE, basis)
    cands = find_candidates(candles)
    sel = select_candidate(cands)
    if sel is None:
        return _no_zone(REASON_NO_ZONE, basis)

    ranges = [_range(c) for c in candles]
    eps = EPSILON_RANGE_FRACTION * (median(ranges) if ranges else 0.0)
    d = sel["direction"]
    rt = scan_retests(candles, sel, eps)
    fvg = find_fvg(candles, d, sel["breakIdx"])
    sweep = find_sweep(candles, d, sel["anchorIdx"], sel["breakIdx"])
    trend = htf_trend(htf_closes)
    impulse = candles[sel["breakIdx"]]

    # --- quality breakdown
    bos_pts = min(25.0, 12.0 + 13.0 * min(1.0, sel["displacement"] / 2.5))
    fresh_pts = 15.0 if rt["retests"] == 0 else (11.0 if rt["retests"] == 1 else 4.0)
    htf_pts = 15.0 if trend == d else 0.0
    ref_vols = [c["v"] for c in candles[max(0, sel["anchorIdx"] - 8):sel["anchorIdx"] + 1]
                if c.get("v") is not None and c["v"] > 0]
    vol_basis = None
    vol_ratio = None
    if len(ref_vols) >= 4 and impulse.get("v") is not None:
        vol_ratio = impulse["v"] / (sum(ref_vols) / len(ref_vols))
        vol_pts = 15.0 * min(1.0, vol_ratio / 2.5)
        vol_basis = "IMPULSE_VS_ANCHOR_AREA"
    elif rvol20 is not None:
        vol_pts = 12.0 * min(1.0, rvol20 / 3.0)
        vol_basis = "RVOL20_FALLBACK"
    else:
        vol_pts = 0.0
        vol_basis = "UNAVAILABLE"
    fvg_pts = 10.0 if fvg else 0.0
    recent = rt["lastRejectionIdx"] is not None and rt["lastRejectionIdx"] >= n - 2
    retest_pts = 10.0 if recent else 0.0
    sweep_pts = 6.0 if sweep else 0.0
    vwap_ok = None
    if vwap is not None and ltp is not None:
        vwap_ok = ltp >= vwap if d == "BULLISH" else ltp <= vwap
    vwap_pts = 4.0 if vwap_ok else 0.0
    breakdown = {"bos": round(bos_pts, 2), "freshness": fresh_pts, "htf": htf_pts, "volume": round(vol_pts, 2),
                 "fvg": fvg_pts, "retest": retest_pts, "sweep": sweep_pts, "vwap": vwap_pts,
                 "volumeBasis": vol_basis, "impulseVolumeRatio": round(vol_ratio, 3) if vol_ratio else None}
    total = bos_pts + fresh_pts + htf_pts + vol_pts + fvg_pts + retest_pts + sweep_pts + vwap_pts
    score = int(min(100, math.floor(total + 0.5)))  # half-up, matches MOM rounding

    age = (n - 1) - sel["breakIdx"]
    last = candles[-1]
    stale = False
    if now_epoch is not None:
        last_end = last["t"] + tf_minutes * 60
        stale = (now_epoch - last_end) > tf_minutes * 60 + STALE_GRACE_SECONDS

    if rt["invalidIdx"] is not None:
        state, reason, score = "INVALID", REASON_INVALID, min(score, INVALID_SCORE_CAP)
    elif age > MAX_ZONE_AGE_BARS:
        state, reason = "WAIT", REASON_EXPIRED
    elif stale:
        state, reason = "WAIT", REASON_STALE
    elif recent and trend == d and score >= READY_MIN_SCORE:
        state, reason = "READY", REASON_READY
    elif rt["retests"] == 0:
        state, reason = "WAIT", REASON_FRESH
    elif recent:
        state, reason = "WAIT", REASON_REJECTION_UNALIGNED
    else:
        state, reason = "WAIT", REASON_RETEST_NO_CONFIRM
    if state == "READY" and not feed_live and not demo:
        state, reason = "WAIT", REASON_FEED_NOT_LIVE

    result = {
        "obQualityScore": score,
        "obState": state,
        "obDirection": d,
        "obReason": reason,
        "obBreakdown": breakdown,
        "obZone": {"low": sel["zoneLow"], "high": sel["zoneHigh"], "anchorIdx": sel["anchorIdx"],
                   "anchorT": candles[sel["anchorIdx"]]["t"], "breakIdx": sel["breakIdx"],
                   "breakT": impulse["t"], "invalidIdx": rt["invalidIdx"],
                   "invalidT": candles[rt["invalidIdx"]]["t"] if rt["invalidIdx"] is not None else None},
        "obFvg": fvg,
        "obSweep": sweep,
        "obBos": {"idx": sel["breakIdx"], "t": impulse["t"], "level": sel["structureLevel"],
                  "displacement": sel["displacement"]},
        "obBasis": basis,
        "obDeltaSource": DELTA_SOURCE,
        "obRetests": rt["retests"],
        "obAgeBars": age,
        "obExpired": age > MAX_ZONE_AGE_BARS,
        "obStaleCandles": stale,
        "obHtfTrend": trend,
        "zones": [],
    }
    if include_zones:
        result["zones"] = zone_overlays(candles, cands, sel, eps)
    return result


def zone_overlays(candles: list[dict], cands: list[dict], sel: dict, eps: float, limit: int = 6) -> list[dict]:
    """Chart overlays: the selected zone plus a few older ones, which are display-only."""
    seen = set()
    out = []
    for c in sorted(cands, key=lambda c: (c["breakIdx"], c["anchorIdx"]), reverse=True):
        key = (c["direction"], c["anchorIdx"])
        if key in seen:
            continue
        seen.add(key)
        rt = scan_retests(candles, c, eps)
        end_idx = rt["invalidIdx"] if rt["invalidIdx"] is not None else len(candles) - 1
        out.append({"direction": c["direction"], "low": c["zoneLow"], "high": c["zoneHigh"],
                    "anchorT": candles[c["anchorIdx"]]["t"], "breakT": candles[c["breakIdx"]]["t"],
                    "endT": candles[end_idx]["t"], "invalid": rt["invalidIdx"] is not None,
                    "selected": c is sel, "expired": (len(candles) - 1 - c["breakIdx"]) > MAX_ZONE_AGE_BARS})
        if len(out) >= limit:
            break
    return out
