"""The session walker: does it trade the rules honestly?

Every test here targets a way the engine could report a profit it could not
have made. Those are the only failures that matter in a backtest — a bug that
loses money announces itself, and a bug that makes money is indistinguishable
from a discovery.
"""
import pandas as pd
import pytest

from assistant.backtest import intraday_engine as engine

CONFIG = {"intraday": {
    "bar_size": "5 mins",
    "no_new_entries_minutes_before_close": 30,
    "flat_by_minutes_before_close": 5,
    "costs": {"spread_bps": 4.0, "slippage_bps": 2.0, "commission_per_trade": 1.0},
    "strategies": {"opening_range_break": {"enabled": True},
                   "vwap_reversion": {"enabled": False},
                   "intraday_momentum": {"enabled": False},
                   "gap_fade": {"enabled": False}},
}}

# The US session in New York local time: 09:30 to 16:00.
def us_session(closes, *, day="2026-08-21", start="09:30", spread=0.1,
               opens=None, volume=10_000):
    index = pd.date_range(f"{day} {start}", periods=len(closes), freq="5min")
    opens = opens if opens is not None else ([closes[0]] + list(closes[:-1]))
    return pd.DataFrame({
        "Open": opens,
        "High": [max(o, c) + spread for o, c in zip(opens, closes)],
        "Low": [min(o, c) - spread for o, c in zip(opens, closes)],
        "Close": closes,
        "Volume": [volume] * len(closes),
    }, index=index)


def test_nothing_is_carried_overnight():
    """The one failure this whole engine exists to prevent. A session walked to
    its end must leave no position open, whatever the prices did."""
    # A grind straight up all day: the breakout fires and never stops out.
    closes = [100 + i * 0.02 for i in range(78)]      # 09:30 to 16:00
    trades = engine.run_session("TEST", us_session(closes), config=CONFIG)
    assert trades, "expected the breakout to fire"
    assert any(t["exit_reason"] in ("flat_by_close", "time") for t in trades)
    # Every trade closed, and none later than the bell.
    for trade in trades:
        assert trade["exit_time"] <= "2026-08-21 16:00:00"


def test_no_position_opens_after_the_entry_cutoff():
    """The last half hour is for getting out, not in."""
    # Flat all day, then a break in the final twenty minutes.
    closes = [100.0] * 72 + [105.0] * 6
    trades = engine.run_session("TEST", us_session(closes), config=CONFIG)
    assert trades == []


def test_a_stop_and_a_target_in_the_same_bar_resolves_as_the_stop():
    """Five-minute bars carry no tick sequence, so the order is unknowable.
    Assuming the target turns every ambiguous bar into a winner — and ambiguous
    bars cluster in exactly the volatile sessions that decide whether a rule is
    any good."""
    # Range 100.0-100.5 over six bars, break up, then one enormous bar that
    # covers both the stop below and the target above.
    closes = [100.0, 100.3, 100.1, 100.5, 100.2, 100.4, 101.0, 101.0]
    frame = us_session(closes)
    frame.iloc[-1, frame.columns.get_loc("High")] = 120.0
    frame.iloc[-1, frame.columns.get_loc("Low")] = 80.0

    trades = engine.run_session("TEST", frame, config=CONFIG)
    assert len(trades) == 1
    assert trades[0]["exit_reason"] == "stop"
    assert trades[0]["net_pct"] < 0


def test_the_entry_is_the_next_bars_open_not_the_signal_bars_close():
    """The signal is computed from a bar's close, which you only know once the
    bar has ended. Filling at that close is free money the market never
    offered."""
    closes = [100.0, 100.3, 100.1, 100.5, 100.2, 100.4, 101.0, 101.0]
    opens = [100.0, 100.0, 100.3, 100.1, 100.5, 100.2, 100.4, 104.0]
    frame = us_session(closes, opens=opens)

    trades = engine.run_session("TEST", frame, config=CONFIG)
    assert len(trades) == 1
    # Filled off the 104.0 open that followed the 101.0 signal bar — not off
    # 101.0, which is what a look-ahead fill would have taken.
    assert trades[0]["entry"] > 103.0


