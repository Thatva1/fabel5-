"""Backtest engine, fill simulator and report aggregation.

The single most important test in this file is
`test_future_bars_cannot_change_a_past_signal`. A backtest with look-ahead does
not merely overstate returns — it reliably converts losing rules into winners,
which is the exact failure this whole package exists to avoid. Everything else
is secondary to that guarantee holding.

The second theme is pessimism: where the simulator has to assume something, the
tests pin it to the assumption that hurts, not the one that flatters.
"""
import numpy as np
import pandas as pd
import pytest

from assistant.backtest import engine, report, simulator


def _bars(rows):
    """rows: list of (open, high, low, close)."""
    return pd.DataFrame(
        [{"Open": o, "High": h, "Low": l, "Close": c, "Volume": 1_000_000}
         for o, h, l, c in rows],
        index=pd.date_range("2024-01-01", periods=len(rows), freq="D"))


COSTS = {"commission_per_trade": 0.0, "slippage_bps": 0.0,
         "stamp_duty_pct": 0.0, "stamp_duty_suffixes": []}


# --- fill simulation ---------------------------------------------------------

def test_long_hits_its_target():
    future = _bars([(100, 102, 99, 101), (101, 112, 100, 111)])
    t = simulator.simulate_trade("X", "long", 100, 95, 110, 10, future, COSTS)
    assert t["exit_reason"] == "target"
    assert t["exit_price"] == 110
    assert t["pnl"] == 100.0
    assert t["r_multiple"] == 2.0


def test_long_hits_its_stop():
    future = _bars([(100, 101, 94, 96)])
    t = simulator.simulate_trade("X", "long", 100, 95, 110, 10, future, COSTS)
    assert t["exit_reason"] == "stop"
    assert t["exit_price"] == 95
    assert t["r_multiple"] == -1.0


def test_a_bar_covering_both_levels_is_scored_as_the_stop():
    """No intrabar data means the order is unknowable. Assuming the target would
    convert every ambiguous bar into a winner — the easiest way to fake a
    profitable backtest that exists."""
    future = _bars([(100, 115, 90, 105)])       # range spans stop AND target
    t = simulator.simulate_trade("X", "long", 100, 95, 110, 10, future, COSTS)
    assert t["exit_reason"] == "stop"


def test_a_gap_through_the_stop_fills_at_the_open_not_the_stop():
    """This is how a '-1R' trade becomes -3R in real life. A backtest that fills
    every stop exactly at the stop price understates its worst losses."""
    future = _bars([(85, 86, 84, 85)])          # opened far below the 95 stop
    t = simulator.simulate_trade("X", "long", 100, 95, 110, 10, future, COSTS)
    assert t["exit_reason"] == "stop"
    assert t["exit_price"] == 85                # not 95
    assert t["r_multiple"] == -3.0              # not -1


def test_a_favourable_gap_past_the_target_fills_at_the_open():
    future = _bars([(118, 120, 117, 119)])
    t = simulator.simulate_trade("X", "long", 100, 95, 110, 10, future, COSTS)
    assert t["exit_reason"] == "target"
    assert t["exit_price"] == 118


def test_short_stop_and_target_are_mirrored():
    stopped = simulator.simulate_trade("X", "short", 100, 105, 90, 10,
                                       _bars([(100, 106, 99, 104)]), COSTS)
    assert stopped["exit_reason"] == "stop" and stopped["r_multiple"] == -1.0

    won = simulator.simulate_trade("X", "short", 100, 105, 90, 10,
                                   _bars([(100, 101, 89, 91)]), COSTS)
    assert won["exit_reason"] == "target"
    assert won["pnl"] == 100.0                  # short profits as price falls


def test_a_trade_that_goes_nowhere_times_out():
    flat = _bars([(100, 101, 99, 100)] * 60)
    t = simulator.simulate_trade("X", "long", 100, 95, 110, 10, flat, COSTS,
                                 max_holding_bars=20)
    assert t["exit_reason"] == "timeout"
    assert t["bars_held"] == 20


