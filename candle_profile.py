"""Approximate volume-at-price profile from candles (5M CANDLE EST.).
Each candle's volume is spread uniformly across its low-high range. This is an
estimate, not live market depth and not verified bid/ask volume."""
from __future__ import annotations

from typing import Optional

BASIS = "5M CANDLE EST."


def volume_profile(candles: list[dict], bins: int = 24) -> Optional[dict]:
    cs = [c for c in candles if c.get("v") and c["v"] > 0]
    if not cs or bins <= 0:
        return None
    lo = min(c["l"] for c in cs)
    hi = max(c["h"] for c in cs)
    if hi <= lo:
        return {"low": lo, "high": hi, "bins": [{"low": lo, "high": hi, "volume": float(sum(c["v"] for c in cs))}],
                "poc": lo, "basis": BASIS}
    width = (hi - lo) / bins
    vols = [0.0] * bins
    for c in cs:
        span = c["h"] - c["l"]
        if span <= 0:
            idx = min(bins - 1, int((c["c"] - lo) / width))
            vols[idx] += c["v"]
            continue
        first = max(0, int((c["l"] - lo) / width))
        last = min(bins - 1, int((c["h"] - lo) / width))
        for b in range(first, last + 1):
            b_lo, b_hi = lo + b * width, lo + (b + 1) * width
            overlap = min(b_hi, c["h"]) - max(b_lo, c["l"])
            if overlap > 0:
                vols[b] += c["v"] * overlap / span
    poc_idx = max(range(bins), key=lambda i: vols[i])
    return {"low": lo, "high": hi,
            "bins": [{"low": round(lo + i * width, 4), "high": round(lo + (i + 1) * width, 4), "volume": round(v, 1)}
                     for i, v in enumerate(vols)],
            "poc": round(lo + (poc_idx + 0.5) * width, 4), "basis": BASIS}


def mini_profile(candles: list[dict], bins: int = 10) -> Optional[list[int]]:
    """Normalised 0..100 bar heights (low price -> high price) for compact cards."""
    p = volume_profile(candles, bins)
    if not p or len(p["bins"]) != bins:
        return None
    mx = max(b["volume"] for b in p["bins"]) or 1.0
    return [int(round(100 * b["volume"] / mx)) for b in p["bins"]]