def test_costs_are_charged_on_both_sides():
    closes = [100.0, 100.3, 100.1, 100.5, 100.2, 100.4, 101.0, 101.0]
    frame = us_session(closes)
    trades = engine.run_session("TEST", frame, config=CONFIG)
    assert len(trades) == 1
    # Gross and net must differ by roughly the full round trip: 4bp of spread
    # plus 2bp of slippage each side = 8bp.
    drag = trades[0]["gross_pct"] - trades[0]["net_pct"]
    assert drag == pytest.approx(0.08, abs=0.01)


def test_a_free_round_trip_is_not_what_the_engine_reports():
    """Guard against a costs block going missing: with no spread configured the
    drag should be zero, so this test failing means the default leaked."""
    closes = [100.0, 100.3, 100.1, 100.5, 100.2, 100.4, 101.0, 101.0]
    free = {"intraday": {**CONFIG["intraday"],
                         "costs": {"spread_bps": 0.0, "slippage_bps": 0.0}}}
    trades = engine.run_session("TEST", us_session(closes), config=free)
    assert trades[0]["gross_pct"] == pytest.approx(trades[0]["net_pct"], abs=1e-9)


def test_a_rule_is_closed_when_its_own_horizon_runs_out():
    """A rule that has run its hour without resolving was wrong, not slow."""
    # The opening range tops out at 100.6, so 101.0 breaks it; holding flat
    # there reaches neither the stop at 99.9 nor the 1R target at 102.1.
    closes = [100.0, 100.3, 100.1, 100.5, 100.2, 100.4] + [101.0] * 60
    trades = engine.run_session("TEST", us_session(closes), config=CONFIG)
    assert trades
    assert trades[0]["exit_reason"] in ("time", "flat_by_close")
    assert trades[0]["held_minutes"] <= 180


def test_only_one_position_is_held_at_a_time():
    closes = [100.0, 100.3, 100.1, 100.5, 100.2, 100.4] + \
             [101.0, 100.0, 101.5, 99.5] * 10
    trades = engine.run_session("TEST", us_session(closes), config=CONFIG)
    times = [(t["entry_time"], t["exit_time"]) for t in trades]
    for (_, out), (nxt, _) in zip(times, times[1:]):
        assert nxt >= out, "a position opened before the previous one closed"


def test_bars_outside_regular_hours_are_not_traded():
    """A pre-market bar has no session left to measure against, and the daily
    bars everything else in the project uses are RTH-only."""
    closes = [100.0, 100.3, 100.1, 100.5, 100.2, 100.4, 101.5]
    premarket = us_session(closes, start="07:00")
    assert engine.run_session("TEST", premarket, config=CONFIG) == []


def test_london_is_walked_against_londons_own_bell():
    """The LSE shuts at 16:30 London. Held against New York's calendar a UK
    position would run four and a half hours past anything tradable."""
    closes = [100 + i * 0.02 for i in range(96)]      # 08:00 onward
    index = pd.date_range("2026-08-21 08:00", periods=len(closes), freq="5min")
    frame = pd.DataFrame({"Open": closes, "High": [c + 0.1 for c in closes],
                          "Low": [c - 0.1 for c in closes], "Close": closes,
                          "Volume": [10_000] * len(closes)}, index=index)
    trades = engine.run_session("TSCO.L", frame, config=CONFIG)
    assert trades
    for trade in trades:
        assert trade["exit_time"] <= "2026-08-21 16:30:00"


# --- reporting ---------------------------------------------------------------

def _spread(mean_gross, mean_net, n=400, sd=0.3, seed=5):
    """A sample with real dispersion around given means, so t means something."""
    import random
    rng = random.Random(seed)
    noise = [rng.gauss(0, sd) for _ in range(n)]
    centre = sum(noise) / n
    return [{"gross_pct": mean_gross + x - centre,
             "net_pct": mean_net + x - centre,
             "r_multiple": 0.1, "held_minutes": 30, "exit_reason": "target"}
            for x in noise]


def test_a_real_edge_smaller_than_its_spread_is_named_as_exactly_that():
    """The full-universe run on 2026-08-23 is why this wording matters: a
    genuine 2.2bp gross edge at t = +4.7 against an 11.4bp cost. 'These rules
    do not work' and 'this venue is too expensive' are opposite conclusions."""
    summary = engine.summarise(_spread(0.03, 0.01, n=1000))
    assert summary["breakeven_bps"] == pytest.approx(3.0, abs=0.1)
    assert summary["t_gross"] > 2.0
    answer = engine.verdict(summary, charged_bps=8.0)
    assert "A real edge, and far too small to trade" in answer
    assert "2.7x" in answer                      # how far short it falls
    assert "Survives its costs" in engine.verdict(summary, charged_bps=1.0)