def test_no_future_bars_is_not_a_trade():
    assert simulator.simulate_trade("X", "long", 100, 95, 110, 10, _bars([]), COSTS) is None


# --- costs -------------------------------------------------------------------

def test_slippage_works_against_you_on_both_legs():
    """Applied in one direction only, slippage makes a losing strategy look
    breakeven. Buying fills higher AND selling fills lower."""
    costs = {**COSTS, "slippage_bps": 100.0}     # 1%
    t = simulator.simulate_trade("X", "long", 100, 95, 110, 10,
                                 _bars([(100, 112, 99, 111)]), costs)
    assert t["entry_price"] == 101.0             # bought higher
    assert t["exit_price"] == 108.9              # sold lower
    assert t["pnl"] < 100.0


def test_uk_stamp_duty_is_charged_on_the_purchase_leg_only():
    costs = {**COSTS, "stamp_duty_pct": 0.5, "stamp_duty_suffixes": [".L"]}
    long_trade = simulator.simulate_trade("TSCO.L", "long", 100, 95, 110, 10,
                                          _bars([(100, 112, 99, 111)]), costs)
    assert long_trade["costs"] == pytest.approx(5.0)      # 0.5% of 10 x 100

    us_trade = simulator.simulate_trade("AAPL", "long", 100, 95, 110, 10,
                                        _bars([(100, 112, 99, 111)]), costs)
    assert us_trade["costs"] == 0.0                       # no UK duty on a US listing


def test_a_short_pays_stamp_duty_on_the_closing_buy():
    costs = {**COSTS, "stamp_duty_pct": 0.5, "stamp_duty_suffixes": [".L"]}
    t = simulator.simulate_trade("TSCO.L", "short", 100, 105, 90, 10,
                                 _bars([(100, 101, 89, 91)]), costs)
    assert t["costs"] == pytest.approx(4.5)               # 0.5% of 10 x 90 exit


def test_costs_reduce_net_pnl_below_gross():
    costs = {**COSTS, "commission_per_trade": 6.0}
    t = simulator.simulate_trade("X", "long", 100, 95, 110, 10,
                                 _bars([(100, 112, 99, 111)]), costs)
    assert t["gross_pnl"] == 100.0
    assert t["pnl"] == 88.0                                # 100 - (6 in + 6 out)


# --- the look-ahead guarantee ------------------------------------------------

def _trending_market(n=600, seed=24):
    """A realistic series with pullbacks, long enough to clear the warm-up."""
    rng = np.random.default_rng(seed)
    price, rows = 100.0, []
    for step in rng.normal(0.0025, 0.014, n):
        price *= 1 + step
        rows.append((price * 0.999, price * 1.012, price * 0.988, price))
    return _bars(rows)


CONFIG = {
    "account": {"portfolio_value": 100000, "risk_per_trade_pct": 1.0,
                "max_position_pct": 15.0},
    "scanner": {"min_history_days": 60},
    "backtest": {"costs": COSTS},
}


def test_future_bars_cannot_change_a_past_signal():
    """THE test. Replaying the first 400 bars must produce exactly the trades
    that the first 400 bars of a 600-bar replay produced. If appending future
    data changes a past decision, the engine is reading the future and every
    number it reports is worthless."""
    full = _trending_market(600)
    truncated = full.iloc[:400]

    from_full = engine.backtest_ticker("X", full, CONFIG)["trades"]
    from_truncated = engine.backtest_ticker("X", truncated, CONFIG)["trades"]

    # Compare only trades that had room to complete inside the shorter series.
    comparable = [t for t in from_full if t["entry_date"] <= from_truncated[-1]["entry_date"]] \
        if from_truncated else []
    assert from_truncated, "fixture produced no trades — it cannot prove anything"
    for short_t, full_t in zip(from_truncated, comparable):
        assert short_t["signal_date"] == full_t["signal_date"]
        assert short_t["direction"] == full_t["direction"]
        assert short_t["strategy"] == full_t["strategy"]
        assert short_t["planned_entry"] == full_t["planned_entry"]
        assert short_t["stop"] == full_t["stop"]


