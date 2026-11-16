from datetime import date

from stable_ob_picks import StableOBPicks, eligibility

D = date(2026, 11, 10)


def rec(sym, side="bullish", ob=70, state="READY", mom=60, rank=50.0, eligible=True, stale=False,
        reason="Recent retest rejection with 15m trend agreement", direction=None, momentum="ACTIVE"):
    return {"symbol": sym, "side": side, "obDirection": direction or ("BULLISH" if side == "bullish" else "BEARISH"),
            "obQualityScore": ob, "obState": state, "obZone": {"low": 1.0, "high": 2.0}, "mom": mom,
            "adaptiveRankScore": rank, "pickEligible": eligible, "stale": stale, "momentumState": momentum,
            "obReason": reason, "obExpired": False, "obStaleCandles": False, "ltp": 10.0, "dayChangePct": 1.0}


def upd(p, leaders, records=None, rev=1, live=True, demo=False, d=D):
    records = records if records is not None else {r["symbol"]: r for r in leaders}
    return p.update(leaders={"bullish": leaders, "bearish": []}, records=records, market_revision=rev,
                    market_live=live, demo=demo, session_date=d)


def syms(out):
    return [s.get("symbol") for s in out["bullish"]["slots"] if not s["empty"]]


def test_empty_slots_are_never_fabricated():
    out = upd(StableOBPicks(), [rec("A", state="WAIT", eligible=False), rec("B", ob=None)])
    assert syms(out) == []
    assert all(s["empty"] and s["pickStatus"] == "WAITING FOR ELIGIBLE OB" for s in out["bullish"]["slots"])
    assert len(out["bullish"]["slots"]) == 3


def test_eligibility_rules():
    ok = lambda r, **kw: eligibility(r, "bullish", market_live=kw.get("live", True), demo=kw.get("demo", False),
                                     strict=kw.get("strict", False))[0]
    assert ok(rec("A"))
    assert not ok(rec("A", state="INVALID"))
    assert not ok(rec("A", stale=True))
    assert not ok(rec("A", direction="BEARISH"))
    assert not ok(rec("A", side="bearish"))
    assert not ok(rec("A"), live=False)
    assert ok(rec("A"), live=False, demo=True)
    assert not ok(rec("A", eligible=False), strict=True)
    # WAIT -> WATCH only in real LIVE mode with all safeguards
    watch = rec("W", state="WAIT", ob=65, reason="Fresh zone identified; awaiting first retest")
    assert eligibility(watch, "bullish", market_live=True, demo=False)[1] == "WATCH"
    assert not ok(watch, demo=True, live=False)
    assert not ok(dict(watch, pickEligible=False))
    assert not ok(dict(watch, obQualityScore=59))
    assert not ok(dict(watch, obReason="Order block is too old; wait for a new structure break."))
    assert not ok(dict(watch, obExpired=True))


def test_sorted_by_ob_then_mom_then_rank():
    out = upd(StableOBPicks(), [rec("A", ob=70, mom=10), rec("B", ob=80), rec("C", ob=70, mom=90), rec("D", ob=60)])
    assert set(syms(out)) == {"B", "C", "A"}
    assert syms(out) == ["B", "C", "A"]


def test_replacement_requires_three_distinct_market_revisions():
    p = StableOBPicks()
    inc = [rec("A", ob=70), rec("B", ob=71), rec("C", ob=72)]
    upd(p, inc, rev=1)
    leaders = inc + [rec("X", ob=90)]
    out = upd(p, leaders, rev=2)
    assert out["bullish"]["pending"]["count"] == 1 and out["bullish"]["pending"]["incumbent"] == "A"
    for _ in range(5):  # repeated publishes / polls on the same revision never advance
        out = upd(p, leaders, rev=2)
    assert out["bullish"]["pending"]["count"] == 1
    p.peek(records={r["symbol"]: r for r in leaders}, market_live=True, demo=False)
    out = upd(p, leaders, rev=3)
    assert "X" not in syms(out) and out["bullish"]["pending"]["count"] == 2
    out = upd(p, leaders, rev=4)
    assert "X" in syms(out) and "A" not in syms(out) and out["bullish"]["pending"] is None
    assert out["bullish"]["lastReplacement"] == {"in": "X", "out": "A", "revision": 4}


def test_pending_resets_when_challenger_stops_beating_incumbent():
    p = StableOBPicks()
    inc = [rec("A", ob=70), rec("B", ob=71), rec("C", ob=72)]
    upd(p, inc, rev=1)
    upd(p, inc + [rec("X", ob=90)], rev=2)
    out = upd(p, inc + [rec("X", ob=65)], rev=3)
    assert out["bullish"]["pending"] is None and "X" not in syms(out)


def test_incumbent_outside_top6_scored_by_current_record_and_missing_data_kept():
    p = StableOBPicks()
    inc = [rec("A", ob=70), rec("B", ob=71), rec("C", ob=72)]
    upd(p, inc, rev=1)
    records = {"B": inc[1], "C": inc[2]}  # A disappeared from data
    out = upd(p, [inc[1], inc[2]], records=records, rev=2)
    a = [s for s in out["bullish"]["slots"] if s.get("symbol") == "A"][0]
    assert a["pickStatus"] == "DATA WAIT" and a["obQualityScore"] is None and a["pickPinned"]
    # challenger beats -inf incumbent, still needs three revisions
    leaders = [inc[1], inc[2], rec("X", ob=61)]
    records = {r["symbol"]: r for r in leaders}
    for rev in (3, 4):
        assert "X" not in syms(upd(p, leaders, records=records, rev=rev))
    assert "X" in syms(upd(p, leaders, records=records, rev=5))


def test_direction_change_and_invalid_incumbent_shown_honestly():
    p = StableOBPicks()
    upd(p, [rec("A")], rev=1)
    out = upd(p, [], records={"A": rec("A", side="bearish")}, rev=2)
    s = out["bullish"]["slots"][0]
    assert s["pickDirectionChanged"] and s["pickStatus"] == "DIRECTION CHANGED" and not s["pickConfirmed"]
    out = upd(p, [], records={"A": rec("A", state="INVALID", ob=25)}, rev=3)
    assert out["bullish"]["slots"][0]["pickStatus"] == "INVALID"


def test_confirmed_only_when_live_ready_eligible_and_not_demo():
    p = StableOBPicks()
    assert upd(p, [rec("A")], rev=1)["bullish"]["slots"][0]["pickConfirmed"] is True
    p2 = StableOBPicks()
    s = upd(p2, [rec("A")], rev=1, live=False, demo=True)["bullish"]["slots"][0]
    assert s["pickConfirmed"] is False and s["pickStatus"] == "DEMO"
    p3 = StableOBPicks()
    upd(p3, [rec("A")], rev=1)
    s = upd(p3, [rec("A")], rev=2, live=False)["bullish"]["slots"][0]
    assert s["pickStatus"] == "FEED WAIT" and not s["pickConfirmed"]


def test_session_rollover_clears_locks():
    p = StableOBPicks()
    upd(p, [rec("A")], rev=1)
    out = upd(p, [], records={}, rev=2, d=date(2026, 11, 11))
    assert syms(out) == []


def test_display_order_follows_current_score_without_changing_members():
    p = StableOBPicks()
    upd(p, [rec("A", ob=90), rec("B", ob=80), rec("C", ob=70)], rev=1)
    out = upd(p, [rec("A", ob=60), rec("B", ob=80), rec("C", ob=95)], rev=2)
    assert syms(out) == ["C", "B", "A"]
