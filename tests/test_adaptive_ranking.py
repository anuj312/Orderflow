import pytest

from adaptive_ranking import adaptive_rank_score, historical_score, rank_records


def rec(sym, day=1.0, mom=50, state="ACTIVE", vc=True, session=0.5, hs=20, adr=2.0, rvol=1.5, **kw):
    r = {"symbol": sym, "dayChangePct": day, "mom": mom, "momentumState": state, "volumeConfirmed": vc,
         "sessionMovePct": session, "historySessions": hs, "adr20": adr, "rvol": rvol}
    r.update(kw)
    return r


@pytest.mark.parametrize("state,mod", [("ACTIVE", 12), ("CONSOLIDATING", -8), ("FADING", -24), ("WARMING_UP", -5),
                                       ("STALE", -45), ("SOMETHING", -12)])
def test_state_modifiers(state, mod):
    s = adaptive_rank_score(rec("A", mom=60, state=state, vc=False, session=0.0), demo=False)
    assert s["stateModifier"] == mod
    assert s["adaptiveRankScore"] == max(0, 60 + mod)


def test_session_bonus_capped_at_8_and_volume_bonus():
    s = adaptive_rank_score(rec("A", mom=50, session=20.0), demo=False)
    assert s["sessionBonus"] == 8.0 and s["volumeBonus"] == 3.0
    assert s["adaptiveRankScore"] == 50 + 8 + 12 + 3
    small = adaptive_rank_score(rec("A", mom=50, session=-1.0, vc=False), demo=False)
    assert small["sessionBonus"] == pytest.approx(1.4) and small["volumeBonus"] == 0


def test_score_never_negative_and_missing_mom_is_zero():
    assert adaptive_rank_score(rec("A", mom=None, state="STALE"), demo=False)["adaptiveRankScore"] == 0


def test_demo_rank_formula():
    s = adaptive_rank_score(rec("A", mom=40, day=-20.0), demo=True)
    assert s["adaptiveRankScore"] == 48 and s["rankBasis"] == "DEMO"


def test_historical_score_and_fallback_label():
    hs, basis = historical_score(rec("A", day=2.0, adr=2.0, rvol=2.0, breakout20="ABOVE_20D_HIGH", vwapAligned=True,
                                     oiChangePct=50.0))
    assert basis == "HISTORICAL_20D"
    assert hs == pytest.approx(22 * 1 + 9 * 2 + 7 + 5 + 0.12 * 25)
    hs2, basis2 = historical_score(rec("A", hs=5))
    assert basis2.startswith("FALLBACK")


def test_split_sort_tie_break_and_rank_of_total():
    rs = [rec("A", mom=50, day=1.0, adr=4.0), rec("B", mom=50, day=3.0, adr=4.0), rec("C", mom=70, day=0.0),
          rec("D", mom=30, day=-1.0), rec("E", mom=80, day=-2.0)]
    sides = rank_records(rs, demo=False)
    assert [r["symbol"] for r in sides["bullish"]] == ["C", "B", "A"]   # B beats A on historicalScore tie-break
    assert [r["symbol"] for r in sides["bearish"]] == ["E", "D"]
    assert sides["bullish"][1]["rank"] == 2 and sides["bullish"][1]["rankOf"] == 3
    assert sides["bullish"][0]["side"] == "bullish"  # 0.0% counts as bullish


def test_early_spike_cannot_dominate_through_session_move():
    spike = rec("SPIKE", mom=20, session=15.0, state="CONSOLIDATING")
    fresh = rec("FRESH", mom=45, session=0.5, state="ACTIVE")
    sides = rank_records([spike, fresh], demo=False)
    assert sides["bullish"][0]["symbol"] == "FRESH"
