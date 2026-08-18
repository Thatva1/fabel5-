"""Intraday edge research — and the cost arithmetic that decides it."""
import numpy as np
import pandas as pd
import pytest

from assistant.research import intraday


def session(prices, day="2026-08-17", start_hour=9, volume=1000):
    """One trading session of 5-minute bars from a list of closes."""
    index = pd.to_datetime([
        f"{day} {start_hour + (i * 5) // 60:02d}:{(i * 5) % 60:02d}:00"
        for i in range(len(prices))])
    return pd.DataFrame({
        "Open": prices, "High": [p * 1.001 for p in prices],
        "Low": [p * 0.999 for p in prices], "Close": prices,
        "Volume": [volume] * len(prices)}, index=index)


def trending_day(day, start=100.0, step=0.5, n=30):
    return session([start + step * i for i in range(n)], day=day)


# -- session splitting -----------------------------------------------------

def test_sessions_split_by_calendar_day():
    frame = pd.concat([trending_day("2026-08-17"), trending_day("2026-08-18")])
    assert len({d for d, _, _ in intraday._sessions(frame)}) == 2


def test_a_stub_session_is_skipped():
    """Too few bars to form an opening range is not a tradable day."""
    frame = session([100, 101, 102])
    assert list(intraday._sessions(frame)) == []


def test_the_first_session_has_no_previous_close():
    frame = pd.concat([trending_day("2026-08-17"), trending_day("2026-08-18")])
    days = list(intraday._sessions(frame))
    assert days[0][2] is None
    assert days[1][2] is not None      # carried from the day before


# -- the published once-a-day rules ----------------------------------------

def test_market_intraday_momentum_reads_from_the_PREVIOUS_close():
    """Gao et al. measure the first half hour from the prior close. Using the
    session's own open instead discards the overnight gap, which is much of
    what the published signal reads."""
    rows = session([100.0] * 30)                  # flat session, no open-to-now move
    up = intraday.market_intraday_momentum(rows, prev_close=95.0)
    assert up and up[0]["direction"] == "long"    # gapped up vs prior close
    down = intraday.market_intraday_momentum(rows, prev_close=105.0)
    assert down and down[0]["direction"] == "short"


def test_market_intraday_momentum_needs_a_previous_close():
    assert intraday.market_intraday_momentum(trending_day("2026-08-17"), None) == []


def test_market_intraday_momentum_takes_one_trade_into_the_close():
    trades = intraday.market_intraday_momentum(trending_day("2026-08-17"), prev_close=99.0)
    assert len(trades) == 1


def test_gap_fade_sells_a_gap_up_and_buys_a_gap_down():
    rows = session([105.0] * 20)
    assert intraday.gap_fade(rows, prev_close=100.0)[0]["direction"] == "short"
    assert intraday.gap_fade(rows, prev_close=110.0)[0]["direction"] == "long"


def test_gap_fade_ignores_a_small_gap():
    rows = session([100.1] * 20)
    assert intraday.gap_fade(rows, prev_close=100.0, min_gap_pct=0.5) == []


def test_intraday_mean_reversion_fades_a_band_break():
    prices = [100.0] * 20 + [94.0] * 3 + [100.0] * 8
    trades = intraday.intraday_mean_reversion(session(prices), lookback=14, band=2.0)
    assert trades and trades[0]["direction"] == "long"


def test_last_hour_momentum_carries_the_session_direction():
    trades = intraday.last_hour_momentum(trending_day("2026-08-17", n=30))
    assert trades and trades[0]["direction"] == "long"


def test_every_registered_strategy_accepts_the_same_call():
    """They are invoked uniformly by evaluate(); a signature drift would show
    up as one strategy silently producing nothing."""
    rows = trending_day("2026-08-17")
    for name, fn in intraday.STRATEGIES.items():
        result = fn(rows, 99.0)
        assert isinstance(result, list), name


# -- strategies ------------------------------------------------------------

def test_opening_range_break_goes_long_on_an_upside_break():
    trades = intraday.opening_range_break(trending_day("2026-08-17"))
    assert len(trades) == 1
    assert trades[0]["direction"] == "long"


def test_opening_range_break_goes_short_on_a_downside_break():
    falling = session([100 - 0.5 * i for i in range(30)])
    trades = intraday.opening_range_break(falling)
    assert trades[0]["direction"] == "short"