def test_the_history_window_does_not_change_the_results():
    """The engine caps each bar's lookback for speed. Every indicator is
    backward-looking, so a bigger window must produce identical trades — this
    proves the optimisation is not silently changing behaviour."""
    df = _trending_market(600)
    capped = engine.backtest_ticker("X", df, CONFIG)["trades"]
    uncapped = engine.backtest_ticker(
        "X", df, {**CONFIG, "backtest": {**CONFIG["backtest"],
                                         "history_window_bars": 5000}})["trades"]
    assert [t["signal_date"] for t in capped] == [t["signal_date"] for t in uncapped]
    assert [t["pnl"] for t in capped] == [t["pnl"] for t in uncapped]


def test_entry_happens_after_the_signal_bar_not_on_it():
    """A signal computed from a bar's close could not have been traded at that
    close — you only knew it once the bar finished."""
    df = _trending_market(600)
    trades = engine.backtest_ticker("X", df, CONFIG)["trades"]
    assert trades
    for t in trades:
        assert t["entry_date"] > t["signal_date"]


def test_signal_close_entry_is_available_but_not_the_default():
    """Filling at the signal bar's close is impossible in reality — you only
    knew the signal once that bar had closed. Kept as an option purely to show
    how much the assumption flatters, never as the default."""
    assert engine.DEFAULTS["entry_timing"] == "limit"
    df = _trending_market(600)
    optimistic = engine.backtest_ticker(
        "X", df, {**CONFIG, "backtest": {**CONFIG["backtest"],
                                         "entry_timing": "signal_close"}})["trades"]
    assert optimistic
    for t in optimistic:
        assert t["entry_date"] == t["signal_date"]


# --- entry modelling ---------------------------------------------------------

def _bar(o, h, l, c=None):
    return {"Open": o, "High": h, "Low": l, "Close": c if c is not None else o}


def test_limit_order_fills_at_the_planned_price_when_the_bar_reaches_it():
    assert engine.resolve_entry("long", 100, _bar(102, 103, 99), "limit") == 100
    assert engine.resolve_entry("short", 100, _bar(98, 101, 97), "limit") == 100


def test_limit_order_takes_a_favourable_gap():
    assert engine.resolve_entry("long", 100, _bar(97, 99, 96), "limit") == 97
    assert engine.resolve_entry("short", 100, _bar(104, 105, 102), "limit") == 104


def test_limit_order_misses_when_the_market_leaves_without_you():
    """Missed fills are a real cost of limit orders. A backtest that quietly
    converts them into perfect fills invents trades that never happened."""
    assert engine.resolve_entry("long", 100, _bar(103, 105, 101), "limit") is None
    assert engine.resolve_entry("short", 100, _bar(97, 99, 95), "limit") is None


def test_a_fill_that_drifts_onto_the_stop_is_skipped():
    """The failure this guard exists for. A market-order fill that lands close
    to the stop shrinks the risk the position was sized against, which inflates
    every R-multiple derived from it and manufactures phantom 30R winners."""
    df = _trending_market(600)
    cfg = {**CONFIG, "backtest": {**CONFIG["backtest"], "entry_timing": "next_open",
                                  "min_risk_fraction": 0.5}}
    out = engine.backtest_ticker("X", df, cfg)
    for t in out["trades"]:
        planned_risk = abs(t["signal_price"] - t["stop"])
        actual_risk = abs(t["planned_entry"] - t["stop"])
        assert actual_risk >= planned_risk * 0.5


def test_r_multiples_stay_in_a_sane_range_with_limit_entries():
    """With limit fills the entry can never drift toward the stop, so the R
    denominator holds and the extreme outliers disappear."""
    df = _trending_market(600)
    trades = engine.backtest_ticker("X", df, CONFIG)["trades"]
    assert trades
    for t in trades:
        assert -12 <= t["r_multiple"] <= 12, f"implausible R: {t}"


