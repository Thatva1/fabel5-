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
        "strategy": "ts_momentum", "strategy_label": "Time-series momentum",
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

def test_the_stronger_signal_wins_a_contested_slot():
    """Exposure is the scarce resource, so it goes to the candidate its own
    rule rates highest — not to whichever happened to be first in the list."""
    weak = _candidate(ticker="WEAK", strength=0.05)
    strong = _candidate(ticker="STRONG", strength=0.60)
    out = portfolio.simulate([weak, strong], {
        **CONFIG, "portfolio": {**CONFIG["portfolio"], "max_open_positions": 1}})
    assert [t["ticker"] for t in out["trades"]] == ["STRONG"]


def test_the_old_tie_break_is_still_available_for_comparison():
    """`arrival` is the pre-October behaviour: reward:risk, then list order.
    Kept so a result can be reproduced and the two compared."""
    poor = _candidate(ticker="POOR", reward_risk=1.1, strength=0.9)
    good = _candidate(ticker="GOOD", reward_risk=9.9, strength=0.1)
    out = portfolio.simulate([poor, good], {
        **CONFIG, "portfolio": {**CONFIG["portfolio"], "max_open_positions": 1,
                                "tie_break": "arrival"}})
    assert [t["ticker"] for t in out["trades"]] == ["GOOD"]


def test_ties_are_not_broken_by_the_order_of_the_list():
    """With nothing to tell candidates apart, the choice must not depend on
    where they sat in the universe list — that is what made the published
    results a bet on company size. Reversing the list changes nothing."""
    tied = [_candidate(ticker=f"T{i:02d}") for i in range(12)]
    config = {**CONFIG, "portfolio": {**CONFIG["portfolio"], "max_open_positions": 3}}
    forward = [t["ticker"] for t in portfolio.simulate(tied, config)["trades"]]
    backward = [t["ticker"] for t in portfolio.simulate(tied[::-1], config)["trades"]]
    assert sorted(forward) == sorted(backward)
    assert forward != ["T00", "T01", "T02"] or backward != ["T11", "T10", "T09"]


def test_strength_is_compared_within_a_strategy_not_across_them():
    """A beta of -0.6 and a twelve-month return of 0.4 are not comparable
    numbers. Each strategy's best candidate of the day is its best."""
    pct = portfolio.strength_percentiles(
        [{"s": "low_beta", "v": -0.9}, {"s": "low_beta", "v": -0.5},
         {"s": "ts_momentum", "v": 0.1}, {"s": "ts_momentum", "v": 2.0}],
        lambda c: c["s"], lambda c: c["v"])
    assert sorted(pct.values()) == [0.5, 0.5, 1.0, 1.0]


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


# --- one strategy's share of the book -------------------------------------------

def _same_day(strategy, tickers, reward_risk=2.0):
    return [_candidate(ticker=t, strategy=strategy, reward_risk=reward_risk)
            for t in tickers]


def test_one_strategy_cannot_take_the_whole_book():
    """A paper book ran 72% time-series momentum: it fires on almost anything in
    a rising market, so with nothing to stop it, it was the book. Each position
    here is £15,000 and the exposure budget is £60,000, so four fit."""
    flood = _same_day("ts_momentum", ["A", "B", "C", "D"], reward_risk=3.0)
    others = _same_day("low_beta", ["E", "F"])

    uncapped = portfolio.simulate(flood + others, CONFIG)
    assert {t["strategy"] for t in uncapped["trades"]} == {"ts_momentum"}

    capped = portfolio.simulate(flood + others, {
        **CONFIG, "portfolio": {**CONFIG["portfolio"], "max_strategy_share_pct": 50}})
    held = [t["strategy"] for t in capped["trades"]]
    assert held.count("ts_momentum") == 2, "half of a four-position budget"
    assert held.count("low_beta") == 2, "and the room it left goes to something else"
    assert capped["skipped"]["strategy_share"] == 2


def test_a_named_strategy_can_be_given_its_own_share():
    flood = _same_day("ts_momentum", ["A", "B", "C", "D"], reward_risk=3.0)
    others = _same_day("low_beta", ["E", "F", "G"])
    config = {**CONFIG, "portfolio": {**CONFIG["portfolio"],
                                      "max_strategy_share_pct": 100,
                                      "strategy_share_pct": {"ts_momentum": 25}}}
    held = [t["strategy"] for t in portfolio.simulate(flood + others, config)["trades"]]
    assert held.count("ts_momentum") == 1 and held.count("low_beta") == 3


def test_a_strategy_measured_on_its_own_is_not_throttled_by_the_cap():
    """The cap balances a mix. A single-strategy run is how each rule is
    measured, and capping it there would quietly shrink every standalone result
    to a quarter of the book."""
    alone = _same_day("ts_momentum", ["A", "B", "C", "D"])
    capped = {**CONFIG, "portfolio": {**CONFIG["portfolio"], "max_strategy_share_pct": 25}}
    assert len(portfolio.simulate(alone, capped)["trades"]) == 4


def test_a_full_strategy_hands_the_instrument_to_the_next_claimant():
    """Dropping the instrument instead would leave the book in cash while a
    second strategy that wanted the same instrument was never asked."""
    config = {**CONFIG, "strategy_priority": ["ts_momentum", "low_beta"],
              "portfolio": {**CONFIG["portfolio"], "one_position_per_ticker": True,
                            "max_strategy_share_pct": 50}}
    candidates = ([_candidate(ticker=t, strategy="ts_momentum", strength=0.5)
                   for t in ("A", "B")]
                  + [_candidate(ticker="Z", strategy="ts_momentum", strength=0.1),
                     _candidate(ticker="Z", strategy="low_beta")])

    trades = portfolio.simulate(candidates, config)["trades"]
    assert {t["ticker"]: t["strategy"] for t in trades} == {
        "A": "ts_momentum", "B": "ts_momentum", "Z": "low_beta"}


def test_one_position_per_instrument_goes_to_the_higher_priority_strategy():
    config = {**CONFIG, "strategy_priority": ["low_beta", "ts_momentum"],
              "portfolio": {**CONFIG["portfolio"], "one_position_per_ticker": True}}
    both = _same_day("ts_momentum", ["Z"], reward_risk=3.0) + _same_day("low_beta", ["Z"])

    trades = portfolio.simulate(both, config)["trades"]
    assert [(t["ticker"], t["strategy"]) for t in trades] == [("Z", "low_beta")]
    # Without the switch both open, which is what the per-strategy study needs.
    assert len(portfolio.simulate(both, CONFIG)["trades"]) == 2
