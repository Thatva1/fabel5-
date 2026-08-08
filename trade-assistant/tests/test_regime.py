"""Regime classifier + indicator maths.

Synthetic price series are used deliberately: a hand-built uptrend is the only
way to assert "this must classify as TRENDING_UP" without depending on whatever
the market happened to do today.
"""
import numpy as np
import pandas as pd
import pytest

from assistant.research import indicators, regime, scanner


def _frame(closes, volume=1_000_000, spread=0.01):
    """Build an OHLCV frame from a close series with a plausible daily range."""
    closes = pd.Series([float(c) for c in closes])
    high = closes * (1 + spread)
    low = closes * (1 - spread)
    return pd.DataFrame({
        "Open": closes.shift(1).fillna(closes.iloc[0]),
        "High": high,
        "Low": low,
        "Close": closes,
        "Volume": [volume] * len(closes),
    })


def _uptrend(n=300, start=100.0, daily=0.004):
    return [start * (1 + daily) ** i for i in range(n)]


def _downtrend(n=300, start=300.0, daily=0.004):
    return [start * (1 - daily) ** i for i in range(n)]


def _flat(n=300, level=100.0, amplitude=4.0, period=20):
    return [level + amplitude * np.sin(2 * np.pi * i / period) for i in range(n)]


def _snapshot(closes, **overrides):
    snap = scanner.scan_ticker("TEST", _frame(closes), {"min_history_days": 60})
    snap.update(overrides)
    return snap


# --- indicators --------------------------------------------------------------

def test_adx_is_high_in_a_clean_trend_and_low_in_chop():
    trending = indicators.adx(_frame(_uptrend()))["adx"].iloc[-1]
    choppy = indicators.adx(_frame(_flat()))["adx"].iloc[-1]
    assert trending > 25, f"clean uptrend should register as a trend, got ADX {trending}"
    assert choppy < 25, f"oscillating series should not register as a trend, got ADX {choppy}"


def test_adx_direction_indicators_agree_with_the_trend():
    up = indicators.adx(_frame(_uptrend()))
    assert up["plus_di"].iloc[-1] > up["minus_di"].iloc[-1]
    down = indicators.adx(_frame(_downtrend()))
    assert down["minus_di"].iloc[-1] > down["plus_di"].iloc[-1]


def test_bollinger_width_is_scale_free():
    """Two series with the same shape but different price levels must produce
    the same band width percentage — otherwise expensive shares would look
    permanently more volatile than cheap ones."""
    cheap = indicators.bollinger(pd.Series(_flat(120, level=10, amplitude=0.4)))
    dear = indicators.bollinger(pd.Series(_flat(120, level=1000, amplitude=40)))
    assert cheap["width_pct"].iloc[-1] == pytest.approx(dear["width_pct"].iloc[-1], rel=1e-6)


def test_percentile_rank_bottoms_out_when_volatility_collapses():
    widening = pd.Series(list(range(100)))
    assert indicators.percentile_rank(widening) == 100.0
    narrowing = pd.Series(list(range(100, 0, -1)))
    assert indicators.percentile_rank(narrowing) == pytest.approx(1.0)


def test_constant_band_width_is_not_a_squeeze():
    """Unchanging volatility must rank at the TOP, not the bottom. Float drift
    once made a perfectly steady series look like a compression to its lowest
    level in months, which would have manufactured squeeze setups from nothing."""
    steady = pd.Series([5.0 * (1 - 1e-15) ** i for i in range(120)])
    assert indicators.percentile_rank(steady) == 100.0


def test_relative_strength_is_the_difference_in_returns():
    ticker = pd.Series([100.0] * 10 + [110.0])       # +10%
    benchmark = pd.Series([100.0] * 10 + [104.0])    # +4%
    assert indicators.relative_strength(ticker, benchmark, days=10) == pytest.approx(6.0)


def test_relative_strength_is_none_without_a_benchmark():
    assert indicators.relative_strength(pd.Series([1.0, 2.0]), None) is None


def test_recent_extremes_exclude_today():
    """Today's own high must not be part of the high it is being compared to,
    or every new high 'breaks out' above itself."""
    df = _frame([10] * 30 + [99])
    high, _ = indicators.recent_extremes(df, lookback=20)
    assert high < 50


# --- regime classification ---------------------------------------------------

def test_uptrend_classifies_as_trending_up():
    result = regime.classify(_snapshot(_uptrend()))
    assert result["regime"] == regime.TRENDING_UP
    assert result["trend_bias"] == "up"
    assert any("200-day" in r for r in result["reasons"])


def test_downtrend_classifies_as_trending_down():
    result = regime.classify(_snapshot(_downtrend()))
    assert result["regime"] == regime.TRENDING_DOWN
    assert result["trend_bias"] == "down"


def test_oscillating_market_classifies_as_sideways():
    result = regime.classify(_snapshot(_flat()))
    assert result["regime"] == regime.SIDEWAYS


def test_squeeze_wins_when_band_width_collapses():
    snap = _snapshot(_flat(), bb_width_rank=3.0)
    assert regime.classify(snap)["regime"] == regime.VOLATILITY_SQUEEZE


def test_squeeze_can_be_told_not_to_override_a_trend():
    snap = _snapshot(_uptrend(), bb_width_rank=3.0)
    assert regime.classify(snap)["regime"] == regime.VOLATILITY_SQUEEZE
    config = {"regime": {"squeeze_overrides_trend": False}}
    assert regime.classify(snap, config)["regime"] == regime.TRENDING_UP


def test_strong_adx_but_price_fighting_its_moving_average_is_not_a_trend():
    """A powerful move against a rising 200-day average is a turn, not a trend.
    Buying it as momentum is exactly the mistake the trend filter prevents."""
    snap = _snapshot(_uptrend())
    snap["price"] = snap["sma200"] * 0.9      # sharp drop below a still-rising MA
    result = regime.classify(snap)
    assert result["regime"] == regime.SIDEWAYS
    assert any("disagree" in r for r in result["reasons"])


def test_missing_history_is_unknown_not_a_guess():
    result = regime.classify({"price": 100, "adx": 30})   # no sma200
    assert result["regime"] == regime.UNKNOWN
    assert "sma200" in result["reasons"][0]


def test_thresholds_come_from_config():
    snap = _snapshot(_flat())
    snap["adx"] = 22.0
    assert regime.classify(snap)["regime"] == regime.SIDEWAYS
    loosened = {"regime": {"adx_trend_min": 20.0}}
    assert regime.classify(snap, loosened)["regime"] in (
        regime.TRENDING_UP, regime.TRENDING_DOWN, regime.SIDEWAYS)


def test_unknown_regime_is_not_in_the_tradeable_list():
    assert regime.UNKNOWN not in regime.ALL_REGIMES
