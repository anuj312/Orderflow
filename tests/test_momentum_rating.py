import pytest

from momentum_rating import demo_momentum_score, momentum_score


def fresh(state="ACTIVE", eligible=True, d=(3, 3, 3, 3), t=(0.1, 0.22, 0.32, 0.48), rvr=3.0):
    win = {}
    for n, dn, tn in zip((5, 15, 30, 60), d, t):
        win[str(n)] = {"directedPct": None if dn is None else dn * tn, "threshold": tn}
    return {"momentumState": state, "pickEligible": eligible, "windows": win, "recentVolumeRatio": rvr}


def test_exact_component_weights_sum_to_100():
    m = momentum_score(fresh(), rvol20=4.0, vwap_aligned=True, breakout_matches=True, history_status="READY")
    assert m["components"] == {"d5": 25, "d15": 20, "d30": 15, "d60": 10, "rvol": 15, "volAccel": 5, "vwap": 7, "breakout": 3}
    assert m["score"] == 100 and m["basis"] == "LIVE_20D"


def test_partial_components_scale_linearly():
    m = momentum_score(fresh(d=(1.5, 1.5, 0, 0), rvr=1.5), rvol20=2.0, vwap_aligned=False, breakout_matches=False,
                       history_status="READY")
    assert m["components"]["d5"] == pytest.approx(12.5) and m["components"]["d15"] == pytest.approx(10)
    assert m["components"]["rvol"] == pytest.approx(7.5) and m["components"]["volAccel"] == pytest.approx(2.5)
    assert m["score"] == 33


def test_missing_windows_and_context_get_zero_and_are_listed():
    m = momentum_score(fresh(d=(3, 3, None, None), rvr=None), rvol20=None, vwap_aligned=None, breakout_matches=False,
                       history_status="PARTIAL")
    assert m["components"]["d30"] == 0 and m["components"]["rvol"] == 0
    assert {"30m", "60m", "RVOL20", "volume acceleration", "VWAP"} <= set(m["missing"])
    assert m["basis"] == "LIVE_PARTIAL_HISTORY"


@pytest.mark.parametrize("state,eligible,cap", [("FADING", False, 25), ("CONSOLIDATING", False, 42), ("ACTIVE", False, 55)])
def test_state_caps(state, eligible, cap):
    m = momentum_score(fresh(state=state, eligible=eligible), rvol20=4.0, vwap_aligned=True, breakout_matches=True,
                       history_status="READY")
    assert m["score"] == cap and m["rawScore"] == 100


def test_missing_short_window_cap_35_and_warmup_basis():
    m = momentum_score(fresh(state="WARMING_UP", d=(None, 3, 3, 3)), rvol20=4.0, vwap_aligned=True,
                       breakout_matches=True, history_status="READY")
    assert m["score"] == 35 and m["basis"] == "LIVE_WARMUP"


def test_stale_score_is_none():
    m = momentum_score(fresh(state="STALE"), rvol20=4.0, vwap_aligned=True, breakout_matches=True, history_status="READY")
    assert m["score"] is None and m["basis"] == "STALE"


def test_negative_directed_moves_give_zero_not_negative():
    m = momentum_score(fresh(state="CONSOLIDATING", d=(-2, -2, -2, -2), rvr=0), rvol20=0, vwap_aligned=False,
                       breakout_matches=False, history_status="READY")
    assert all(v >= 0 for v in m["components"].values()) and m["score"] == 0 and m["basis"] == "LIVE_WATCH"


def test_demo_formula_is_separate_and_labelled():
    m = demo_momentum_score(3.0, 3.0, True, True)
    assert m["components"] == {"dayMove": 65, "rvol": 21, "oi": 4, "vwap": 10} and m["score"] == 100
    assert m["basis"] == "DEMO"
    assert demo_momentum_score(-1.5, None, False, False)["score"] == 33  # 32.5 rounds half-up