def test_a_rule_that_loses_before_costs_is_told_no_venue_fixes_it():
    summary = engine.summarise(_spread(-0.05, -0.10))
    assert "Loses money BEFORE costs" in engine.verdict(summary)
    assert "the rule is wrong" in engine.verdict(summary)


def test_a_sample_with_no_dispersion_is_refused_rather_than_judged():
    """Identical trades leave a variance of ~1e-34, and the ratio came back as
    1.3e16 — a divide by zero that did not quite divide by zero, printed beside
    real results as though it were overwhelming evidence."""
    identical = [{"net_pct": 0.01, "gross_pct": 0.03, "r_multiple": 0.1,
                  "held_minutes": 30, "exit_reason": "target"}] * 10
    summary = engine.summarise(identical)
    assert summary["t_gross"] is None
    answer = engine.verdict(summary, charged_bps=8.0)
    assert "Cannot be judged" in answer
    assert "genuine" not in answer               # it must not claim an edge


def test_no_trades_is_reported_as_no_trades_not_as_a_zero():
    assert engine.summarise([])["trades"] == 0
    assert "No trade fired" in engine.verdict(engine.summarise([]))


def test_run_hands_each_session_the_previous_sessions_close():
    """Gao et al.'s rule and the gap fade are both defined against the previous
    close. Without it neither can be expressed, only approximated with the
    session's own open — a different and much weaker signal."""
    day_one = us_session([100.0] * 78, day="2026-08-20")
    day_two = us_session([100.0] * 78, day="2026-08-21")
    frame = pd.concat([day_one, day_two])

    seen = []
    real = engine.run_session

    def spy(ticker, rows, *, prev_close=None, config=None, venue=None):
        seen.append(prev_close)
        return real(ticker, rows, prev_close=prev_close, config=config, venue=venue)

    engine.run_session = spy
    try:
        engine.run({"TEST": frame}, config=CONFIG)
    finally:
        engine.run_session = real

    assert seen[0] is None                 # nothing precedes the first session
    assert seen[1] == pytest.approx(100.0)  # day one's close


# --- is it even different from zero? -----------------------------------------
#
# Run against a pure random walk this engine reports a few basis points of
# gross "edge" — the breakout rule's payoff shape on noise. Without a
# significance test that reads as a finding, and a cost analysis of a number
# that was never different from zero is the most common intraday
# self-deception there is.

def test_a_result_inside_its_own_error_bar_is_called_noise():
    summary = engine.summarise(_spread(0.004, -0.076, n=150, sd=0.3, seed=11))
    assert abs(summary["t_gross"]) < 2.0
    answer = engine.verdict(summary, charged_bps=8.0)
    assert "Nothing there to cost" in answer
    # And it must NOT go on to reason about costs as though the number were real.
    assert "far too small to trade" not in answer


def test_a_result_well_clear_of_its_error_bar_is_judged_on_cost():
    summary = engine.summarise(_spread(0.28, 0.20, n=200, sd=0.3))
    assert abs(summary["t_gross"]) >= 2.0
    assert "Survives its costs" in engine.verdict(summary, charged_bps=8.0)


def test_the_significance_question_is_asked_before_the_cost_question():
    """A strong-looking breakeven on four trades is not a cheap edge, it is
    four trades."""
    trades = [{"net_pct": 0.5, "gross_pct": 0.6, "r_multiple": 1.0,
               "held_minutes": 60, "exit_reason": "target"},
              {"net_pct": -0.4, "gross_pct": -0.3, "r_multiple": -1.0,
               "held_minutes": 60, "exit_reason": "stop"},
              {"net_pct": 0.6, "gross_pct": 0.7, "r_multiple": 1.0,
               "held_minutes": 60, "exit_reason": "target"},
              {"net_pct": -0.3, "gross_pct": -0.2, "r_multiple": -1.0,
               "held_minutes": 60, "exit_reason": "stop"}]
    summary = engine.summarise(trades)
    assert summary["breakeven_bps"] > 0        # looks like an edge
    assert "Nothing there to cost" in engine.verdict(summary, charged_bps=1.0)


