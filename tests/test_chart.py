import json
import shutil
import subprocess
from datetime import timedelta
from pathlib import Path

import pytest

from candle_profile import mini_profile, volume_profile
from candles import SessionCandles, resample, vwap_series
from helpers import D, at, candles_from
from market_time import epoch, session_open_dt
from stock_detail_metrics import conviction, delta_proxy, floor_pivots, pivots_with_basis, risk_reward

ROOT = Path(__file__).resolve().parents[1]


def full_session(d=D, n=75):
    return candles_from([(100 + i, 101 + i, 99 + i, 100.5 + i, 10) for i in range(n)], d=d)


def test_15m_resample_anchored_at_0915_with_ohlcv_rules():
    bars = resample(full_session(), 15)
    assert len(bars) == 25
    first = bars[0]
    assert first["t"] == epoch(session_open_dt(D))
    assert (first["o"], first["h"], first["l"], first["c"], first["v"]) == (100, 103, 99, 102.5, 30)
    assert bars[1]["t"] - bars[0]["t"] == 900


def test_1h_resample_anchored_0915_1015_not_clock_hours():
    bars = resample(full_session(), 60)
    starts = [b["t"] - epoch(session_open_dt(D)) for b in bars]
    assert starts == [0, 3600, 7200, 10800, 14400, 18000, 21600]   # 09:15, 10:15, ... 15:15
    assert bars[-1]["members"] == 3                                  # 15:15-15:30 partial hour
    assert bars[0]["members"] == 12


def test_no_cross_session_aggregation():
    two = full_session(D)[-2:] + full_session(D + timedelta(days=1))[:2]
    bars = resample(two, 60)
    assert len(bars) == 2 and bars[0]["members"] == 2 and bars[1]["members"] == 2


def test_completeness_respects_now():
    cs = full_session()[:4]
    now = epoch(at(9, 32))
    bars = resample(cs, 15, now)
    assert bars[0]["complete"] is True and bars[1]["complete"] is False


def test_vwap_resets_each_session_and_skips_missing_volume():
    cs = candles_from([(10, 12, 8, 10, 100), (20, 22, 18, 20, 100)]) + \
         candles_from([(30, 33, 27, 30, None), (40, 42, 38, 40, 50)], d=D + timedelta(days=1))
    v = vwap_series(cs)
    assert v[0] == pytest.approx(10) and v[1] == pytest.approx(15)
    assert v[2] is None and v[3] == pytest.approx(40)


def test_session_candles_from_ticks_partial_first_and_volume_deltas():
    sc = SessionCandles(D)
    sc.on_tick(at(9, 21, 30), 100.0, 5000)       # first tick after connect: no volume reference
    sc.on_tick(at(9, 22, 0), 101.0, 5600)
    sc.on_tick(at(9, 21, 50), 50.0, 5700)         # out-of-order: ignored
    sc.on_tick(at(9, 25, 5), 102.0, 6000)
    cs = sc.candles(at(9, 26))
    assert cs[0]["partial"] is True and cs[0]["v"] == 600 and cs[0]["h"] == 101.0 and cs[0]["l"] == 100.0
    assert cs[0]["complete"] is True and cs[1]["complete"] is False and cs[1]["v"] == 400
    assert cs[1]["partial"] is False


def test_merge_history_is_authoritative_and_keeps_live_close():
    sc = SessionCandles(D)
    sc.on_tick(at(9, 21), 103.0, 100)   # forming 09:20 bucket built from ticks
    hist = candles_from([(100, 101, 99, 100.5, 1000), (100.5, 102, 100, 101.5, 900)])
    sc.merge_history(hist)
    cs = sc.candles(at(9, 22))
    assert cs[0]["src"] == "HIST" and cs[0]["v"] == 1000
    assert cs[1]["c"] == 103.0 and cs[1]["h"] == 103.0


def test_volume_profile_conserves_volume_and_handles_edges():
    cs = candles_from([(100, 104, 100, 103, 400), (103, 103, 103, 103, 100)])
    p = volume_profile(cs, 4)
    assert sum(b["volume"] for b in p["bins"]) == pytest.approx(500)
    assert p["basis"] == "5M CANDLE EST."
    assert volume_profile(candles_from([(1, 2, 0, 1, None)]), 4) is None
    assert len(mini_profile(cs, 10)) == 10


def test_pivots_delta_proxy_conviction_and_rr():
    pv = floor_pivots(110, 100, 105)
    assert pv["P"] == 105 and pv["R1"] == 110 and pv["S1"] == 100
    assert pivots_with_basis(None, candles_from([(1, 2, 0, 1, 1)]))["basis"].startswith("FALLBACK")
    cs = candles_from([(100, 110, 100, 110, 100), (5, 5, 5, 5, 999)])   # zero-range candle skipped
    assert delta_proxy(cs) == pytest.approx(100)
    assert conviction(80, 50) == pytest.approx(68) and conviction(None, 50) is None
    rr = risk_reward("BULLISH", 104.0, {"low": 102.0, "high": 103.0}, {"levels": pv})
    assert rr["stop"] == 102.0 and rr["target"] == 105 and rr["rr"] == pytest.approx(0.5)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_js_viewport_auto_pan_zoom():
    script = r"""
    const V = require(process.argv[1]);
    const out = {};
    let vp = V.fit(200); out.fit = vp;
    vp = V.pan(vp, 200, -20); out.pan = vp;
    out.dataManual = V.onData(vp, 201, 1);
    out.auto = V.setAuto(vp, 201);
    out.dataAuto = V.onData(out.auto, 202, 1);
    out.zoom = V.zoom(V.fit(200), 200, 0.5, 1);
    out.zoomMin = V.zoom(V.fit(200), 200, 0.01, 0.5);
    out.vis = V.visibleRange(V.fit(200), 200);
    console.log(JSON.stringify(out));
    """
    res = subprocess.run(["node", "-e", script, str(ROOT / "static" / "viewport.js")], capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, res.stderr
    o = json.loads(res.stdout)
    assert o["fit"]["auto"] is True and o["fit"]["end"] - o["fit"]["start"] == 90 and o["fit"]["end"] == 203
    assert o["pan"]["auto"] is False and o["pan"]["start"] == pytest.approx(93)
    assert o["dataManual"]["auto"] is False and o["dataManual"]["start"] == pytest.approx(92)
    assert o["auto"]["auto"] is True and o["auto"]["end"] == 204
    assert o["dataAuto"]["end"] == 205
    assert o["zoom"]["auto"] is False and o["zoom"]["end"] - o["zoom"]["start"] == pytest.approx(45)
    assert o["zoomMin"]["end"] - o["zoomMin"]["start"] == pytest.approx(12)
    assert o["vis"] == [113, 199]
