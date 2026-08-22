"""Estimating what an instrument costs to cross, from its own bars.

This exists because of one specific way a wide-universe backtest lies. Charging
every instrument a mega-cap's spread is not a small inaccuracy when the universe
goes from eight names to fifteen hundred — it is the largest error in the study,
and it is wrong in the direction that flatters.

The tests pin the estimator's KNOWN BIAS as well as its accuracy. An estimator
whose error direction is undocumented is worse than a crude one whose is.
"""
import numpy as np
import pandas as pd
import pytest

from assistant.backtest import spread


def bouncing(round_trip_bps, *, bars=400, vol=0.04, seed=42):
    """A random walk with a known bid-ask bounce printed on top."""
    rng = np.random.default_rng(seed)
    half = round_trip_bps / 2 / 10_000
    mid = 50 + np.cumsum(rng.normal(0, vol, bars))
    side = np.where(np.arange(bars) % 2 == 0, 1 + half, 1 - half)
    close = mid * side
    return pd.DataFrame({"Open": close, "High": mid * (1 + half),
                         "Low": mid * (1 - half), "Close": close,
                         "Volume": [1] * bars})


def test_a_wide_instrument_measures_far_wider_than_a_tight_one():
    """The separation is what the exclusion rule rests on. Absolute accuracy
    matters less than never confusing a 3bp name for a 60bp one."""
    tight = spread.estimate_spread_bps(bouncing(3))
    wide = spread.estimate_spread_bps(bouncing(60))
    assert wide > tight * 20


def test_the_estimator_understates_wide_spreads_and_that_is_documented():
    """The dangerous direction, pinned so it cannot drift unnoticed. A true
    60bp round trip comes back around 44. Any cap set on these numbers has to
    allow for it."""
    measured = spread.estimate_spread_bps(bouncing(60))
    assert 35 < measured < 55, measured
    assert measured < 60, "if this ever overstates, the docstring is wrong"


def test_it_scales_monotonically_with_the_real_spread():
    values = [spread.estimate_spread_bps(bouncing(bps)) for bps in (5, 20, 50, 100)]
    assert values == sorted(values)


def test_too_few_bars_is_unmeasurable_rather_than_zero():
    """Returning 0.0 would be read as 'free to trade', which is the worst
    possible answer to 'I don't know'."""
    assert spread.estimate_spread_bps(bouncing(20, bars=2)) is None
    assert spread.estimate_spread_bps(None) is None


def test_a_floor_stops_a_clean_series_being_charged_nothing():
    """An estimate of zero means the estimator could not see the spread, not
    that there isn't one."""
    table = spread.cost_table({"CLEAN": bouncing(1)}, floor_bps=2.0)
    assert table["spreads"]["CLEAN"] == 2.0


def test_instruments_too_wide_to_trade_are_excluded_and_named():
    """Silently dropping them would hide how much of the universe the study
    actually covers."""
    frames = {"TIGHT": bouncing(3), "WIDE": bouncing(200)}
    table = spread.cost_table(frames, cap_bps=40.0)
    assert list(table["spreads"]) == ["TIGHT"]
    assert [row["ticker"] for row in table["excluded"]] == ["WIDE"]
    assert table["excluded"][0]["spread_bps"] > 40


def test_unmeasurable_instruments_are_reported_not_assumed_free():
    frames = {"GOOD": bouncing(10), "STUB": bouncing(10, bars=2)}
    table = spread.cost_table(frames)
    assert table["unmeasured"] == ["STUB"]
    assert "STUB" not in table["spreads"]


def test_describe_reads_the_shape_of_the_surviving_universe():
    frames = {f"T{i}": bouncing(5 + i * 4, seed=i) for i in range(10)}
    summary = spread.describe(spread.cost_table(frames))
    assert summary["instruments"] == 10
    assert summary["tightest_bps"] <= summary["median_bps"] <= summary["widest_bps"]


def test_an_empty_universe_describes_itself_as_empty():
    assert spread.describe({"spreads": {}})["instruments"] == 0
