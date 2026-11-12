"""Adaptive bullish/bearish Top 6 ordering by fresh momentum."""
from __future__ import annotations

from typing import Optional

STATE_MODIFIERS = {
    "ACTIVE": 12.0,
    "CONSOLIDATING": -8.0,
    "FADING": -24.0,
    "WARMING_UP": -5.0,
    "STALE": -45.0,
}
DEFAULT_STATE_MODIFIER = -12.0
SESSION_BONUS_CAP = 8.0
OI_CHANGE_CAP_PCT = 25.0
MIN_SESSIONS_FOR_HISTORICAL = 14


def side_of(day_change_pct: float) -> str:
    return "bullish" if day_change_pct >= 0 else "bearish"


def adaptive_rank_score(rec: dict, demo: bool) -> dict:
    mom = rec.get("mom") or 0
    day = rec.get("dayChangePct") or 0.0
    if demo:
        bonus = min(8.0, abs(day) * 0.75)
        return {"adaptiveRankScore": round(max(0.0, mom + bonus), 2), "sessionMovePct": rec.get("sessionMovePct"),
                "sessionBonus": round(bonus, 3), "stateModifier": 0.0, "volumeBonus": 0.0, "rankBasis": "DEMO"}
    session_move = rec.get("sessionMovePct")
    bonus = min(SESSION_BONUS_CAP, abs(session_move) * 1.4) if session_move is not None else 0.0
    state = rec.get("momentumState")
    modifier = STATE_MODIFIERS.get(state, DEFAULT_STATE_MODIFIER)
    vol_bonus = 3.0 if state == "ACTIVE" and rec.get("volumeConfirmed") else 0.0
    score = round(max(0.0, mom + bonus + modifier + vol_bonus), 2)
    return {"adaptiveRankScore": score, "sessionMovePct": session_move, "sessionBonus": round(bonus, 3),
            "stateModifier": modifier, "volumeBonus": vol_bonus, "rankBasis": "LIVE"}


def historical_score(rec: dict) -> tuple[float, str]:
    day = abs(rec.get("dayChangePct") or 0.0)
    rvol = rec.get("rvol") or 0.0
    oi = rec.get("oiChangePct")
    oi_pts = 0.12 * max(0.0, max(-OI_CHANGE_CAP_PCT, min(OI_CHANGE_CAP_PCT, oi))) if oi is not None else 0.0
    adr = rec.get("adr20")
    if (rec.get("historySessions") or 0) >= MIN_SESSIONS_FOR_HISTORICAL and adr:
        s = (22.0 * min(5.0, day / adr) + 9.0 * min(4.0, rvol)
             + (7.0 if rec.get("breakout20") else 0.0)
             + (5.0 if rec.get("vwapAligned") else 0.0) + oi_pts)
        return round(s, 3), "HISTORICAL_20D"
    s = 10.0 * min(5.0, day) + 9.0 * min(4.0, rvol) + oi_pts
    return round(s, 3), "FALLBACK_PRICE_RVOL_OI_WHILE_HISTORY_WARMS"


def rank_records(records: list[dict], demo: bool) -> dict[str, list[dict]]:
    """Annotate records in place and return sorted bullish/bearish lists (all records)."""
    sides: dict[str, list[dict]] = {"bullish": [], "bearish": []}
    for rec in records:
        if rec.get("dayChangePct") is None:
            continue
        rec.update(adaptive_rank_score(rec, demo))
        hs, basis = historical_score(rec)
        rec["historicalScore"] = hs
        rec["historicalBasis"] = basis
        sides[side_of(rec["dayChangePct"])].append(rec)
    for side, lst in sides.items():
        # (adaptiveRankScore, historicalScore) descending; symbol ascending only as a
        # deterministic last resort so equal scores never reorder between publishes.
        lst.sort(key=lambda r: (-r["adaptiveRankScore"], -r["historicalScore"], r["symbol"]))
        total = len(lst)
        for i, rec in enumerate(lst, 1):
            rec["rank"] = i
            rec["rankOf"] = total
            rec["side"] = side
    return sides