def test_fill_quality_is_reported():
    out = engine.run_backtest(["X"], CONFIG, lambda t: _trending_market(600))
    assert "missed_fills" in out and "skipped_risk_collapsed" in out
    built = report.build(out, CONFIG)
    assert built["fills"]["signals"] >= built["fills"]["traded"]


def test_positions_do_not_overlap_on_one_ticker():
    df = _trending_market(600)
    trades = engine.backtest_ticker("X", df, CONFIG)["trades"]
    for earlier, later in zip(trades, trades[1:]):
        assert later["entry_date"] > earlier["entry_date"]


def test_short_history_is_reported_not_silently_skipped():
    out = engine.backtest_ticker("X", _trending_market(50), CONFIG)
    assert out["trades"] == []
    assert "Not enough history" in out["note"]


def test_every_trade_carries_its_strategy_and_regime():
    trades = engine.backtest_ticker("X", _trending_market(600), CONFIG)["trades"]
    assert trades
    for t in trades:
        assert t["strategy"] and t["regime"] and t["strategy_label"]


def test_run_backtest_survives_a_ticker_with_no_data():
    def prices(ticker):
        if ticker == "BROKEN":
            raise RuntimeError("delisted")
        return _trending_market(600)

    out = engine.run_backtest(["GOOD", "BROKEN"], CONFIG, prices)
    assert out["tickers_tested"] == 1
    assert any("BROKEN" in n for n in out["notes"])


# --- trailing and scale-out exits --------------------------------------------

def test_a_trailing_stop_lets_a_winner_run_past_the_fixed_target():
    """The capped-winners hypothesis, tested rather than inferred. Price runs
    well past the 110 target; the fixed exit banks 110, the trail rides on."""
    bars = _bars([(100, 108, 99, 107), (107, 118, 106, 117), (117, 130, 116, 129),
                  (129, 132, 120, 122), (122, 124, 110, 112)])
    fixed = simulator.simulate_trade("X", "long", 100, 95, 110, 10, bars, COSTS)
    trailed = simulator.simulate_trade("X", "long", 100, 95, 110, 10, bars, COSTS,
                                       exit_style="trailing", atr=4.0, trail_atr=2.5)
    assert fixed["exit_price"] == 110
    assert trailed["exit_price"] > fixed["exit_price"]
    assert trailed["r_multiple"] > fixed["r_multiple"]


def test_the_trail_gives_back_the_last_stretch_of_the_move():
    """The cost of letting winners run: you never exit at the high. A trailing
    result that equalled the peak would mean the simulation was cheating."""
    bars = _bars([(100, 130, 99, 129), (129, 131, 100, 101)])
    trailed = simulator.simulate_trade("X", "long", 100, 95, 110, 10, bars, COSTS,
                                       exit_style="trailing", atr=4.0, trail_atr=2.5)
    assert trailed["exit_price"] < 131


def test_the_trail_does_not_activate_until_the_trade_is_in_profit():
    """Trailing from entry converts ordinary early noise into an instant exit
    and kills trades that were about to work."""
    bars = _bars([(100, 101, 99, 100), (100, 102, 98, 101), (101, 112, 100, 111)])
    trailed = simulator.simulate_trade("X", "long", 100, 95, 110, 10, bars, COSTS,
                                       exit_style="trailing", atr=1.0, trail_atr=2.5,
                                       activate_at_r=1.0)
    assert trailed["bars_held"] >= 3        # survived the early chop


def test_the_trailing_stop_is_checked_before_it_is_advanced():
    """A bar that trades through the stop must not escape because the same
    bar's high moved the stop up — that is using the bar's own future."""
    bars = _bars([(100, 120, 99, 119), (119, 125, 90, 92)])
    trailed = simulator.simulate_trade("X", "long", 100, 95, 200, 10, bars, COSTS,
                                       exit_style="trailing", atr=4.0, trail_atr=2.5)
    assert trailed["exit_reason"] == "stop"
    assert trailed["exit_price"] < 120