def test_opening_range_break_stays_out_of_a_flat_day():
    flat = session([100.0] * 30)
    assert intraday.opening_range_break(flat) == []


def test_first_hour_continuation_takes_one_trade_per_day():
    trades = intraday.first_hour_continuation(trending_day("2026-08-17"))
    assert len(trades) == 1
    assert trades[0]["direction"] == "long"


def test_vwap_reversion_fades_a_stretch():
    # Drop hard, then recover — a stretch below VWAP that comes back.
    prices = [100] * 10 + [96] * 3 + [100] * 10
    trades = intraday.vwap_reversion(session(prices), threshold_pct=0.4)
    assert trades and trades[0]["direction"] == "long"


# -- costs -----------------------------------------------------------------

def test_cost_is_charged_round_trip_not_one_side():
    """Halving the spread to 'one side' is the commonest way an intraday
    backtest flatters itself."""
    trades = [{"direction": "long", "entry": 100.0, "exit": 101.0}]
    gross = intraday._returns(trades, cost_bps=0.0)[0]
    net = intraday._returns(trades, cost_bps=10.0)[0]
    assert gross == pytest.approx(1.0, abs=1e-9)
    assert net == pytest.approx(0.9, abs=1e-9)      # 10 bps = 0.10%


def test_a_short_profits_when_price_falls():
    trades = [{"direction": "short", "entry": 100.0, "exit": 99.0}]
    assert intraday._returns(trades, 0.0)[0] == pytest.approx(1.0, abs=1e-9)


def test_breakeven_cost_is_the_gross_edge_in_basis_points():
    """The number the whole exercise turns on."""
    trades = [{"direction": "long", "entry": 100.0, "exit": 100.2}]   # +0.20% = 20bps
    assert intraday.breakeven_cost_bps(trades) == pytest.approx(20.0, abs=0.01)


def test_an_edge_below_the_cost_is_reported_as_not_tradable():
    frames = {"X": pd.concat([trending_day(f"2026-08-{d:02d}", step=0.001)
                              for d in range(1, 12)])}
    out = intraday.evaluate(frames, cost_bps=50.0,
                            strategies=["first_hour_continuation"])
    r = out["results"]["first_hour_continuation"]
    assert r["tradable_at_cost"] is False


# -- statistics ------------------------------------------------------------

def test_t_stat_is_none_for_a_single_trade():
    assert intraday._stats([1.0])["t_stat"] is None


def test_stats_report_win_rate_and_profit_factor():
    s = intraday._stats([1.0, 1.0, -0.5])
    assert s["trades"] == 3
    assert s["win_rate_pct"] == pytest.approx(66.7, abs=0.1)
    assert s["profit_factor"] == pytest.approx(4.0, abs=0.01)


# -- verdict ---------------------------------------------------------------

def test_no_edge_is_the_default_reading():
    """The base rate for intraday studies is 'no edge', and the verdict has to
    be hard to pass or it is worthless."""
    noise = {}
    rng = np.random.default_rng(0)
    for d in range(1, 20):
        prices = 100 + np.cumsum(rng.normal(0, 0.05, 30))
        noise[f"N{d}"] = session(list(prices), day=f"2026-08-{d:02d}")
    frames = {"NOISE": pd.concat(noise.values())}
    out = intraday.evaluate(frames, cost_bps=10.0)
    assert out["verdict"]["edge_found"] is False
    assert "not justified" in out["verdict"]["message"]


def test_a_verdict_requires_statistical_significance_not_just_profit():
    """Two profitable trades are not an edge, however large."""
    results = {"x": {"net": {"trades": 2, "mean_pct": 5.0, "t_stat": 1.1},
                     "breakeven_cost_bps": 500.0}}
    assert intraday.verdict(results, 10.0)["edge_found"] is False


def test_a_clear_persistent_edge_is_reported_as_found():
    results = {"x": {"net": {"trades": 400, "mean_pct": 0.3, "t_stat": 4.2},
                     "breakeven_cost_bps": 40.0}}
    v = intraday.verdict(results, 10.0)
    assert v["edge_found"] is True
    # Even then it must not read as permission to trade.
    assert "NOT a reason to trade" in v["message"]


def test_evaluate_reports_the_cost_assumption_it_used():
    frames = {"X": trending_day("2026-08-17")}
    out = intraday.evaluate(frames, cost_bps=12.5)
    assert out["cost_bps"] == 12.5
    assert out["sessions"] == 1
