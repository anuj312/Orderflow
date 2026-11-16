import random

import pytest

from helpers import BEAR_HTF, BULL_HTF, bullish_ob_rows, candles_from, mirror_rows
from orderblock_intelligence import (REASON_EXPIRED, REASON_FEED_NOT_LIVE, REASON_FRESH, REASON_INVALID,
                                     REASON_NO_ZONE, REASON_STALE, analyze_order_blocks, find_candidates, htf_trend,
                                     select_candidate)


def run(rows, htf=BULL_HTF, vwap=100.5, ltp=100.9, live=True, demo=False, now_offset=310, **kw):
    cs = candles_from(rows)
    return analyze_order_blocks(cs, htf_closes=htf, rvol20=1.5, vwap=vwap, ltp=ltp,
                                now_epoch=cs[-1]["t"] + now_offset, tf_minutes=5, feed_live=live, demo=demo, **kw)


def test_bullish_anchor_bos_fvg_sweep_retest_ready_with_exact_breakdown():
    r = run(bullish_ob_rows())
    assert r["obDirection"] == "BULLISH" and r["obState"] == "READY"
    assert r["obZone"]["low"] == 99.3 and r["obZone"]["high"] == 100.3      # [anchor low, anchor open]
    assert r["obZone"]["anchorIdx"] == 9 and r["obBos"]["idx"] == 10
    assert r["obBos"]["displacement"] == pytest.approx(1.7)
    assert r["obFvg"]["firstIdx"] == 9 and r["obFvg"]["low"] == 100.4 and r["obFvg"]["high"] == 101.5
    assert r["obSweep"]["idx"] == 9
    b = r["obBreakdown"]
    assert b["bos"] == pytest.approx(20.84) and b["freshness"] == 11 and b["htf"] == 15 and b["volume"] == 15
    assert b["fvg"] == 10 and b["retest"] == 10 and b["sweep"] == 6 and b["vwap"] == 4
    assert r["obQualityScore"] == 92
    assert "aggressor" in r["obDeltaSource"].lower()


def test_bearish_mirror_is_detected_symmetrically():
    r = run(mirror_rows(bullish_ob_rows()), htf=BEAR_HTF, vwap=99.5, ltp=99.1)
    assert r["obDirection"] == "BEARISH" and r["obState"] == "READY" and r["obQualityScore"] == 92
    assert r["obZone"]["low"] == pytest.approx(99.7) and r["obZone"]["high"] == pytest.approx(100.7)  # [open, high]


def test_requires_12_completed_candles():
    r = run(bullish_ob_rows()[:11])
    assert r["obQualityScore"] is None and r["obState"] == "WAIT" and r["obZone"] is None
    assert r["obReason"] == REASON_NO_ZONE


def test_fresh_zone_without_retest_waits():
    r = run(bullish_ob_rows()[:12])
    assert r["obState"] == "WAIT" and r["obReason"] == REASON_FRESH and r["obBreakdown"]["freshness"] == 15


def test_displacement_below_threshold_is_not_bos():
    rows = bullish_ob_rows()
    rows[10] = (99.95, 100.95, 99.9, 100.9, 3000)   # body 0.95 / median 1.0 < 1.15
    rows[11] = (100.9, 101.0, 100.6, 100.95, 1000)
    assert not [c for c in find_candidates(candles_from(rows)) if c["direction"] == "BULLISH"]


def test_close_through_zone_invalidates_and_caps_score():
    rows = bullish_ob_rows()[:13] + [(99.3, 99.4, 98.9, 99.0, 1000)]
    r = run(rows)
    assert r["obState"] == "INVALID" and r["obReason"] == REASON_INVALID and r["obQualityScore"] <= 25


def test_zone_expires_after_36_bars_and_cannot_be_ready():
    rows = bullish_ob_rows() + [(101.0, 101.3, 100.9, 101.2, 1000)] * 34
    r = run(rows)
    assert r["obAgeBars"] > 36 and r["obState"] == "WAIT" and r["obReason"] == REASON_EXPIRED


def test_stale_latest_candle_blocks_ready():
    r = run(bullish_ob_rows(), now_offset=310 + 900)
    assert r["obState"] == "WAIT" and r["obReason"] == REASON_STALE


def test_htf_disagreement_blocks_ready():
    r = run(bullish_ob_rows(), htf=BEAR_HTF)
    assert r["obState"] == "WAIT" and r["obBreakdown"]["htf"] == 0


def test_not_live_feed_downgrades_ready_but_demo_keeps_label():
    assert run(bullish_ob_rows(), live=False)["obReason"] == REASON_FEED_NOT_LIVE
    assert run(bullish_ob_rows(), live=False, demo=True)["obState"] == "READY"


def test_most_recent_break_wins_even_if_older_zone_scores_better():
    cands = [{"breakIdx": 10, "anchorIdx": 9, "q": "old"}, {"breakIdx": 20, "anchorIdx": 17, "q": "new"},
             {"breakIdx": 20, "anchorIdx": 18, "q": "newest"}]
    assert select_candidate(cands)["q"] == "newest"


def test_htf_trend_rules():
    assert htf_trend(BULL_HTF) == "BULLISH" and htf_trend(BEAR_HTF) == "BEARISH"
    assert htf_trend([1, 2, 3, 4]) is None and htf_trend([1, 1, 1, 1, 1]) is None


def test_volume_fallback_to_rvol_when_candle_volumes_missing():
    rows = [(o, h, l, c, None) for (o, h, l, c, v) in bullish_ob_rows()]
    r = run(rows)
    assert r["obBreakdown"]["volumeBasis"] == "RVOL20_FALLBACK"
    assert r["obBreakdown"]["volume"] == pytest.approx(12 * 1.5 / 3)


@pytest.mark.parametrize("seed", range(25))
def test_quality_score_bounded_on_random_walks(seed):
    rnd = random.Random(seed)
    p, rows = 100.0, []
    for _ in range(60):
        o = p
        c = o * (1 + rnd.gauss(0, 0.004))
        rows.append((o, max(o, c) * (1 + rnd.random() * 0.002), min(o, c) * (1 - rnd.random() * 0.002), c, rnd.randint(1, 9000)))
        p = c
    r = run(rows, ltp=p, vwap=100.0)
    s = r["obQualityScore"]
    assert s is None or 0 <= s <= 100
    if r["obState"] == "READY":
        assert s >= 60 and r["obHtfTrend"] == r["obDirection"] and not r["obExpired"]
