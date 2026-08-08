"""Portfolio-level simulation: compounding, exposure cap, contention, correlation.

This module exists because of a real error: the per-ticker engine sized every
instrument against the full book, so running 93 of them quietly held ~14
positions at once on a £100k account — about 2x leverage the live risk gate
would have refused. These tests pin the limits that make the numbers achievable.
"""
import pandas as pd
import pytest

from assistant.backtest import portfolio


def _candidate(ticker="AAA", entry="2024-01-02", exit_="2024-01-10",
               entry_price=100.0, stop=95.0, gross_per_share=10.0,
               market="US", reward_risk=2.0, currency="GBP", fx=1.0, **extra):
    out = {
        "ticker": ticker, "market": market, "currency": currency,
        "strategy": "mean_reversion", "strategy_label": "Mean reversion",
        "regime": "SIDEWAYS", "direction": "long",
        "signal_date": entry, "entry_date": entry, "exit_date": exit_,
        "entry_price": entry_price, "stop": stop, "target": 115.0,
        "exit_price": entry_price + gross_per_share, "exit_reason": "target",
        "bars_held": 5, "r_multiple": 2.0, "reward_risk": reward_risk,
        "gross_per_share": gross_per_share,
        "cost_fixed": 0.0, "cost_rate": 0.0,
        "fx_in": fx, "fx_out": fx,
    }
    out.update(extra)
    return out


CONFIG = {"account": {"portfolio_value": 100000, "risk_per_trade_pct": 1.0,
                      "max_position_pct": 15.0},
          "portfolio": {"max_gross_exposure_pct": 60.0, "max_open_positions": 8,
                        "max_positions_per_market": 8}}


# --- compounding --------------------------------------------------------------

def test_position_size_scales_with_current_equity():
    """The compounding fix. With a fixed book, a strategy that doubled the
    account still sized every trade off the original £100k — understating a
    compounding system and making the benchmark comparison unfair."""
    sequence = [_candidate(ticker=f"T{i}", entry=f"2024-0{i}-01",
                           exit_=f"2024-0{i}-15") for i in range(1, 5)]
    compounded = portfolio.simulate(sequence, CONFIG)
    fixed = portfolio.simulate(
        sequence, {**CONFIG, "portfolio": {**CONFIG["portfolio"], "compound": False}})

    assert compounded["final_equity"] > fixed["final_equity"]
    # Later trades should be larger under compounding.
    assert compounded["trades"][-1]["shares"] > compounded["trades"][0]["shares"]
    assert fixed["trades"][-1]["shares"] == fixed["trades"][0]["shares"]


def test_losses_shrink_the_next_position_too():
    """Compounding must cut both ways, or risk grows after every loss."""
    losers = [_candidate(ticker=f"L{i}", entry=f"2024-0{i}-01", exit_=f"2024-0{i}-15",
                         gross_per_share=-5.0) for i in range(1, 5)]
    out = portfolio.simulate(losers, CONFIG)
    assert out["final_equity"] < 100000
    assert out["trades"][-1]["shares"] < out["trades"][0]["shares"]


# --- exposure cap -------------------------------------------------------------

def test_gross_exposure_is_capped():
    """The bug that inflated every earlier headline: 14 concurrent positions on
    a £100k book. Each position here is 15% of the book, so a 60% cap admits
    four and refuses the rest."""
    same_day = [_candidate(ticker=f"T{i}") for i in range(10)]
    out = portfolio.simulate(same_day, CONFIG)
    assert len(out["trades"]) == 4
    assert out["skipped"]["exposure"] == 6


def test_a_bigger_cap_admits_more_positions():
    same_day = [_candidate(ticker=f"T{i}") for i in range(10)]
    levered = portfolio.simulate(same_day, {
        **CONFIG, "portfolio": {**CONFIG["portfolio"], "max_gross_exposure_pct": 200.0}})
    assert len(levered["trades"]) > 4


def test_max_open_positions_is_enforced():
    small = [_candidate(ticker=f"T{i}", entry_price=10.0, stop=9.9) for i in range(20)]
    out = portfolio.simulate(small, {
        **CONFIG, "portfolio": {**CONFIG["portfolio"], "max_open_positions": 3,
                                "max_gross_exposure_pct": 1000.0}})
    assert len(out["trades"]) == 3
    assert out["skipped"]["max_positions"] == 17


def test_positions_per_market_is_capped():
    """Four UK banks bought the same day is one bet with four tickets."""
    uk = [_candidate(ticker=f"UK{i}", market=".L") for i in range(6)]
    out = portfolio.simulate(uk, {
        **CONFIG, "portfolio": {**CONFIG["portfolio"], "max_positions_per_market": 2,
                                "max_gross_exposure_pct": 1000.0}})
    assert len(out["trades"]) == 2
    assert out["skipped"]["per_market"] == 4


def test_capital_frees_up_when_a_position_closes():
    """Capital released today must be usable today, or the model silently
    forbids rolling one position into the next."""
    first = _candidate(ticker="A", entry="2024-01-02", exit_="2024-01-10")
    later = [_candidate(ticker=f"B{i}", entry="2024-02-01", exit_="2024-02-10")
             for i in range(4)]
    out = portfolio.simulate([first] + later, CONFIG)
    assert len(out["trades"]) == 5      # the first one's capital was recycled


