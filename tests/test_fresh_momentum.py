import pytest

from fresh_momentum import MinuteTape, compute_fresh, thresholds
from helpers import D, at


def tape_with(prices: dict, d=D, vols=None):
    """prices: {(hh, mm): price}"""
    t = MinuteTape(d)
    for (hh, mm), p in sorted(prices.items()):
        t.add(at(hh, mm, 30, d), p, (vols or {}).get((hh, mm)))
    return t


def run(t, ltp, now=None, prev=100.0, adr=None, rvol=1.5):
    return compute_fresh(t, now=now or at(11, 0, 40), ltp=ltp, prev_close=prev, adr20=adr, rvol20=rvol, session_date=D)


def test_thresholds_floor_and_adr_scaling():
    assert thresholds(None) == {5: 0.10, 15: 0.22, 30: 0.32, 60: 0.48}
    th = thresholds(4.0)
    assert th[5] == pytest.approx(0.22) and th[15] == pytest.approx(0.48)
    assert th[30] == pytest.approx(0.72) and th[60] == pytest.approx(1.12)


def test_one_sample_per_minute_replaced_and_capped():
    t = MinuteTape(D)
    t.add(at(10, 0, 5), 100.0, 10)
    t.add(at(10, 0, 50), 101.0, 20)
    assert len(t) == 1 and t.latest()[1] == 101.0
    for m in range(1, 80):
        t.add(at(10, 0) .replace(minute=0) + __import__("datetime").timedelta(minutes=m, seconds=5), 100.0 + m, 0)
    assert len(t) == 68


def test_tape_rejects_previous_session_and_out_of_order():
    t = MinuteTape(D)
    assert not t.add(at(10, 0, d=D.replace(day=9)), 100.0, 1)
    assert t.add(at(10, 5), 100.0, 1)
    assert not t.add(at(10, 4), 99.0, 1)
    assert not t.add(at(16, 0), 99.0, 1)  # outside session hours


def test_reference_minute_tolerance_one_minute_only():
    t = tape_with({(10, 54): 100.0, (11, 0): 101.0})
    assert t.reference(at(10, 55)) == (100.0, None)   # one minute earlier is accepted
    assert t.reference(at(10, 56)) is None             # two minutes earlier is not


def test_active_with_pending_longer_windows():
    t = tape_with({(10, 45): 100.0, (10, 55): 100.3, (11, 0): 100.8})
    f = run(t, 100.8)
    assert f["momentumState"] == "ACTIVE"
    assert f["windows"]["30"]["status"] == "pending" and f["windows"]["60"]["status"] == "pending"
    assert f["readyCount"] == 2 and f["pickEligible"] is True
    assert "pending" in f["freshReason"]


def test_warming_up_when_15m_missing():
    f = run(tape_with({(10, 55): 100.0, (11, 0): 101.0}), 101.0)
    assert f["momentumState"] == "WARMING_UP" and "15m" in f["freshReason"] and f["freshScore"] == 0


def test_stale_when_latest_sample_older_than_two_minutes():
    t = tape_with({(10, 45): 100.0, (10, 55): 100.3, (11, 0): 100.8})
    assert run(t, 100.8, now=at(11, 3, 0))["momentumState"] == "STALE"


def test_fading_on_strong_reversal_against_day_direction():
    # bullish day (ltp > prev close) but last 5 minutes dropped sharply
    t = tape_with({(10, 45): 103.0, (10, 55): 103.5, (11, 0): 102.5})
    f = run(t, 102.5)
    assert f["direction"] == "BULLISH" and f["momentumState"] == "FADING"


def test_consolidating_when_longer_window_disagrees():
    prices = {(10, 0): 104.0, (10, 30): 103.9, (10, 45): 100.0, (10, 55): 100.3, (11, 0): 100.8}
    f = run(tape_with(prices), 100.8)
    # 5m/15m strong, 30m ok-ish? 30m ref at 10:30 = 103.9 -> negative directed -> FADING if <= -T30
    assert f["momentumState"] in ("FADING", "CONSOLIDATING")
    prices = {(10, 0): 100.95, (10, 30): 100.79, (10, 45): 100.0, (10, 55): 100.3, (11, 0): 100.8}
    f = run(tape_with(prices), 100.8)
    assert f["momentumState"] == "CONSOLIDATING" and "Longer window" in f["freshReason"]


def test_consolidating_when_short_momentum_weak():
    f = run(tape_with({(10, 45): 100.0, (10, 55): 100.0, (11, 0): 100.02}), 100.02)
    assert f["momentumState"] == "CONSOLIDATING" and "Short-term" in f["freshReason"]


def test_bearish_direction_uses_negative_sign():
    t = tape_with({(10, 45): 99.0, (10, 55): 98.7, (11, 0): 98.2})
    f = run(t, 98.2)
    assert f["direction"] == "BEARISH" and f["momentumState"] == "ACTIVE"
    assert f["windows"]["5"]["directedPct"] > 0 > f["windows"]["5"]["pct"]


def test_volume_confirmation_and_acceleration_ratio():
    prices = {(10, 45): 100.0, (10, 50): 100.1, (10, 55): 100.3, (11, 0): 100.8}
    vols = {(10, 45): 0, (10, 50): 1000, (10, 55): 2000, (11, 0): 5000}
    f = run(tape_with(prices, vols=vols), 100.8, rvol=0.9)
    assert f["recentVolumeRatio"] == pytest.approx(3.0)   # (5000-2000)/(2000-1000)
    assert f["volumeConfirmed"] is False and f["pickEligible"] is False
    assert run(tape_with(prices, vols=vols), 100.8, rvol=None)["volumeConfirmed"] is False


def test_fresh_score_formula_active():
    t = tape_with({(10, 45): 100.0, (10, 55): 100.3, (11, 0): 100.8})
    f = run(t, 100.8, rvol=2.0)
    d5 = 100 * (100.8 / 100.3 - 1)
    d15 = 0.8
    expected = 20 * min(3, d5 / 0.10) + 16 * min(3, d15 / 0.22) + 4 * 2.0
    assert f["freshScore"] == pytest.approx(expected, rel=1e-3)
