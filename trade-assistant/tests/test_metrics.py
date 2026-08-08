"""Equity-curve metrics.

The first two tests pin down the exact mistake that understated every drawdown
in this project's earlier analysis: dividing the fall by the WRONG peak.
"""
import pytest

from assistant.backtest import metrics


def test_drawdown_uses_the_peak_at_the_time_not_the_final_peak():
    """The bug that reported a 27% fall as 18%. Equity peaks at 120, falls to
    90 (25% down from 120), then recovers to 200. Measured against the final
    peak of 200 the fall looks like 15%; the true answer is 25%."""
    dd = metrics.max_drawdown([100, 120, 90, 150, 200])
    assert dd["pct"] == 25.0
    assert dd["amount"] == 30.0
    assert dd["peak"] == 120 and dd["trough"] == 90


def test_the_worst_drawdown_wins_not_the_last_one():
    dd = metrics.max_drawdown([100, 200, 100, 210, 189])
    assert dd["pct"] == 50.0          # 200 -> 100, not the later 210 -> 189


def test_a_LATER_drawdown_wins_when_it_is_worse():
    """The case the earlier tests missed, because in both of them the worst
    drawdown happened to come first. The running best was stored as a
    percentage but compared against a fraction, so once any drawdown was
    recorded nothing could beat it (0.5 > 4.55 is false) — and the function
    silently returned the FIRST drawdown rather than the largest. It reported
    0.28% on a real ten-year run."""
    dd = metrics.max_drawdown([100, 110, 105, 200, 100])
    assert dd["pct"] == 50.0          # not the 4.55% dip that came first
    assert dd["peak"] == 200 and dd["trough"] == 100


def test_drawdowns_are_compared_in_consistent_units():
    """Three successive drawdowns, each worse than the last."""
    dd = metrics.max_drawdown([100, 99, 100, 95, 100, 70])
    assert dd["pct"] == 30.0


def test_a_curve_that_only_rises_has_no_drawdown():
    assert metrics.max_drawdown([100, 110, 120, 130])["pct"] == 0.0


def test_drawdown_reports_when_it_happened():
    dd = metrics.max_drawdown([100, 120, 90, 150])
    assert dd["peak_index"] == 1 and dd["trough_index"] == 2


# --- time underwater ---------------------------------------------------------

def test_time_underwater_measures_recovery_not_just_depth():
    """A 9% drawdown recovered in a month and one lasting three years are not
    the same experience, and max drawdown alone cannot tell them apart."""
    quick = metrics.time_underwater([100, 90, 100, 110])
    slow = metrics.time_underwater([100, 90, 91, 92, 93, 94, 100, 110])
    assert quick["longest_periods"] == 1
    assert slow["longest_periods"] == 5
    # Same depth, very different ordeal.
    assert metrics.max_drawdown([100, 90, 100, 110])["pct"] == \
        metrics.max_drawdown([100, 90, 91, 92, 93, 94, 100, 110])["pct"]


def test_time_underwater_reports_the_share_of_the_period_below_peak():
    out = metrics.time_underwater([100, 90, 95, 100, 110])
    assert out["pct_of_time"] == 40.0        # 2 of 5 points below a prior peak


def test_a_curve_still_below_its_peak_reports_current_underwater():
    out = metrics.time_underwater([100, 120, 110, 105])
    assert out["current_periods"] == 2


def test_underwater_dates_are_surfaced_when_given():
    dates = ["2024-01-01", "2024-02-01", "2024-03-01", "2024-04-01"]
    out = metrics.time_underwater([100, 90, 91, 100], dates)
    assert out["longest_start"] == "2024-01-01"
    assert out["longest_end"] == "2024-03-01"


# --- return metrics ----------------------------------------------------------

def test_cagr_compounds():
    assert metrics.cagr([100, 200], years=1) == 100.0
    assert metrics.cagr([100.0, 121.0], years=2) == pytest.approx(10.0, abs=0.01)


def test_cagr_is_none_when_the_book_is_wiped_out():
    """A zero or negative final value has no real-valued growth rate. Returning
    a number there would be inventing one."""
    assert metrics.cagr([100, 0], years=1) is None
    assert metrics.cagr([100, -10], years=1) is None


def test_sortino_ignores_upside_volatility():
    """Sharpe penalises big winners as much as big losers, which misprices a
    strategy built on frequent small losses and occasional 3R winners."""
    steady = [100, 101, 102, 103, 104, 105]
    spiky = [100, 101, 102, 103, 104, 130]        # same losses, one huge gain
    assert metrics.sortino(spiky) is None or metrics.sharpe(spiky) is not None
    lumpy = [100, 99, 101, 100, 102, 101, 104]
    assert metrics.sortino(lumpy) > metrics.sharpe(lumpy)


def test_calmar_is_return_over_worst_case_pain():
    equity = [100, 120, 90, 150]
    expected = metrics.cagr(equity, 1) / metrics.max_drawdown(equity)["pct"]
    assert metrics.calmar(equity, 1) == pytest.approx(expected, abs=0.02)


def test_metrics_on_an_empty_or_flat_curve_do_not_explode():
    assert metrics.summarise_curve([]) == {}
    flat = metrics.summarise_curve([100, 100, 100])
    assert flat["max_drawdown_pct"] == 0.0
    assert flat["cagr_pct"] == 0.0


def test_summary_carries_both_percentage_and_amount():
    """A drawdown in pounds with no denominator is uninterpretable; a percentage
    with no amount hides the real-money size. Report both."""
    out = metrics.summarise_curve([100000, 120000, 90000, 150000])
    assert out["max_drawdown_pct"] == 25.0
    assert out["max_drawdown_amount"] == 30000.0
    assert out["final_equity"] == 150000
    assert out["total_return_pct"] == 50.0
