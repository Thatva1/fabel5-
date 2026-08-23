"""The live loop: what it fetches, when it refuses to, and what it reports.

The failures worth testing here are the quiet ones. A loop that takes longer
than its own bar still produces trades that look like trades. A previous close
read from the wrong end of the frame still produces gaps that look like gaps.
Neither announces itself.
"""
from datetime import datetime, timezone

import pandas as pd
import pytest

from assistant.core import market_clock
from assistant.paper import intraday_runner, intraday_watchlist
from assistant.paper.book import Book

CONFIG = {"account": {"portfolio_value": 100_000},
          "intraday": {"bar_size": "5 mins", "estimate_spreads": False,
                       "strategies": {"opening_range_break": {"enabled": True},
                                      "vwap_reversion": {"enabled": False},
                                      "intraday_momentum": {"enabled": False},
                                      "gap_fade": {"enabled": False}}}}


def london(hour, minute, day=21):
    """A London wall-clock moment in BST, as UTC."""
    return datetime(2026, 8, day, hour - 1, minute, tzinfo=timezone.utc)


def multiday_bars():
    """Two sessions of five-minute bars, so a PREVIOUS close exists."""
    rows = []
    for day, level in (("2026-08-20", 100.0), ("2026-08-21", 110.0)):
        idx = pd.date_range(f"{day} 09:30", periods=20, freq="5min")
        closes = [level + i * 0.01 for i in range(20)]
        rows.append(pd.DataFrame({
            "Open": closes, "High": [c + 0.05 for c in closes],
            "Low": [c - 0.05 for c in closes], "Close": closes,
            "Volume": [10_000] * 20}, index=idx))
    return pd.concat(rows)


def test_the_previous_close_is_yesterdays_close_not_the_frames_first_bar():
    """At a month of five-minute bars the frame's first close is a price from
    four weeks ago. Two of the four rules are defined against the previous
    close, so this does not weaken them — it makes them measure something else,
    and report an enormous gap every single morning."""
    frames = {"TEST": multiday_bars()}
    out = intraday_runner._previous_closes(frames, london(16, 0))
    # Yesterday's LAST bar, not today's and not the first one in the frame.
    assert out["TEST"] == pytest.approx(100.19)


def test_an_instrument_with_only_todays_bars_has_no_previous_close():
    """Better absent than wrong: a rule handed a made-up previous close still
    fires, and the rules that need it decline when it is missing."""
    only_today = multiday_bars()
    only_today = only_today[only_today.index.date == pd.Timestamp("2026-08-21").date()]
    assert intraday_runner._previous_closes({"TEST": only_today}, london(16, 0)) == {}


def test_nothing_is_fetched_when_no_market_is_open(tmp_path, monkeypatch):
    """A loop left running overnight must cost nothing. Walking the broker
    every five minutes until morning spends a rate limit on bars that cannot be
    traded."""
    called = []
    monkeypatch.setattr(intraday_runner.bulk, "intraday_history_ibkr",
                        lambda *a, **k: called.append(1) or ({}, [], None))
    book = Book.load(str(tmp_path / "b.json"))
    out = intraday_runner.tick(CONFIG, now=london(3, 0), book=book)
    assert out["skipped"] is True
    assert out["phase"]["phase"] == market_clock.PHASE_CLOSED
    assert called == []


def test_it_refuses_to_run_without_a_working_set(tmp_path, monkeypatch):
    """Choosing instruments from a stale liquidity profile is the exact failure
    the two-stage selection exists to prevent, so a missing set is an error
    rather than a fallback."""
    monkeypatch.setattr(intraday_watchlist, "load", lambda *a, **k: None)
    book = Book.load(str(tmp_path / "b.json"))
    out = intraday_runner.tick(CONFIG, now=london(16, 0), book=book)
    assert "working set" in out["error"]


def test_held_positions_are_always_fetched_even_if_dropped_from_the_set(
        tmp_path, monkeypatch):
    """A position that cannot be priced cannot be managed, and the flat-by-close
    guarantee depends on being able to price everything that is open."""
    asked = {}
    monkeypatch.setattr(intraday_watchlist, "load",
                        lambda *a, **k: {"symbols": ["NEW"]})

    def fake_fetch(symbols, **kwargs):
        asked["symbols"] = list(symbols)
        return {}, list(symbols), None
    monkeypatch.setattr(intraday_runner.bulk, "intraday_history_ibkr", fake_fetch)

    book = Book.load(str(tmp_path / "b.json"))
    book.start(100_000.0, "USD", "2026-08-21")
    book.open_position(ticker="OLD", direction="long", shares=1, price=10.0,
                       stop=9.0, target=12.0, strategy="opening_range_break",
                       regime="INTRADAY", date="2026-08-21")

    intraday_runner.tick(CONFIG, now=london(16, 0), book=book)
    assert "OLD" in asked["symbols"] and "NEW" in asked["symbols"]