def test_gross_and_net_significance_are_different_questions():
    """The finding that made this necessary: net t = -19.83 reads as total
    failure while gross t = +4.74 says the rules found something real. Costs
    are a near-constant subtraction, so the two share a standard error and
    diverge only in sign."""
    summary = engine.summarise(_spread(0.022, -0.092, n=2000, sd=0.5))
    assert summary["t_gross"] > 2          # a real edge exists
    assert summary["t_stat"] < -2          # and it loses money anyway
    assert "A real edge, and far too small to trade" in engine.verdict(
        summary, charged_bps=11.4)


def test_a_single_trade_has_no_t_statistic_rather_than_a_fake_one():
    one = [{"net_pct": 0.5, "gross_pct": 0.6, "r_multiple": 1.0,
            "held_minutes": 60, "exit_reason": "target"}]
    assert engine.summarise(one)["t_stat"] is None
    assert engine.summarise(one)["t_gross"] is None


# --- one cost for fifteen hundred instruments is not a cost model ------------

def _walk(bounce_bps, days=8, seed=1):
    import numpy as np
    rng = np.random.default_rng(seed)
    half = bounce_bps / 2 / 10_000
    rows = []
    for day in pd.bdate_range("2026-07-06", periods=days):
        mid = 50 + np.cumsum(rng.normal(0, 0.05, 78))
        side = np.where(np.arange(78) % 2 == 0, 1 + half, 1 - half)
        close = mid * side
        idx = pd.date_range(f"{day.date()} 09:30", periods=78, freq="5min")
        rows.append(pd.DataFrame({
            "Open": close, "High": mid * (1 + half), "Low": mid * (1 - half),
            "Close": close, "Volume": rng.integers(1_000, 20_000, 78)}, index=idx))
    return pd.concat(rows)


def test_untradeably_wide_instruments_are_dropped_from_the_study():
    """A name whose spread is wider than the cap is not traded at all. Left in
    and charged the flat rate it contributes noise dressed as signal, and the
    wider the universe the more of that noise there is."""
    frames = {"TIGHT": _walk(3, seed=1), "WIDE": _walk(180, seed=2)}
    out = engine.run(frames, config={"intraday": {**CONFIG["intraday"],
                                                  "estimate_spreads": True,
                                                  "max_spread_bps": 40.0}})
    assert "WIDE" not in out["by_ticker"]
    assert out["liquidity"]["excluded"] == 1
    assert "WIDE" in out["liquidity"]["excluded_names"]


def test_each_instrument_is_charged_its_own_measured_spread():
    frames = {"TIGHT": _walk(3, seed=1), "MID": _walk(25, seed=2)}
    out = engine.run(frames, config={"intraday": {**CONFIG["intraday"],
                                                  "estimate_spreads": True,
                                                  "max_spread_bps": 60.0}})
    tight = out["by_ticker"]["TIGHT"]["spread_bps"]
    mid = out["by_ticker"]["MID"]["spread_bps"]
    assert mid > tight, (tight, mid)


def test_switching_the_estimate_off_falls_back_to_the_flat_config_number():
    frames = {"TIGHT": _walk(3, seed=1), "WIDE": _walk(180, seed=2)}
    out = engine.run(frames, config={"intraday": {**CONFIG["intraday"],
                                                  "estimate_spreads": False}})
    # Nothing measured, nothing excluded — and the wide name is still in,
    # charged 4bp it could not possibly trade at. Which is the point.
    assert out["liquidity"] is None
    assert out["spread_source"] == "flat, from config.yaml"


def test_the_headline_cost_is_weighted_by_where_the_trades_actually_were():
    """An unweighted mean lets a thousand names that never fired a signal drag
    the reported cost toward the illiquid tail, and a rule that only trades the
    tightest names would be judged against a spread it never paid."""
    frames = {"TIGHT": _walk(3, seed=1), "MID": _walk(25, seed=2)}
    out = engine.run(frames, config={"intraday": {**CONFIG["intraday"],
                                                  "estimate_spreads": True,
                                                  "max_spread_bps": 60.0}})
    charged = out["overall"]["charged_bps"]
    per = {t: r for t, r in out["by_ticker"].items() if r.get("trades")}
    if len(per) == 2:
        widths = sorted(r["spread_bps"] for r in per.values())
        assert widths[0] <= charged <= widths[1]