# --- contention ---------------------------------------------------------------

def test_the_best_reward_risk_wins_a_contested_slot():
    """Exposure is the scarce resource, so it goes to the best setup available
    rather than whichever happened to be first in the list."""
    poor = _candidate(ticker="POOR", reward_risk=1.1)
    good = _candidate(ticker="GOOD", reward_risk=9.9)
    out = portfolio.simulate([poor, good], {
        **CONFIG, "portfolio": {**CONFIG["portfolio"], "max_open_positions": 1}})
    assert [t["ticker"] for t in out["trades"]] == ["GOOD"]


def test_daily_entry_cap_is_respected():
    same_day = [_candidate(ticker=f"T{i}") for i in range(6)]
    out = portfolio.simulate(same_day, {
        **CONFIG, "portfolio": {**CONFIG["portfolio"], "max_new_entries_per_day": 2,
                                "max_gross_exposure_pct": 1000.0}})
    assert len(out["trades"]) == 2
    assert out["skipped"]["daily_cap"] == 4


# --- market filter ------------------------------------------------------------

def test_risk_off_can_skip_new_entries():
    candidate = _candidate(index_symbol="^GSPC")
    states = {("^GSPC", candidate["entry_date"]): "RISK_OFF"}
    out = portfolio.simulate([candidate],
                             {**CONFIG, "market_filter": {"risk_off_action": "skip"}},
                             market_states=states)
    assert out["trades"] == []
    assert out["skipped"]["risk_off"] == 1


def test_risk_off_half_sizing_takes_a_smaller_position():
    candidate = _candidate(index_symbol="^GSPC")
    states = {("^GSPC", candidate["entry_date"]): "RISK_OFF"}
    full = portfolio.simulate([candidate], CONFIG)
    half = portfolio.simulate([candidate], CONFIG, market_states=states)
    assert half["trades"][0]["shares"] == pytest.approx(
        full["trades"][0]["shares"] // 2, abs=1)
    assert half["trades"][0]["size_multiplier"] == 0.5


# --- correlation --------------------------------------------------------------

def _returns(seed_shift):
    import numpy as np
    rng = np.random.default_rng(1)
    base = rng.normal(0, 0.01, 120)
    values = base if seed_shift == 0 else base * 0.99 + rng.normal(0, 0.0005, 120)
    return pd.Series(values, index=pd.date_range("2023-10-01", periods=120, freq="D"))


def test_a_highly_correlated_candidate_is_rejected():
    """Position count is a poor proxy for risk — seven correlated longs is one
    bet, and a drawdown model treating them as seven understates the hole."""
    held = _candidate(ticker="AAA", entry="2024-01-02", exit_="2024-03-01")
    twin = _candidate(ticker="BBB", entry="2024-01-03", exit_="2024-03-01")
    series = {"AAA": _returns(0), "BBB": _returns(1)}
    out = portfolio.simulate([held, twin], {
        **CONFIG, "portfolio": {**CONFIG["portfolio"], "correlation_threshold": 0.8}},
        returns_lookup=series.get)
    assert len(out["trades"]) == 1
    assert out["skipped"]["correlation"] == 1


def test_correlation_check_is_off_by_default():
    held = _candidate(ticker="AAA", entry="2024-01-02", exit_="2024-03-01")
    twin = _candidate(ticker="BBB", entry="2024-01-03", exit_="2024-03-01")
    series = {"AAA": _returns(0), "BBB": _returns(1)}
    out = portfolio.simulate([held, twin], CONFIG, returns_lookup=series.get)
    assert len(out["trades"]) == 2


# --- mark to market -----------------------------------------------------------

def test_open_losses_show_in_the_drawdown_before_the_trade_closes():
    """Realised-only equity hides an open loser until the day it closes, which
    understates drawdown — the same error the earlier analysis corrected."""
    candidate = _candidate(ticker="AAA", entry="2024-01-02", exit_="2024-01-10",
                           gross_per_share=5.0)
    calendar = ["2024-01-02", "2024-01-04", "2024-01-06", "2024-01-10"]
    # Price collapses mid-trade before recovering to a winning exit.
    prices = {"2024-01-02": 100.0, "2024-01-04": 60.0,
              "2024-01-06": 80.0, "2024-01-10": 105.0}

    realised_only = portfolio.simulate([candidate], CONFIG)
    marked = portfolio.simulate([candidate], CONFIG, calendar=calendar,
                                price_lookup=lambda t, d: prices.get(d))

    assert realised_only["marked_to_market"] is False
    assert marked["marked_to_market"] is True
    assert marked["metrics"]["max_drawdown_pct"] > \
        (realised_only["metrics"]["max_drawdown_pct"] or 0)


def test_final_equity_matches_the_sum_of_realised_trades():
    trades = [_candidate(ticker=f"T{i}", entry=f"2024-0{i}-01", exit_=f"2024-0{i}-15")
              for i in range(1, 4)]
    out = portfolio.simulate(trades, CONFIG)
    expected = 100000 + sum(t["pnl_base"] for t in out["trades"])
    assert out["final_equity"] == pytest.approx(expected, abs=1.0)


def test_no_candidates_produces_a_flat_curve_not_a_crash():
    out = portfolio.simulate([], CONFIG)
    assert out["equity_curve"] == [100000]
    assert out["trades"] == []
