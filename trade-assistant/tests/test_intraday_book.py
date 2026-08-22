"""The intraday book: does it actually behave like a trading book?

One claim is being tested above all others. A trading firm's book ends every
day flat and therefore produces a realised number every day; a holding
company's book ends the day holding things and reports a change in marks. Every
test here is about which of those this is.
"""
from datetime import datetime, timezone

import pandas as pd
import pytest

from assistant.core import market_clock
from assistant.paper import intraday_book
from assistant.paper.book import Book

CONFIG = {
    "account": {"portfolio_value": 100_000},
    "intraday": {
        "bar_size": "5 mins",
        "risk_per_trade_pct": 0.25,
        "max_position_pct": 5.0,
        "max_gross_exposure_pct": 60.0,
        "max_open_positions": 8,
        "max_daily_loss_pct": 2.0,
        "max_trades_per_day": 40,
        "no_new_entries_minutes_before_close": 30,
        "flat_by_minutes_before_close": 5,
        "estimate_spreads": False,
        "costs": {"spread_bps": 4.0, "slippage_bps": 2.0, "commission_per_trade": 1.0},
        "strategies": {"opening_range_break": {"enabled": True},
                       "vwap_reversion": {"enabled": False},
                       "intraday_momentum": {"enabled": False},
                       "gap_fade": {"enabled": False}},
    },
}


def london(hour, minute, *, day=21, month=8):
    """A London wall-clock moment in BST, expressed in UTC."""
    return datetime(2026, month, day, hour - 1, minute, tzinfo=timezone.utc)


def bars(closes, *, start="2026-08-21 09:30", spread=0.1):
    index = pd.date_range(start, periods=len(closes), freq="5min")
    opens = [closes[0]] + list(closes[:-1])
    return pd.DataFrame({
        "Open": opens,
        "High": [max(o, c) + spread for o, c in zip(opens, closes)],
        "Low": [min(o, c) - spread for o, c in zip(opens, closes)],
        "Close": closes, "Volume": [10_000] * len(closes)}, index=index)


BREAKOUT = [100.0, 100.3, 100.1, 100.5, 100.2, 100.4, 101.2]


@pytest.fixture
def book(tmp_path):
    return Book.load(str(tmp_path / "intraday.json"))


def test_it_opens_a_position_while_the_session_is_open(book):
    out = intraday_book.run(CONFIG, {"TEST": bars(BREAKOUT)},
                            now=london(16, 0), book=book)
    assert out["phase"]["phase"] == market_clock.PHASE_OPEN
    assert len(out["opened"]) == 1
    assert out["opened"][0]["direction"] == "long"
    assert book.positions[0]["meta"]["max_hold_minutes"] > 0


def test_nothing_opens_inside_the_last_half_hour(book):
    out = intraday_book.run(CONFIG, {"TEST": bars(BREAKOUT)},
                            now=london(20, 40), book=book)
    assert out["phase"]["phase"] == market_clock.PHASE_NO_NEW_ENTRIES
    assert out["opened"] == []
    assert book.positions == []


def test_everything_is_closed_in_the_liquidation_window(book):
    intraday_book.run(CONFIG, {"TEST": bars(BREAKOUT)},
                      now=london(16, 0), book=book)
    assert len(book.positions) == 1

    out = intraday_book.run(CONFIG, {"TEST": bars(BREAKOUT)},
                            now=london(20, 56), book=book)
    assert book.positions == []
    assert [c["reason"] for c in out["closed"]] == ["flat_by_close"]


def test_the_day_ends_with_a_realised_number_not_a_holding(book):
    """The whole difference between a trading book and a holding one."""
    intraday_book.run(CONFIG, {"TEST": bars(BREAKOUT)},
                      now=london(16, 0), book=book)
    out = intraday_book.run(CONFIG, {"TEST": bars(BREAKOUT)},
                            now=london(20, 56), book=book)
    day = book.daily[-1]
    assert book.positions == []
    assert day["closed"] == 1
    assert day["realised"] == pytest.approx(out["realised"], abs=0.01)


def test_a_position_is_closed_when_its_own_horizon_runs_out(book):
    intraday_book.run(CONFIG, {"TEST": bars(BREAKOUT)},
                      now=london(15, 0), book=book)
    assert len(book.positions) == 1
    horizon = book.positions[0]["meta"]["max_hold_minutes"]

    later = london(15, 0) + pd.Timedelta(minutes=horizon + 5).to_pytimedelta()
    out = intraday_book.run(CONFIG, {"TEST": bars(BREAKOUT)}, now=later, book=book)
    assert [c["reason"] for c in out["closed"]] == ["time"]
    assert out["closed"][0]["held_minutes"] >= horizon


def test_a_stop_closes_the_position(book):
    intraday_book.run(CONFIG, {"TEST": bars(BREAKOUT)},
                      now=london(16, 0), book=book)
    stop = book.positions[0]["stop"]

    collapsed = bars(BREAKOUT + [stop - 1.0])
    out = intraday_book.run(CONFIG, {"TEST": collapsed}, now=london(16, 30), book=book)
    assert [c["reason"] for c in out["closed"]] == ["stop"]
    assert out["closed"][0]["pnl"] < 0