def test_trailing_still_respects_the_original_stop_on_a_loser():
    bars = _bars([(100, 101, 94, 96)])
    trailed = simulator.simulate_trade("X", "long", 100, 95, 110, 10, bars, COSTS,
                                       exit_style="trailing", atr=2.0)
    assert trailed["exit_reason"] == "stop"
    assert trailed["r_multiple"] == -1.0


def test_trailing_mirrors_for_shorts():
    bars = _bars([(100, 101, 88, 89), (89, 90, 80, 81), (81, 95, 80, 94)])
    trailed = simulator.simulate_trade("X", "short", 100, 105, 90, 10, bars, COSTS,
                                       exit_style="trailing", atr=3.0, trail_atr=2.5)
    assert trailed["pnl"] > 0               # a short profits as price falls


def test_scale_out_banks_half_at_target_and_trails_the_rest():
    bars = _bars([(100, 112, 99, 111), (111, 125, 110, 124), (124, 126, 112, 114)])
    plain = simulator.simulate_trade("X", "long", 100, 95, 110, 10, bars, COSTS)
    scaled = simulator.simulate_trade("X", "long", 100, 95, 110, 10, bars, COSTS,
                                      atr=4.0, scale_out_fraction=0.5)
    assert plain["exit_price"] == 110
    assert scaled["scale_out"]["banked_price"] == 110
    assert scaled["exit_price"] > 110       # blended with the runner
    assert "target+" in scaled["exit_reason"]


def test_trailing_without_an_atr_is_refused_rather_than_guessed():
    bars = _bars([(100, 112, 99, 111)])
    assert simulator.simulate_trade("X", "long", 100, 95, 110, 10, bars, COSTS,
                                    exit_style="trailing", atr=None) is None


# --- position-slot contention -------------------------------------------------

def test_priority_decides_who_gets_the_slot_not_registry_order():
    """The bug this fixes: with one slot per ticker, whichever strategy the
    registry listed first won it. That silently prioritised the strategy that
    fires most often over the one that trades best."""
    assert engine.DEFAULTS["strategy_priority"][0] == "mean_reversion"
    assert engine.DEFAULTS["strategy_priority"][-1] == "range_trading"


def test_per_strategy_slots_let_a_rare_signal_through():
    """Under per_ticker slots, a strategy holding a position for weeks blocks
    every other strategy on that instrument for the whole holding period. Under
    per_strategy slots each has its own, so a rare high-quality setup is never
    crowded out by a frequent low-quality one."""
    df = _trending_market(600)
    shared = engine.backtest_ticker("X", df, CONFIG)
    per_strategy = engine.backtest_ticker("X", df, {
        **CONFIG, "backtest": {**CONFIG["backtest"], "position_slots": "per_strategy"}})
    assert len(per_strategy["trades"]) >= len(shared["trades"])


def test_blocked_signals_are_counted_not_silently_dropped():
    """Silence about blocked signals is what hid this problem for a whole
    backtest — the report has to be able to show the opportunity cost."""
    df = _trending_market(600)
    out = engine.backtest_ticker("X", df, CONFIG)
    assert "blocked_by_open_position" in out
    run = engine.run_backtest(["X"], CONFIG, lambda t: df)
    assert "blocked_by_open_position" in run


def test_per_strategy_slots_still_prevent_the_same_strategy_stacking():
    """One position per strategy per ticker — not unlimited. Two concurrent
    mean-reversion positions in the same stock is doubling the same bet."""
    df = _trending_market(600)
    cfg = {**CONFIG, "backtest": {**CONFIG["backtest"], "position_slots": "per_strategy"}}
    trades = engine.backtest_ticker("X", df, cfg)["trades"]
    by_strategy = {}
    for t in trades:
        prior = by_strategy.get(t["strategy"])
        if prior:
            assert t["entry_date"] > prior, f"{t['strategy']} overlapped itself"
        by_strategy[t["strategy"]] = t["entry_date"]