def test_a_held_position_that_did_not_price_is_named(tmp_path, monkeypatch):
    monkeypatch.setattr(intraday_watchlist, "load", lambda *a, **k: {"symbols": []})
    monkeypatch.setattr(intraday_runner.bulk, "intraday_history_ibkr",
                        lambda symbols, **k: ({}, list(symbols), None))
    book = Book.load(str(tmp_path / "b.json"))
    book.start(100_000.0, "USD", "2026-08-21")
    book.open_position(ticker="OLD", direction="long", shares=1, price=10.0,
                       stop=9.0, target=12.0, strategy="opening_range_break",
                       regime="INTRADAY", date="2026-08-21")
    out = intraday_runner.tick(CONFIG, now=london(16, 0), book=book)
    assert any("HELD BUT UNPRICED" in note and "OLD" in note for note in out["notes"])


def test_a_pass_slower_than_its_own_bar_says_so(tmp_path, monkeypatch):
    """The quiet failure: the loop overruns, every signal arrives a bar late,
    and the trades still look like trades."""
    monkeypatch.setattr(intraday_watchlist, "load", lambda *a, **k: {"symbols": ["T"]})

    def slow_fetch(symbols, **kwargs):
        import time as _t
        intraday_runner.time.monotonic = lambda _c=[0]: _c.append(_c[-1] + 400) or _c[-1]
        return {"T": multiday_bars()}, [], None
    monkeypatch.setattr(intraday_runner.bulk, "intraday_history_ibkr", slow_fetch)

    book = Book.load(str(tmp_path / "b.json"))
    out = intraday_runner.tick(CONFIG, now=london(16, 0), book=book)
    assert any("arriving late" in note for note in out["notes"])


def test_stale_bars_are_counted_and_reported(tmp_path, monkeypatch):
    """The one thing worse than a 15-minute delay is a delay nobody counts."""
    monkeypatch.setattr(intraday_watchlist, "load", lambda *a, **k: {"symbols": ["T"]})
    monkeypatch.setattr(intraday_runner.bulk, "intraday_history_ibkr",
                        lambda symbols, **k: ({"T": multiday_bars()}, [], None))
    book = Book.load(str(tmp_path / "b.json"))
    # Last bar is 2026-08-21 11:05 New York; asking at 16:00 London = 11:00 NY
    # would be negative, so ask an hour later.
    out = intraday_runner.tick(CONFIG, now=london(17, 30), book=book)
    assert out["staleness_minutes"] is not None
    assert out["staleness_minutes"] > 25
    assert any("minutes old" in note for note in out["notes"])


def test_prepare_says_so_when_there_is_no_daily_history(monkeypatch):
    monkeypatch.setattr(intraday_runner, "_cached_daily_history", lambda: {})
    out = intraday_runner.prepare(CONFIG, now=london(13, 0))
    assert "No daily universe history" in out["error"]


# --- how much history a pass asks for ---------------------------------------
#
# Measured on this account at 5-minute bars: 2 days costs 0.61s an instrument,
# 5 days 0.68s, a month 15.33s. Not pacing — zero violations across the sample.
# The loop defaulted to the maximum, which made a 60-name pass take FIFTEEN
# MINUTES against a five-minute bar: every signal three bars stale, and the
# trades still looking like trades.

def test_a_live_pass_asks_for_days_not_the_maximum(monkeypatch):
    asked = {}
    monkeypatch.setattr(intraday_watchlist, "load", lambda *a, **k: {"symbols": ["T"]})

    def spy(symbols, **kwargs):
        asked.update(kwargs)
        return {}, list(symbols), None
    monkeypatch.setattr(intraday_runner.bulk, "intraday_history_ibkr", spy)

    book = Book.load("/nonexistent/book.json")
    intraday_runner.tick(CONFIG, now=london(16, 0), book=book)
    assert asked["duration"] == "2 D"


def test_the_live_duration_still_spans_a_previous_session():
    """One day contains no previous close, and two of the four rules are
    defined against it — on a Monday, "1 D" holds no Friday at all."""
    for bar in ("1 min", "5 mins", "15 mins"):
        duration = intraday_runner._live_duration({"intraday": {"bar_size": bar}})
        assert duration.split()[0] != "1" or duration.endswith("M")


def test_the_measurement_pass_asks_for_more_than_a_live_one_and_less_than_a_month():
    """Corwin-Schultz needs a few hundred bars to average over. Five sessions
    supplies 390 at a twentieth of the cost of the month that supplies 1,716."""
    config = {"intraday": {"bar_size": "5 mins"}}
    assert intraday_runner._live_duration(config) == "2 D"
    assert intraday_runner._measurement_duration(config) == "5 D"