def test_the_bell_beats_the_target(book):
    """A position at its target inside the liquidation window is recorded as
    closed by the CLOCK, not by the rule. Crediting the strategy with an exit
    the calendar made corrupts the by-exit breakdown the rules are judged on."""
    intraday_book.run(CONFIG, {"TEST": bars(BREAKOUT)},
                      now=london(16, 0), book=book)
    target = book.positions[0]["target"]

    won = bars(BREAKOUT + [target + 1.0])
    out = intraday_book.run(CONFIG, {"TEST": won}, now=london(20, 57), book=book)
    assert [c["reason"] for c in out["closed"]] == ["flat_by_close"]


def test_it_does_nothing_at_all_when_no_market_is_open(book):
    out = intraday_book.run(CONFIG, {"TEST": bars(BREAKOUT)},
                            now=london(3, 0), book=book)
    assert out["phase"]["phase"] == market_clock.PHASE_CLOSED
    assert out["opened"] == [] and book.positions == []


def test_london_and_new_york_are_judged_on_their_own_clocks(book):
    """At 16:10 London the LSE is inside its own last half hour while New York
    is mid-session. One calendar for both is how a UK position gets held four
    and a half hours past anything tradable."""
    frames = {"TEST": bars(BREAKOUT), "TSCO.L": bars(BREAKOUT, start="2026-08-21 08:00")}
    out = intraday_book.run(CONFIG, frames, now=london(16, 10), book=book)
    assert out["phase"]["by_venue"]["US"] == market_clock.PHASE_OPEN
    assert out["phase"]["by_venue"]["LSE"] == market_clock.PHASE_NO_NEW_ENTRIES
    # The permissive state governs OPENING, and London is not among the opened.
    assert all(row["ticker"] != "TSCO.L" for row in out["opened"])


def test_the_daily_loss_limit_stops_the_book(book):
    book.start(100_000.0, "USD", "2026-08-21")
    book.closed.append({"ticker": "OLD", "exit_date": "2026-08-21",
                        "pnl": -2_500.0, "exit_reason": "stop"})
    out = intraday_book.run(CONFIG, {"TEST": bars(BREAKOUT)},
                            now=london(16, 0), book=book)
    assert out["opened"] == []
    assert any("Daily loss limit" in note for note in out["notes"])


def test_the_open_position_cap_is_respected(book):
    tight = {**CONFIG, "intraday": {**CONFIG["intraday"], "max_open_positions": 2}}
    frames = {f"T{i}": bars(BREAKOUT) for i in range(6)}
    out = intraday_book.run(tight, frames, now=london(16, 0), book=book)
    assert len(out["opened"]) == 2
    assert len(book.positions) == 2


def test_a_position_is_never_opened_twice_in_the_same_name(book):
    intraday_book.run(CONFIG, {"TEST": bars(BREAKOUT)},
                      now=london(16, 0), book=book)
    intraday_book.run(CONFIG, {"TEST": bars(BREAKOUT)},
                      now=london(16, 5), book=book)
    assert len(book.positions) == 1


def test_an_instrument_too_wide_to_trade_is_never_opened(book):
    """With spread estimation on, a name whose spread exceeds the cap is not in
    the tradable set at all."""
    wide = {**CONFIG, "intraday": {**CONFIG["intraday"], "estimate_spreads": True,
                                   "max_spread_bps": 0.5}}
    out = intraday_book.run(wide, {"TEST": bars(BREAKOUT, spread=2.0)},
                            now=london(16, 0), book=book)
    assert out["opened"] == []


def test_a_position_that_cannot_be_priced_is_reported_not_ignored(book):
    intraday_book.run(CONFIG, {"TEST": bars(BREAKOUT)},
                      now=london(16, 0), book=book)
    out = intraday_book.run(CONFIG, {}, now=london(16, 30), book=book)
    assert any("cannot be priced" in note for note in out["notes"])
    assert book.positions[0]["mark_failed"] is True


def test_costs_are_charged_on_entry_and_on_exit(book):
    opened = intraday_book.run(CONFIG, {"TEST": bars(BREAKOUT)},
                               now=london(16, 0), book=book)
    entry = book.positions[0]["entry_price"]
    # The signal was 101.2; the fill is worse by half the spread plus slippage.
    assert entry > 101.2
    assert opened["costs"] == pytest.approx(1.0)   # commission on the way in

    closed = intraday_book.run(CONFIG, {"TEST": bars(BREAKOUT)},
                               now=london(20, 56), book=book)
    assert closed["costs"] == pytest.approx(1.0)   # and again on the way out
    # `costs` is per PASS, so the day's record is what accumulates them.
    assert book.daily[-1]["costs"] == pytest.approx(2.0)


def test_the_exit_fill_is_worse_than_the_price_that_triggered_it(book):
    intraday_book.run(CONFIG, {"TEST": bars(BREAKOUT)},
                      now=london(16, 0), book=book)
    target = book.positions[0]["target"]
    won = bars(BREAKOUT + [target + 1.0])
    intraday_book.run(CONFIG, {"TEST": won}, now=london(16, 30), book=book)
    trade = book.closed[-1]
    assert trade["exit_reason"] == "target"
    # A long sells lower than its target, never at it.
    assert trade["exit_price"] < target