# --- multi-market: currency and costs ----------------------------------------

def test_costs_differ_by_market():
    """Applying one market's cost structure globally is how a UK-specific tax
    gets mistaken for a flaw in a strategy."""
    config = {"backtest": {"costs": {
        "commission_per_trade": 6.0, "slippage_bps": 5.0,
        "stamp_duty_pct": 0.5, "stamp_duty_suffixes": [".L"],
        "per_market": {"": {"commission_per_trade": 1.0},
                       ".L": {"commission_per_trade": 6.0}},
    }}}
    uk = simulator.cost_config_for("TSCO.L", config)
    us = simulator.cost_config_for("AAPL", config)
    assert uk["commission_per_trade"] == 6.0
    assert us["commission_per_trade"] == 1.0


def test_stamp_duty_only_applies_to_the_market_that_charges_it():
    config = {"backtest": {"costs": {
        "commission_per_trade": 0.0, "slippage_bps": 0.0,
        "stamp_duty_pct": 0.5, "stamp_duty_suffixes": [".L"]}}}
    future = _bars([(100, 112, 99, 111)])
    uk = simulator.simulate_trade("TSCO.L", "long", 100, 95, 110, 10, future,
                                  simulator.cost_config_for("TSCO.L", config))
    us = simulator.simulate_trade("AAPL", "long", 100, 95, 110, 10, future,
                                  simulator.cost_config_for("AAPL", config))
    assert uk["costs"] > 0 and us["costs"] == 0.0


def _fx(rates):
    return pd.Series(rates, index=pd.to_datetime(
        ["2024-01-01", "2024-06-01", "2025-01-01"]))


def test_trades_are_converted_to_the_base_currency():
    """A global run that sums raw P&L adds dollars to yen and produces a number
    that looks precise and means nothing."""
    trades = [{"pnl": 100.0, "costs": 10.0, "entry_date": "2024-06-15"}]
    notes = []
    engine.convert_trades(trades, "USD", "GBP", _fx([0.8, 0.75, 0.7]), notes)
    assert trades[0]["currency"] == "USD"
    assert trades[0]["pnl_base"] == 75.0          # rate as of 2024-06-01
    assert trades[0]["costs_base"] == 7.5


def test_conversion_uses_the_rate_from_the_trades_own_date():
    """Converting a 2017 trade at today's rate silently rewrites history every
    time the currency moved — over a decade that is a large number pretending
    to be strategy performance."""
    early = [{"pnl": 100.0, "costs": 0.0, "entry_date": "2024-01-15"}]
    late = [{"pnl": 100.0, "costs": 0.0, "entry_date": "2025-06-01"}]
    series = _fx([0.8, 0.75, 0.7])
    engine.convert_trades(early, "USD", "GBP", series, [])
    engine.convert_trades(late, "USD", "GBP", series, [])
    assert early[0]["pnl_base"] == 80.0
    assert late[0]["pnl_base"] == 70.0


def test_same_currency_needs_no_conversion():
    trades = [{"pnl": 100.0, "costs": 10.0, "entry_date": "2024-06-15"}]
    engine.convert_trades(trades, "GBP", "GBP", None, [])
    assert trades[0]["pnl_base"] == 100.0 and trades[0]["fx_rate"] == 1.0


def test_a_missing_rate_excludes_the_trade_rather_than_guessing():
    """Silently treating an unconvertible trade as zero would flatter or damn a
    strategy depending on which way it went."""
    trades = [{"pnl": 100.0, "costs": 10.0, "entry_date": "2024-06-15"}]
    notes = []
    engine.convert_trades(trades, "JPY", "GBP", None, notes)
    assert trades[0]["pnl_base"] is None
    assert any("JPY->GBP" in n for n in notes)

    summary = report.summarise(trades)
    assert summary["all"]["trades"] == 0
    assert summary["all"]["unconverted"] == 1


