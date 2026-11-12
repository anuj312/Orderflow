"""Sticky Top 3 bullish/bearish OB-qualified picks.

Membership only changes on a *new market-data revision* (an actual exchange/demo data
change). Read-only API calls and periodic publishes with no new data never advance it.
A challenger replaces the weakest incumbent only after beating it on three distinct
market-data revisions. Empty slots stay empty; nothing is fabricated.
"""
from __future__ import annotations

import math
from typing import Optional

SLOTS = 3
REPLACEMENT_REVISIONS = 3
WATCH_MIN_SCORE = 60
WAIT_ADMISSION_PREFIXES = ("Fresh zone identified", "Retest lacks a recent confirmation")
NEG_INF = float("-inf")
SIDE_DIRECTION = {"bullish": "BULLISH", "bearish": "BEARISH"}
STATE_TO_STATUS = {"CONSOLIDATING": "SIDEWAYS", "FADING": "FADING", "WARMING_UP": "WARMUP"}


def _finite(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _side_matches(rec: dict, side: str) -> bool:
    return rec.get("side") == side and rec.get("obDirection") == SIDE_DIRECTION[side]


def eligibility(rec: Optional[dict], side: str, *, market_live: bool, demo: bool,
                strict: bool = False) -> tuple[bool, Optional[str], str]:
    """Returns (eligible, kind READY|WATCH, reason)."""
    if rec is None:
        return False, None, "No current data."
    if not _finite(rec.get("obQualityScore")):
        return False, None, "No numeric OB score."
    if rec.get("stale") or rec.get("momentumState") == "STALE":
        return False, None, "Record is stale."
    if rec.get("obState") == "INVALID" or not rec.get("obZone"):
        return False, None, "OB zone missing or invalidated."
    if not _side_matches(rec, side):
        return False, None, "OB direction does not match side."
    if not (market_live or demo):
        return False, None, "Market feed is not LIVE."
    state = rec.get("obState")
    if state == "READY":
        if strict and not rec.get("pickEligible"):
            return False, None, "STRICT_TOP3_CONFIRMATION: READY also needs ACTIVE momentum with RVOL >= 1."
        return True, "READY", "OB READY: recent retest rejection with 15m trend agreement."
    if state == "WAIT":
        if demo or not market_live:
            return False, None, "WAIT zones are only admitted as WATCH in real LIVE mode."
        reason = rec.get("obReason") or ""
        if (rec.get("pickEligible") and rec["obQualityScore"] >= WATCH_MIN_SCORE
                and not rec.get("obExpired") and not rec.get("obStaleCandles")
                and reason.startswith(WAIT_ADMISSION_PREFIXES)):
            return True, "WATCH", "Unconfirmed WATCH: quality >= 60 with ACTIVE, volume-confirmed momentum."
        return False, None, "WAIT zone does not meet WATCH admission rules."
    return False, None, "OB state not admissible."


def incumbent_score(rec: Optional[dict], side: str) -> float:
    if rec is None or rec.get("obState") == "INVALID" or not _side_matches(rec, side):
        return NEG_INF
    if rec.get("stale") or not _finite(rec.get("obQualityScore")):
        return NEG_INF
    return float(rec["obQualityScore"])


def _sort_key(rec: dict):
    return (rec.get("obQualityScore") or 0, rec.get("mom") or 0, rec.get("adaptiveRankScore") or 0)


class StableOBPicks:
    def __init__(self, strict: bool = False):
        self.strict = strict
        self.session_date = None
        self.last_revision = None
        self.advances = 0
        self._state = {s: {"members": [], "pending": None, "lastReplacement": None} for s in ("bullish", "bearish")}

    def reset(self, session_date) -> None:
        self.session_date = session_date
        self.last_revision = None
        for st in self._state.values():
            st["members"].clear()
            st["pending"] = None
            st["lastReplacement"] = None

    def members(self, side: str) -> list[str]:
        return list(self._state[side]["members"])

    def update(self, *, leaders: dict[str, list[dict]], records: dict[str, dict], market_revision: int,
               market_live: bool, demo: bool, session_date) -> dict:
        if session_date != self.session_date:
            self.reset(session_date)
        advanced = False
        if market_revision != self.last_revision:
            self.last_revision = market_revision
            self.advances += 1
            advanced = True
            for side in ("bullish", "bearish"):
                self._advance(side, leaders.get(side, []), records, market_revision, market_live, demo)
        out = {side: self._render(side, records, market_live, demo) for side in ("bullish", "bearish")}
        out["strict"] = self.strict
        out["advanced"] = advanced
        out["revision"] = self.last_revision
        return out

    def peek(self, *, records: dict[str, dict], market_live: bool, demo: bool) -> dict:
        """Read-only render: never changes membership, pending counts or revision."""
        out = {side: self._render(side, records, market_live, demo) for side in ("bullish", "bearish")}
        out["strict"] = self.strict
        out["advanced"] = False
        out["revision"] = self.last_revision
        return out

    def _advance(self, side, leaders, records, revision, market_live, demo):
        st = self._state[side]
        members = st["members"]
        eligible = [r for r in leaders
                    if eligibility(r, side, market_live=market_live, demo=demo, strict=self.strict)[0]]
        eligible.sort(key=_sort_key, reverse=True)
        for r in eligible:
            if len(members) >= SLOTS:
                break
            if r["symbol"] not in members:
                members.append(r["symbol"])
        challengers = [r for r in eligible if r["symbol"] not in members]
        if len(members) < SLOTS or not challengers:
            st["pending"] = None
            return
        best = challengers[0]
        scores = {m: incumbent_score(records.get(m), side) for m in members}
        weakest = min(reversed(members), key=lambda m: scores[m])
        if float(best["obQualityScore"]) > scores[weakest]:
            p = st["pending"]
            if p and p["challenger"] == best["symbol"] and p["incumbent"] == weakest:
                p["count"] += 1
                p["revisions"].append(revision)
            else:
                p = st["pending"] = {"challenger": best["symbol"], "incumbent": weakest, "count": 1,
                                     "revisions": [revision], "required": REPLACEMENT_REVISIONS}
            p["challengerScore"] = best["obQualityScore"]
            p["incumbentScore"] = None if scores[weakest] == NEG_INF else scores[weakest]
            if p["count"] >= REPLACEMENT_REVISIONS:
                members[members.index(weakest)] = best["symbol"]
                st["lastReplacement"] = {"in": best["symbol"], "out": weakest, "revision": revision}
                st["pending"] = None
        else:
            st["pending"] = None

    def _render(self, side, records, market_live, demo) -> dict:
        st = self._state[side]
        slots = []
        for sym in st["members"]:
            rec = records.get(sym)
            ok, kind, why = eligibility(rec, side, market_live=market_live, demo=demo, strict=self.strict)
            direction_changed = bool(rec) and rec.get("side") is not None and rec.get("side") != side
            confirmed = bool(rec and rec.get("pickEligible") and market_live and not demo
                             and _side_matches(rec, side) and rec.get("obState") == "READY"
                             and not rec.get("stale"))
            if rec is None:
                status = "DATA WAIT"
            elif demo:
                status = "DEMO"
            elif direction_changed:
                status = "DIRECTION CHANGED"
            elif rec.get("obState") == "INVALID":
                status = "INVALID"
            elif not market_live or rec.get("stale"):
                status = "FEED WAIT"
            elif confirmed:
                status = "CONFIRMED"
            else:
                status = STATE_TO_STATUS.get(rec.get("momentumState"), "WATCH")
            score = rec.get("obQualityScore") if rec else None
            slots.append({
                "empty": False,
                "symbol": sym,
                "present": rec is not None,
                "sector": rec.get("sector") if rec else None,
                "ltp": rec.get("ltp") if rec else None,
                "dayChangePct": rec.get("dayChangePct") if rec else None,
                "mom": rec.get("mom") if rec else None,
                "obQualityScore": score,
                "obState": rec.get("obState") if rec else None,
                "obReason": rec.get("obReason") if rec else "Data unavailable; identity retained.",
                "momentumState": rec.get("momentumState") if rec else None,
                "qualifyingReason": why,
                "admissionKind": kind,
                "pickPinned": not ok,
                "pickConfirmed": confirmed,
                "pickStatus": status,
                "pickDirectionChanged": direction_changed,
            })
        slots.sort(key=lambda s: (s["obQualityScore"] if _finite(s["obQualityScore"]) else NEG_INF), reverse=True)
        for i, s in enumerate(slots, 1):
            s["slot"] = i
        while len(slots) < SLOTS:
            slots.append({"empty": True, "slot": len(slots) + 1, "obQualityScore": None,
                          "pickStatus": "WAITING FOR ELIGIBLE OB",
                          "note": "Only qualifying OB scores fill this slot."})
        p = st["pending"]
        return {"slots": slots, "pending": dict(p) if p else None, "lastReplacement": st["lastReplacement"]}
