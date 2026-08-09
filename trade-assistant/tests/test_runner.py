"""Benchmarks, out-of-sample validation and sequence sensitivity.

These are the checks that every finding in this project was previously missing:
a comparison to doing nothing, a second period to confirm the result wasn't
fitted to the first, and a test of whether the answer depends on the order the
years happened to arrive in.
"""
import pandas as pd
import pytest

from assistant.backtest import runner


CONFIG = {"account": {"portfolio_value": 100000, "risk_per_trade_pct": 1.0,
                      "max_position_pct": 15.0},
          "portfolio": {"max_gross_exposure_pct": 60.0}}


def _frame(closes, start="2020-01-01"):
    idx = pd.date_range(start, periods=len(closes), freq="D")
    return pd.DataFrame({"Open": closes, "High": closes, "Low": closes,
                         "Close": closes, "Volume": [1e6] * len(closes)}, index=idx)


def _candidate(entry, pnl=1000.0, ticker="AAA"):
    return {"ticker": ticker, "market": "US", "currency": "GBP",
            "strategy": "ts_momentum", "strategy_label": "Time-series momentum",
            "regime": "SIDEWAYS", "direction": "long",
            "signal_date": entry, "entry_date": entry, "exit_date": entry,
            "entry_price": 100.0, "stop": 95.0, "target": 115.0,
            "exit_price": 110.0, "exit_reason": "target", "bars_held": 5,
            "r_multiple": 2.0, "reward_risk": 2.0,
            "gross_per_share": pnl / 100, "cost_fixed": 0.0, "cost_rate": 0.0,
            "fx_in": 1.0, "fx_out": 1.0}


# --- buy and hold -------------------------------------------------------------

def test_buy_and_hold_measures_the_same_universe():
    """Benchmarking against a broad index instead conflates two questions:
    whether the timing adds value, and whether the stock picks do."""
    frames = {"AAA": _frame([100 + i for i in range(200)]),
              "BBB": _frame([200 - i * 0.2 for i in range(200)])}
    dates = [str(d)[:10] for d in frames["AAA"].index]
    out = runner.buy_and_hold(frames, CONFIG, calendar=dates)
    assert out["final_equity"] > 0
    assert "2 instruments" in out["label"]
    assert out["cagr_pct"] is not None


def test_buy_and_hold_of_a_rising_market_makes_money():
    frames = {"AAA": _frame([100 * (1.001 ** i) for i in range(400)])}
    dates = [str(d)[:10] for d in frames["AAA"].index]
    out = runner.buy_and_hold(frames, CONFIG, calendar=dates)
    assert out["total_return_pct"] > 0
    assert out["max_drawdown_pct"] == 0.0


def test_buy_and_hold_reports_its_own_drawdown():
    values = [100] * 50 + [60] * 50 + [120] * 50
    frames = {"AAA": _frame(values)}
    dates = [str(d)[:10] for d in frames["AAA"].index]
    out = runner.buy_and_hold(frames, CONFIG, calendar=dates)
    assert out["max_drawdown_pct"] == pytest.approx(40.0, abs=1.0)


def test_blended_benchmark_is_less_volatile_than_full_equity():
    """The alternative a low-drawdown strategy must beat: you can always reduce
    risk for free by holding less equity."""
    curve = [100000, 120000, 70000, 140000]
    dates = ["2020-01-01", "2021-01-01", "2022-01-01", "2023-01-01"]
    blended = runner.blended_benchmark(curve, dates, equity_weight=0.6)
    from assistant.backtest import metrics
    full = metrics.summarise_curve(curve, dates)
    assert blended["max_drawdown_pct"] < full["max_drawdown_pct"]
    assert "60/40" in blended["label"]


# --- out-of-sample validation -------------------------------------------------

def test_a_config_that_only_works_in_the_first_half_is_marked_failed():
    """The check every finding in this project has been missing."""
    winners = [_candidate(f"2018-{m:02d}-{d:02d}", pnl=4000.0)
               for m in range(1, 13) for d in (1, 15, 20)]
    losers = [_candidate(f"2023-{m:02d}-{d:02d}", pnl=-4000.0)
              for m in range(1, 13) for d in (1, 15, 20)]
    out = runner.validate_out_of_sample(winners + losers, CONFIG, "2021-01-01")
    assert out["verdict"] == "FAILED"
    assert "fitted to the first half" in out["note"]


def test_a_config_that_works_in_both_halves_survives():
    early = [_candidate(f"2018-{m:02d}-{d:02d}", pnl=3000.0)
             for m in range(1, 13) for d in (1, 15, 20)]
    late = [_candidate(f"2023-{m:02d}-{d:02d}", pnl=3000.0)
            for m in range(1, 13) for d in (1, 15, 20)]
    out = runner.validate_out_of_sample(early + late, CONFIG, "2021-01-01")
    assert out["verdict"] in ("SURVIVED", "WEAKENED")


def test_split_divides_candidates_by_entry_date():
    candidates = [_candidate("2018-05-01"), _candidate("2023-05-01")]
    develop, validate = runner.split_candidates(candidates, "2021-01-01")
    assert len(develop) == 1 and len(validate) == 1


def test_too_few_trades_to_judge_is_said_plainly():
    out = runner.validate_out_of_sample([_candidate("2018-01-01")], CONFIG, "2021-01-01")
    assert out["verdict"] == "inconclusive"
    assert "Not enough trades" in out["note"]


# --- sequence sensitivity -----------------------------------------------------

def test_sequence_sensitivity_reports_a_spread_not_a_point():
    """Compounding makes the order of returns decisive. If a good result only
    holds when the crash arrives late, it is fragile rather than robust."""
    trades = ([{"pnl_base": 5000.0} for _ in range(20)]
              + [{"pnl_base": -4000.0} for _ in range(15)])
    out = runner.sequence_sensitivity(trades, CONFIG, shuffles=100)
    assert out["final_worst_5pct"] <= out["final_median"] <= out["final_best_5pct"]
    assert out["drawdown_worst_5pct_pct"] >= out["drawdown_median_pct"]


def test_sequence_sensitivity_needs_a_meaningful_sample():
    assert runner.sequence_sensitivity([{"pnl_base": 1.0}] * 5, CONFIG) == {}


def test_shuffling_cannot_change_the_final_total():
    """Reordering trades changes the PATH and therefore the drawdown, but the
    sum is the sum — if the median final moved, the simulation is broken."""
    trades = [{"pnl_base": 1000.0} for _ in range(15)] + \
             [{"pnl_base": -500.0} for _ in range(15)]
    out = runner.sequence_sensitivity(trades, CONFIG, shuffles=50)
    expected = 100000 + sum(t["pnl_base"] for t in trades)
    assert out["final_median"] == pytest.approx(expected, abs=0.01)
    assert out["final_worst_5pct"] == pytest.approx(expected, abs=0.01)
