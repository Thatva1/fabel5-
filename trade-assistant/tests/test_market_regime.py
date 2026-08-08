"""Market-wide risk-on / risk-off filter.

The hysteresis tests matter most: a plain above/below test flips state every
time the index brushes its average, and each flip costs a round of entries and
exits. That whipsaw is what turned a choppy 2022 into a losing year.
"""
import numpy as np
import pandas as pd
import pytest

from assistant.research import market_regime as mr


def _series(values):
    return pd.Series([float(v) for v in values],
                     index=pd.date_range("2020-01-01", periods=len(values), freq="D"))


def _rising(n=300, start=100.0, daily=0.002):
    return _series([start * (1 + daily) ** i for i in range(n)])


def _falling(n=300, start=300.0, daily=0.002):
    return _series([start * (1 - daily) ** i for i in range(n)])


def _flat_then(final, n=250, drift_bars=30, seed=5, noise=0.004):
    """A flat-ish market with ordinary daily noise that then DRIFTS to `final`
    times its own 200-day average.

    Two fixture mistakes this avoids. A perfectly flat line has zero historical
    volatility, so any final move ranks as the most volatile bar on record. And
    reaching the target in one bar is itself a volatility spike — correctly
    tripping the crash override, so the hysteresis branch never gets tested.
    Real markets get 5% above their average over weeks, not overnight.
    """
    rng = np.random.default_rng(seed)
    values, price = [], 100.0
    for step in rng.normal(0.0, noise, n):
        price *= 1 + step
        values.append(price)

    target = (sum(values[-200:]) / 200) * final
    start = values[-1]
    for i in range(1, drift_bars + 1):
        glide = start + (target - start) * (i / drift_bars)
        values.append(glide * (1 + rng.normal(0.0, noise / 3)))
    return _series(values)


# --- basic classification ----------------------------------------------------

def test_rising_market_is_risk_on():
    out = mr.classify_market(_rising())
    assert out["state"] == mr.RISK_ON
    assert out["pct_vs_average"] > 0


def test_falling_market_is_risk_off():
    out = mr.classify_market(_falling())
    assert out["state"] == mr.RISK_OFF
    assert "BELOW" in " ".join(out["reasons"])


def test_not_enough_history_is_unknown_not_a_guess():
    out = mr.classify_market(_series([100] * 50))
    assert out["state"] == mr.UNKNOWN
    assert "50 bars" in out["reasons"][0]


def test_the_filter_can_be_switched_off():
    out = mr.classify_market(_falling(), {"market_filter": {"enabled": False}})
    assert out["state"] == mr.RISK_ON


# --- hysteresis: the whipsaw fix ---------------------------------------------

def test_a_marginal_recovery_does_not_flip_straight_back_to_risk_on():
    """Just above the average is not enough to re-enter. Without this, a choppy
    market crossing the line repeatedly generates a round of entries and exits
    on every crossing."""
    out = mr.classify_market(_flat_then(1.003), previous_state=mr.RISK_OFF)
    assert out["state"] == mr.RISK_OFF
    assert "whipsaw" in " ".join(out["reasons"])


def test_clearing_the_buffer_does_re_enter():
    out = mr.classify_market(_flat_then(1.05), previous_state=mr.RISK_OFF)
    assert out["state"] == mr.RISK_ON
    assert "re-entry buffer" in " ".join(out["reasons"])


def test_going_risk_off_needs_no_buffer():
    """Deliberately asymmetric — slow to buy back in, fast to step aside.
    Whipsaw costs money; being late to a rally costs less."""
    out = mr.classify_market(_flat_then(0.999), previous_state=mr.RISK_ON)
    assert out["state"] == mr.RISK_OFF


def test_the_buffer_is_configurable():
    series = _flat_then(1.01)
    strict = mr.classify_market(series, previous_state=mr.RISK_OFF)
    assert strict["state"] == mr.RISK_OFF          # 1% < default 2% buffer
    loose = mr.classify_market(series,
                               {"market_filter": {"reentry_buffer_pct": 0.5}},
                               previous_state=mr.RISK_OFF)
    assert loose["state"] == mr.RISK_ON


# --- volatility override -----------------------------------------------------

def test_a_volatility_spike_forces_risk_off_even_above_the_average():
    """A fast crash can outrun a 200-day average entirely — by the time price is
    below it, the damage is done."""
    rng = np.random.default_rng(3)
    calm = [100 * (1.001) ** i for i in range(260)]
    violent = list(calm)
    price = calm[-1]
    for step in rng.normal(0.0, 0.09, 25):        # extreme daily swings
        price *= 1 + step
        violent.append(price)
    out = mr.classify_market(_series(violent))
    assert out["volatility_percentile"] is not None
    if out["volatility_percentile"] >= 95:
        assert out["state"] == mr.RISK_OFF
        assert "volatility" in " ".join(out["reasons"])


# --- sizing ------------------------------------------------------------------

def test_half_size_is_the_default_response_to_risk_off():
    """Standing fully aside gives up the recovery, which historically begins
    while the moving average still reads risk-off."""
    assert mr.size_multiplier(mr.RISK_OFF) == 0.5
    assert mr.size_multiplier(mr.RISK_ON) == 1.0


def test_skip_mode_stands_fully_aside():
    config = {"market_filter": {"risk_off_action": "skip"}}
    assert mr.size_multiplier(mr.RISK_OFF, config) == 0.0


# --- per-region routing ------------------------------------------------------

def test_each_market_is_gated_on_its_own_index():
    """Gating a Japanese trade on the S&P is a category error — a US uptrend
    says nothing about whether Tokyo is rising."""
    assert mr.index_for("AAPL") == "^GSPC"
    assert mr.index_for("TSCO.L") == "^FTSE"
    assert mr.index_for("7203.T") == "^N225"
    assert mr.index_for("SAP.DE") == "^GDAXI"
    assert mr.index_for("BHP.AX") == "^AXJO"


def test_indices_can_be_overridden_in_config():
    config = {"market_filter": {"indices": {"": "^NDX", ".L": "^FTMC"}}}
    assert mr.index_for("AAPL", config) == "^NDX"
    assert mr.index_for("TSCO.L", config) == "^FTMC"


def test_an_unmapped_market_has_no_index():
    assert mr.index_for("SOMETHING.XYZ") is None
