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

    # The clock is replaced BEFORE the pass and put back after it. It used to be
    # swapped inside the fetch and never restored, which measured the fake 400
    # against the real clock's reading from before the swap: the test passed
    # only while the suite reached it within its first 220 seconds, and left
    # every later test running on a counter instead of a clock.
    clock = iter(range(0, 1_000_000, 400))
    monkeypatch.setattr(intraday_runner.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(intraday_runner.bulk, "intraday_history_ibkr",
                        lambda symbols, **k: ({"T": multiday_bars()}, [], None))

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


# --- the book has to survive the pass ---------------------------------------
#
# Every test above hands `tick` a book of its own, which is the one path where
# nobody is expected to save. The live loop hands it nothing. For six weeks
# that path loaded the book, traded it, reported "8 opened" and dropped it: the
# dashboard said "No intraday book yet" at the end of a day of 78 passes.

BREAKOUT = [100.0, 100.3, 100.1, 100.5, 100.2, 100.4, 101.2]


def breakout_bars():
    index = pd.date_range("2026-08-21 09:30", periods=len(BREAKOUT), freq="5min")
    opens = [BREAKOUT[0]] + BREAKOUT[:-1]
    return pd.DataFrame({
        "Open": opens,
        "High": [max(o, c) + 0.1 for o, c in zip(opens, BREAKOUT)],
        "Low": [min(o, c) - 0.1 for o, c in zip(opens, BREAKOUT)],
        "Close": BREAKOUT, "Volume": [10_000] * len(BREAKOUT)}, index=index)


def live_pass(monkeypatch, path, moment):
    """One pass exactly as the scheduler makes it: no book handed in."""
    monkeypatch.setattr(intraday_watchlist, "load", lambda *a, **k: {"symbols": ["TEST"]})
    monkeypatch.setattr(intraday_runner.bulk, "intraday_history_ibkr",
                        lambda symbols, **k: ({"TEST": breakout_bars()}, [], None))
    return intraday_runner.tick(CONFIG, now=moment, book_path=path)


def test_a_live_pass_saves_the_book_it_traded(tmp_path, monkeypatch):
    path = str(tmp_path / "intraday.json")
    out = live_pass(monkeypatch, path, london(16, 0))
    assert len(out["opened"]) == 1

    saved = Book.load(path)
    assert saved.started
    assert [p["ticker"] for p in saved.positions] == ["TEST"]


def test_the_next_pass_manages_what_the_last_one_opened(tmp_path, monkeypatch):
    """The flat-by-close guarantee is only as good as the book's memory: a
    position the next pass cannot see is a position nobody closes."""
    path = str(tmp_path / "intraday.json")
    live_pass(monkeypatch, path, london(16, 0))
    out = live_pass(monkeypatch, path, london(20, 56))

    assert [c["reason"] for c in out["closed"]] == ["flat_by_close"]
    assert Book.load(path).positions == []


def test_intraday_trades_never_reach_the_daily_ledger(tmp_path, monkeypatch):
    """Two books, two records. The daily journal reads the daily ledger, so one
    intraday trade written there makes both journals report the sum."""
    from assistant.paper import closed_archive, intraday_book

    path = str(tmp_path / "intraday.json")
    live_pass(monkeypatch, path, london(16, 0))
    live_pass(monkeypatch, path, london(20, 56))

    assert closed_archive.load() == []
    own = closed_archive.load(intraday_book.ledger_for(path))
    assert [t["ticker"] for t in own] == ["TEST"]
    assert intraday_book.load_book(path).summary()["closed_trades"] == 1


def test_the_days_limits_count_trades_the_book_no_longer_holds_inline(tmp_path):
    """A save keeps a tail of 20 closed trades and moves the rest to the ledger.
    Counted from the tail, a limit of 40 trades a day could never be reached."""
    from assistant.paper import closed_archive, intraday_book

    path = str(tmp_path / "intraday.json")
    book = intraday_book.load_book(path)
    book.start(100_000.0, "USD", "2026-08-21")
    for i in range(30):
        book.closed.append({"ticker": f"T{i}", "direction": "long", "shares": 1,
                            "entry_date": "2026-08-21", "exit_date": "2026-08-21",
                            "entry_price": 10.0, "exit_price": 10.0 + i / 100,
                            "strategy": "opening_range_break", "pnl": 0.0})
    book.save(path)

    reloaded = intraday_book.load_book(path)
    assert len(reloaded.closed) == closed_archive.BOOK_TAIL
    assert len(intraday_book.closed_on(reloaded, "2026-08-21")) == 30


def test_a_pass_that_overran_its_bar_is_abandoned_not_traded(tmp_path, monkeypatch):
    """A pass that began before the laptop slept wakes holding a clock reading
    from hours ago. Trading on it opens positions after the bell."""
    from datetime import timedelta

    path = str(tmp_path / "intraday.json")
    began = london(16, 0)
    clock = iter([began, began + timedelta(hours=4)])
    monkeypatch.setattr(intraday_runner, "_now", lambda: next(clock))
    monkeypatch.setattr(intraday_runner.market_clock, "is_open", lambda *a, **k: True)
    monkeypatch.setattr(intraday_watchlist, "load", lambda *a, **k: {"symbols": ["TEST"]})
    monkeypatch.setattr(intraday_runner.bulk, "intraday_history_ibkr",
                        lambda symbols, **k: ({"TEST": breakout_bars()}, [], None))

    out = intraday_runner.tick(CONFIG, book_path=path)
    assert "Abandoned" in out["error"]
    assert not Book.load(path).started


def test_a_live_pass_gives_the_fetch_a_budget_inside_its_own_bar(tmp_path, monkeypatch):
    """Without one, a connection that has gone quiet costs a request timeout
    per symbol and a single pass holds the loop for an hour."""
    asked = {}
    monkeypatch.setattr(intraday_watchlist, "load", lambda *a, **k: {"symbols": ["T"]})

    def spy(symbols, **kwargs):
        asked.update(kwargs)
        return {}, list(symbols), None
    monkeypatch.setattr(intraday_runner.bulk, "intraday_history_ibkr", spy)

    intraday_runner.tick(CONFIG, now=london(16, 0), book_path=str(tmp_path / "b.json"))
    assert 0 < asked["max_seconds"] < 5 * 60


def test_the_fetch_stops_asking_once_its_budget_is_spent(monkeypatch):
    """What is left is returned as missing, so the caller can still act on what
    did price — and the held positions, which lead the list, are what priced."""
    from types import SimpleNamespace

    from assistant.providers import bulk, ibkr_provider

    asked = []
    ticks = iter(range(0, 10_000, 100))          # each look at the clock: +100s

    class Events:
        def __iadd__(self, _handler):
            return self

        def __isub__(self, _handler):
            return self

    class FakeIB:
        errorEvent = Events()

        def reqHistoricalData(self, contract, **kwargs):
            asked.append(contract.symbol)
            return [SimpleNamespace(date="2026-08-21 09:30:00", open=1.0, high=1.0,
                                    low=1.0, close=1.0, volume=1)]

        def disconnect(self):
            pass

    class FakeProvider:
        INTRADAY_MAX_DURATION = {"5 mins": "1 M"}

        def __init__(self, config=None):
            pass

        def is_available(self):
            return True

        def _connect(self):
            return FakeIB()

        def _contract_for(self, symbol):
            return SimpleNamespace(symbol=symbol)

        def _symbol_variants(self, symbol):
            return []

        @staticmethod
        def normalise_bars(symbol, frame):
            return frame

    monkeypatch.setattr(ibkr_provider, "IBKRDataProvider", FakeProvider)
    monkeypatch.setattr(bulk.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(bulk.time, "sleep", lambda _s: None)

    frames, missing, pacing = bulk.intraday_history_ibkr(
        ["HELD", "A", "B", "C"], duration="2 D", max_seconds=150)

    assert asked == ["HELD"]
    assert list(frames) == ["HELD"] and missing == ["A", "B", "C"]
    assert pacing["out_of_time"] is True
