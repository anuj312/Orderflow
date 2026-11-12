"""Detail-view numbers: floor pivots, conviction, risk/reward estimate, and a
candle close-location volume-delta PROXY (never exchange-classified aggressor delta)."""
from __future__ import annotations

from typing import Optional

DELTA_PROXY_SOURCE = ("CANDLE CLOSE-LOCATION PROXY: sum(volume*(2*close-high-low)/(high-low)). "
                      "Not exchange-classified aggressor delta.")


def floor_pivots(h: float, l: float, c: float) -> dict:
    p = (h + l + c) / 3.0
    return {"P": round(p, 2), "R1": round(2 * p - l, 2), "S1": round(2 * p - h, 2),
            "R2": round(p + (h - l), 2), "S2": round(p - (h - l), 2),
            "R3": round(h + 2 * (p - l), 2), "S3": round(l - 2 * (h - p), 2)}


def pivots_with_basis(prev_session: Optional[dict], current_candles: list[dict]) -> Optional[dict]:
    if prev_session:
        return {"levels": floor_pivots(prev_session["h"], prev_session["l"], prev_session["c"]),
                "basis": f"Classic floor pivots from previous complete session {prev_session.get('date', '')}".strip()}
    if current_candles:
        h = max(c["h"] for c in current_candles)
        l = min(c["l"] for c in current_candles)
        c = current_candles[-1]["c"]
        return {"levels": floor_pivots(h, l, c),
                "basis": "FALLBACK: current-session extremes (no previous session available); not trade levels"}
    return None


def conviction(mom: Optional[float], ob: Optional[float]) -> Optional[float]:
    if mom is None or ob is None:
        return None
    return round(0.6 * mom + 0.4 * ob, 1)


def risk_reward(direction: Optional[str], ltp: Optional[float], zone: Optional[dict],
                pivots: Optional[dict]) -> Optional[dict]:
    if not direction or not ltp:
        return None
    levels = (pivots or {}).get("levels") or {}
    bull = direction == "BULLISH"
    stop = stop_basis = None
    if zone:
        z_stop = zone["low"] if bull else zone["high"]
        if (bull and z_stop < ltp) or (not bull and z_stop > ltp):
            stop, stop_basis = z_stop, "OB zone " + ("low" if bull else "high")
    if stop is None:
        sup = [v for k, v in levels.items() if (v < ltp if bull else v > ltp)]
        if sup:
            stop = max(sup) if bull else min(sup)
            stop_basis = "nearest pivot " + ("support" if bull else "resistance")
    tgt = [v for v in levels.values() if (v > ltp if bull else v < ltp)]
    target = (min(tgt) if bull else max(tgt)) if tgt else None
    if stop is None or target is None:
        return {"direction": direction, "stop": stop, "stopBasis": stop_basis, "target": target,
                "targetBasis": "nearest pivot reference" if target else None, "rr": None,
                "note": "Insufficient reference levels for an estimate."}
    risk = abs(ltp - stop)
    reward = abs(target - ltp)
    return {"direction": direction, "stop": round(stop, 2), "stopBasis": stop_basis, "target": round(target, 2),
            "targetBasis": "nearest pivot reference", "rr": round(reward / risk, 2) if risk > 0 else None,
            "note": "Estimate from reference levels only; not a trade instruction."}


def delta_proxy(candles: list[dict]) -> Optional[float]:
    total = 0.0
    seen = False
    for c in candles:
        v = c.get("v")
        if not v:
            continue
        rng = c["h"] - c["l"]
        if rng <= 0:
            continue
        total += v * (2 * c["c"] - c["h"] - c["l"]) / rng
        seen = True
    return round(total, 1) if seen else None