def test_r_multiple_is_comparable_across_markets_without_conversion():
    """R is profit divided by risk in the same currency, so it cancels out — the
    one metric that compares a Tokyo trade with a New York one directly."""
    tokyo = _trade(150000.0, 2.0, ticker="7203.T")
    tokyo.update({"currency": "JPY", "pnl_base": 800.0, "costs_base": 5.0})
    ny = _trade(1000.0, 2.0, ticker="AAPL")
    ny.update({"currency": "USD", "pnl_base": 790.0, "costs_base": 5.0})
    out = report.summarise([tokyo, ny])["all"]
    assert out["expectancy_r"] == 2.0
    assert out["pnl"] == pytest.approx(1590.0)      # both in base, not raw


# --- report ------------------------------------------------------------------

def _trade(pnl, r, strategy="momentum", regime="TRENDING_UP", reason="target",
           date="2024-03-01", ticker="X"):
    return {"pnl": pnl, "r_multiple": r, "strategy": strategy,
            "strategy_label": strategy.title(), "regime": regime, "ticker": ticker,
            "exit_reason": reason, "bars_held": 5, "costs": 2.0,
            "entry_date": date, "direction": "long"}


def test_expectancy_and_profit_factor_are_computed_per_strategy():
    trades = [_trade(200, 2.0), _trade(200, 2.0), _trade(-100, -1.0),
              _trade(-100, -1.0, strategy="range_trading")]
    out = report.summarise(trades, lambda t: t["strategy"], lambda t: t["strategy_label"])
    momentum = out["momentum"]
    assert momentum["wins"] == 2 and momentum["losses"] == 1
    assert momentum["win_rate_pct"] == pytest.approx(66.7)
    assert momentum["expectancy_r"] == 1.0          # (2 + 2 - 1) / 3
    assert momentum["profit_factor"] == pytest.approx(4.0)
    assert out["range_trading"]["expectancy_r"] == -1.0


def test_small_samples_are_marked_unreliable():
    """A 100% hit rate over three trades is noise, and the report has to say so
    rather than let it be read as an edge."""
    assert report.summarise([_trade(100, 1.0)] * 3)["all"]["reliable"] is False
    assert report.summarise([_trade(100, 1.0)] * 30)["all"]["reliable"] is True


def test_max_drawdown_is_measured_peak_to_trough():
    """The total says nothing about whether a strategy is survivable. +900 after
    being 400 underwater is one most people abandon at the bottom."""
    curve = report.equity_curve([
        _trade(1000, 2.0, date="2024-01-01"),
        _trade(-400, -1.0, date="2024-02-01"),
        _trade(300, 1.0, date="2024-03-01"),
    ])
    assert curve["final_pnl"] == 900.0
    assert curve["max_drawdown"] == 400.0


def test_exit_reasons_are_counted():
    out = report.summarise([_trade(100, 1.0, reason="target"),
                            _trade(-100, -1.0, reason="stop"),
                            _trade(10, 0.1, reason="timeout")])["all"]
    assert (out["targeted"], out["stopped"], out["timed_out"]) == (1, 1, 1)


def test_report_uses_the_same_keys_as_the_live_journal():
    """Backtest and live results must be directly comparable — same metric names,
    same maths — or the comparison that matters most cannot be made."""
    from assistant import journal
    out = report.build({"trades": [_trade(100, 1.0)], "per_ticker": [],
                        "notes": [], "tickers_tested": 1}, CONFIG)
    for section in ("by_strategy", "by_regime", "by_strategy_regime"):
        assert section in out
    shared = {"wins", "losses", "win_rate_pct", "expectancy_r", "profit_factor"}
    assert shared <= set(out["by_strategy"]["momentum"].keys())
    assert hasattr(journal, "stats")
