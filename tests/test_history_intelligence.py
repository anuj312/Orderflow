import pytest

from helpers import D, at, daily_bars, intraday_sessions
from history_intelligence import (build_baseline, compute_adr20, compute_atr14, compute_rvol, completed_daily_bars,
                                  cumulative_volume_profile, expected_cumulative_volume, group_intraday_sessions,
                                  live_history_metrics, load_cache, save_cache)


def test_no_lookahead_daily_bars_excluded_on_or_after_target():
    bars = daily_bars(25, D) + [{"date": D, "o": 1, "h": 1000, "l": 1, "c": 900, "v": 1}]
    done = completed_daily_bars(bars, D)
    assert all(b["date"] < D for b in done)
    base = build_baseline("X", bars, [], D)
    assert base["high20"] == 110.0 and base["low20"] == 100.0


def test_adr20_and_atr14_exact():
    bars = completed_daily_bars(daily_bars(25, D), D)
    assert compute_adr20(bars) == pytest.approx(100 * 10 / 105, rel=1e-4)
    assert compute_atr14(bars) == pytest.approx(10.0)


def test_atr14_null_without_14_true_ranges():
    bars = completed_daily_bars(daily_bars(14, D), D)  # 13 true ranges
    assert compute_atr14(bars) is None
    assert compute_adr20([]) is None


def test_true_range_uses_previous_close_gap():
    bars = [{"date": "2026-11-0%d" % i, "h": 101, "l": 99, "c": 100} for i in range(1, 3)]
    bars[1] = {"date": "2026-11-02", "h": 120, "l": 118, "c": 119}  # gap up
    from history_intelligence import true_ranges
    assert true_ranges(bars) == [20]  # max(2, |120-100|, |118-100|)


def test_only_sessions_with_60_of_75_candles_are_valid():
    good = intraday_sessions(3, D, candles=60)
    bad = intraday_sessions(5, D, candles=59)
    sessions = group_intraday_sessions(good, D)
    assert len(cumulative_volume_profile(sessions)[2]) == 3
    assert cumulative_volume_profile(group_intraday_sessions(bad, D))[1] == 0


def test_rvol_time_matched_at_10am_bucket():
    sessions = group_intraday_sessions(intraday_sessions(12, D), D)
    profile, n, _ = cumulative_volume_profile(sessions)
    assert n == 12
    assert expected_cumulative_volume(profile, at(10, 0)) == pytest.approx(900)  # 09:15-10:00 = 9 candles
    assert compute_rvol(1800, profile, n, at(10, 0)) == pytest.approx(2.0)
    assert expected_cumulative_volume(profile, at(10, 2, 30)) == pytest.approx(950)


def test_rvol_unavailable_until_10_valid_sessions_and_missing_volume():
    p9, n9, _ = cumulative_volume_profile(group_intraday_sessions(intraday_sessions(9, D), D))
    assert compute_rvol(1000, p9, n9, at(10, 0)) is None
    p, n, _ = cumulative_volume_profile(group_intraday_sessions(intraday_sessions(12, D), D))
    assert compute_rvol(None, p, n, at(10, 0)) is None
    assert compute_rvol(1000, p, n, at(9, 0)) is None  # before open


def test_intraday_today_never_used_for_baseline():
    today = intraday_sessions(1, D.replace(day=D.day + 1))  # candles dated D itself
    assert group_intraday_sessions(today, D) == {}


def test_baseline_statuses_and_nulls():
    ready = build_baseline("X", daily_bars(22, D), intraday_sessions(20, D), D)
    assert ready["historyStatus"] == "READY" and ready["intradaySessions"] == 20 and ready["historySessions"] == 20
    partial = build_baseline("X", daily_bars(15, D), intraday_sessions(11, D), D)
    assert partial["historyStatus"] == "PARTIAL"
    empty = build_baseline("X", [], [], D)
    assert empty["historyStatus"] == "UNAVAILABLE" and empty["adr20"] is None and empty["cumProfile"] is None


def test_live_metrics_breakout_and_missing():
    base = build_baseline("X", daily_bars(22, D), intraday_sessions(20, D), D)
    m = live_history_metrics(base, 111.0, 1800, 111.0, 104.0, at(10, 0))
    assert m["breakout20"] == "ABOVE_20D_HIGH" and m["rvol"] == pytest.approx(2.0)
    assert live_history_metrics(base, 99.0, None, None, None, at(10, 0))["breakout20"] == "BELOW_20D_LOW"
    loading = live_history_metrics(None, 100.0, 10, 101, 99, at(10, 0))
    assert loading["historyStatus"] == "LOADING" and loading["rvol"] is None and loading["adr20"] is None


def test_cache_roundtrip_and_target_date_guard(tmp_path):
    base = build_baseline("X", daily_bars(22, D), intraday_sessions(12, D), D)
    save_cache(str(tmp_path), D, "t", {"X": base})
    assert load_cache(str(tmp_path), D, "t")["X"]["adr20"] == base["adr20"]
    assert load_cache(str(tmp_path), D.replace(day=11), "t") == {}
    text = (tmp_path / f"baselines_t_{D.isoformat()}.json.gz").read_bytes()
    assert b"KITE" not in text.upper()
