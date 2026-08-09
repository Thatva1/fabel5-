"""Paper trader — book state, universe screening, and the session loop.

The negative assertions matter most here, and they are of a different kind to
the backtest's. A backtest that is wrong reports a bad number. A paper trader
that is wrong CORRUPTS STATE — it double-buys, loses a position, or resets the
balance — and every subsequent session inherits the damage. So the tests
concentrate on the things that must survive being run twice, interrupted, or
run on a day when nothing should happen.
"""
import json
import os

import pandas as pd
import pytest

from assistant.paper import screen, session
from assistant.paper.book import Book


@pytest.fixture
def book_path(tmp_path):
    return str(tmp_path / "book.json")


def _started(path, equity=100_000):
    book = Book.load(path)
    book.start(equity, "GBP", "2026-01-05")
    book.save(path)
    return book


# --- the book ----------------------------------------------------------------

def test_a_fresh_book_is_empty_rather_than_an_error():
    book = Book.load("/nonexistent/path/book.json")
    assert not book.started and book.positions == [] and book.equity() == 0.0


def test_a_book_survives_a_round_trip(book_path):
    book = _started(book_path)
    book.open_position(ticker="AAPL", direction="long", shares=10, price=100.0,
                       stop=90.0, target=130.0, strategy="xs_momentum",
                       regime="TRENDING_UP", date="2026-01-05")
    book.save(book_path)

    reloaded = Book.load(book_path)
    assert reloaded.started
    assert len(reloaded.positions) == 1
    assert reloaded.cash == pytest.approx(99_000.0)
    assert reloaded.equity() == pytest.approx(100_000.0)


def test_an_interrupted_save_does_not_destroy_the_previous_book(book_path, monkeypatch):
    """The book is the only record of what the trader has done. A crash
    mid-write must not leave a truncated file that reads as a fresh account
    with the starting balance restored — that silently erases the experiment."""
    book = _started(book_path)
    book.open_position(ticker="MSFT", direction="long", shares=5, price=200.0,
                       stop=180.0, target=260.0, strategy="ts_momentum",
                       regime="SIDEWAYS", date="2026-01-05")
    book.save(book_path)

    real_replace = os.replace

    def explode(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", explode)
    with pytest.raises(OSError):
        book.save(book_path)
    monkeypatch.setattr(os, "replace", real_replace)

    survivor = Book.load(book_path)
    assert len(survivor.positions) == 1
    assert survivor.cash == pytest.approx(99_000.0)
    with open(book_path) as handle:
        json.load(handle)          # still valid JSON, not a truncated stub


def test_closing_a_position_books_cash_and_an_r_multiple(book_path):
    book = _started(book_path)
    book.open_position(ticker="KO", direction="long", shares=10, price=100.0,
                       stop=90.0, target=130.0, strategy="low_beta",
                       regime="SIDEWAYS", date="2026-01-05")
    position = book.positions[0]
    book.close_position(position, 130.0, "2026-02-02", "target")

    assert book.positions == []
    assert book.cash == pytest.approx(100_300.0)
    assert book.closed[0]["pnl"] == pytest.approx(300.0)
    assert book.closed[0]["r_multiple"] == pytest.approx(3.0)   # risked 10/share


def test_the_curve_keeps_one_row_per_day(book_path):
    book = _started(book_path)
    book.mark("2026-01-06")
    book.mark("2026-01-06")
    book.mark("2026-01-07")
    assert [row["date"] for row in book.curve] == ["2026-01-05", "2026-01-06", "2026-01-07"]


def test_drawdown_is_measured_from_the_peak_not_the_start(book_path):
    book = _started(book_path)
    book.curve = [{"date": "d1", "equity": 100.0}, {"date": "d2", "equity": 200.0},
                  {"date": "d3", "equity": 150.0}]
    assert book.summary()["max_drawdown_pct"] == pytest.approx(25.0)


# --- the universe screen -----------------------------------------------------

def test_the_screen_rejects_what_cannot_honestly_be_traded(monkeypatch):
    """28,192 symbols is not a universe until the untradeable ones are gone. A
    momentum ranking over the raw list puts a sub-dollar shell that tripled on
    one news day at the top, every single month."""
    monkeypatch.setattr(screen.bulk, "liquidity_table", lambda symbols, **kw: {
        "GOOD":  {"price": 50.0, "dollar_volume": 90e6, "bars": 60},
        "CHEAP": {"price": 0.40, "dollar_volume": 90e6, "bars": 60},
        "THIN":  {"price": 50.0, "dollar_volume": 1_000.0, "bars": 60},
        "NEW":   {"price": 50.0, "dollar_volume": 90e6, "bars": 5},
    })
    kept, report = screen.apply(["GOOD", "CHEAP", "THIN", "NEW", "DEAD"], {})
    assert kept == ["GOOD"]
    assert report["rejected_price"] == 1
    assert report["rejected_volume"] == 1
    assert report["rejected_history"] == 1
    assert report["no_data"] == 1


def test_the_cap_keeps_the_most_liquid_names_not_the_alphabet(monkeypatch):
    monkeypatch.setattr(screen.bulk, "liquidity_table", lambda symbols, **kw: {
        "AAA": {"price": 50.0, "dollar_volume": 10e6, "bars": 60},
        "ZZZ": {"price": 50.0, "dollar_volume": 900e6, "bars": 60},
    })
    kept, report = screen.apply(["AAA", "ZZZ"], {"paper": {"screen": {"max_symbols": 1}}})
    assert kept == ["ZZZ"]
    assert report["capped"] == 1


def test_a_throttled_screen_refuses_to_cache_itself(monkeypatch, tmp_path):
    """The bug this pins, exactly as it happened: a full 28,192-symbol run
    returned 537 "tradable" names with NVDA, MSFT, SPY, QQQ, META and GOOGL all
    missing and 88% marked "no data". yfinance had rate-limited partway through
    and whole chunks came back empty. Nothing noticed, so the wreckage was
    cached and every later session would have traded an arbitrary slice of the
    market believing it was the market.
    """
    monkeypatch.setattr(screen, "CACHE_PATH", str(tmp_path / "u.json"))
    survivors = {f"JUNK{i}": {"price": 50.0, "dollar_volume": 90e6, "bars": 60}
                 for i in range(537)}
    monkeypatch.setattr(screen.bulk, "liquidity_table", lambda s, **kw: survivors)
    monkeypatch.setattr(screen, "candidate_symbols",
                        lambda config, router: [f"S{i}" for i in range(28_192)])

    with pytest.raises(screen.bulk.ThrottleSuspected) as caught:
        screen.tradable_universe({}, None, force_refresh=True)
    assert "rate-limited" in str(caught.value)
    assert not os.path.exists(screen.CACHE_PATH), "a bad universe must not be cached"


def test_a_healthy_wide_screen_is_accepted(monkeypatch, tmp_path):
    monkeypatch.setattr(screen, "CACHE_PATH", str(tmp_path / "u.json"))
    table = {name: {"price": 100.0, "dollar_volume": 900e6, "bars": 60}
             for name in screen.SANITY_CANARIES}
    table.update({f"OK{i}": {"price": 50.0, "dollar_volume": 20e6, "bars": 60}
                  for i in range(900)})
    monkeypatch.setattr(screen.bulk, "liquidity_table", lambda s, **kw: table)
    monkeypatch.setattr(screen, "candidate_symbols",
                        lambda config, router: [f"S{i}" for i in range(2_000)])

    symbols, report, cached = screen.tradable_universe({}, None, force_refresh=True)
    assert cached is False
    assert "NVDA" in symbols
    assert os.path.exists(screen.CACHE_PATH)


def test_the_canary_check_does_not_fire_on_a_deliberately_small_universe(monkeypatch):
    """A hand-picked 20-name universe has no reason to contain SPY. Failing it
    there would make the guard useless noise."""
    report = {"considered": 20, "no_data": 0, "kept": 20}
    assert screen.sanity_check(["AAPL", "KO"], report, {}) is None


def test_turning_the_screen_off_says_so_out_loud():
    kept, report = screen.apply(["ANYTHING"], {"paper": {"screen": {"enabled": False}}})
    assert kept == ["ANYTHING"]
    assert "could not have traded" in screen.describe(report)


# --- the session -------------------------------------------------------------

def test_the_session_hands_its_calendar_to_the_strategies():
    """Two clocks that disagree. A strategy decides it is a rebalance bar from
    its own price frame; the session decides from the book. Left alone the
    session declares a rebalance day and every strategy refuses, because today
    is the 9th and the last bar is a Friday mid-month — nothing ever trades.
    """
    from assistant.strategies import registry

    opened = session._hand_the_calendar_to_the_session({"strategies": {}})
    calendar_strategies = [s for s in registry.all_strategies()
                           if "rebalance" in s.defaults]
    assert calendar_strategies, "the fixture assumes at least one calendar strategy"
    for strategy in calendar_strategies:
        assert opened["strategies"][strategy.name]["rebalance"] == "any"


def test_handing_over_the_calendar_changes_nothing_else():
    config = {"strategies": {"xs_momentum": {"top_n": 5, "enabled": False}}}
    opened = session._hand_the_calendar_to_the_session(config)
    assert opened["strategies"]["xs_momentum"]["top_n"] == 5
    assert opened["strategies"]["xs_momentum"]["enabled"] is False
    assert config["strategies"]["xs_momentum"] == {"top_n": 5, "enabled": False}, \
        "the caller's config must not be mutated"


def test_a_rebalance_is_due_once_a_month_not_once_a_day(book_path):
    book = _started(book_path)
    assert session._rebalance_due(book, "2026-01-05") is True      # never run

    book.sessions.append({"date": "2026-01-05"})
    assert session._rebalance_due(book, "2026-01-06") is False
    assert session._rebalance_due(book, "2026-01-30") is False
    assert session._rebalance_due(book, "2026-02-02") is True


def test_a_trader_switched_off_for_months_rebalances_when_it_returns(book_path):
    """Keyed on the book, not the calendar. A trader that missed March must
    rebalance the day it comes back rather than hold a stale book until April."""
    book = _started(book_path)
    book.sessions.append({"date": "2026-01-05"})
    assert session._rebalance_due(book, "2026-06-17") is True


def test_slippage_always_hurts():
    assert session._slipped(100.0, "long", 50.0, opening=True) > 100.0
    assert session._slipped(100.0, "long", 50.0, opening=False) < 100.0
    assert session._slipped(100.0, "short", 50.0, opening=True) < 100.0
    assert session._slipped(100.0, "short", 50.0, opening=False) > 100.0


def test_a_bar_covering_both_levels_is_scored_as_the_stop():
    """No intrabar data means the order is unknowable. Assuming the target
    converts every ambiguous day into a winner."""
    position = {"direction": "long", "stop": 90.0, "target": 110.0}
    bar = pd.Series({"High": 115.0, "Low": 85.0, "Close": 100.0})
    assert session._exit_reason(position, bar)[0] == "stop"

    short = {"direction": "short", "stop": 110.0, "target": 90.0}
    assert session._exit_reason(short, bar)[0] == "stop"


def test_a_position_that_touched_nothing_stays_open():
    position = {"direction": "long", "stop": 90.0, "target": 110.0}
    bar = pd.Series({"High": 105.0, "Low": 95.0, "Close": 100.0})
    assert session._exit_reason(position, bar) == (None, None)


def test_the_paper_package_cannot_reach_a_broker():
    """The strongest guarantee available is not a flag that is switched off, it
    is the absence of the code that would place an order.

    Checked on the parsed module rather than by searching the text, so the
    prose explaining that there is no broker does not itself trip the test.
    """
    import ast
    import pathlib

    package = pathlib.Path(session.__file__).parent
    for path in sorted(package.glob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "broker" not in alias.name, f"{path.name} imports {alias.name}"
            elif isinstance(node, ast.ImportFrom):
                assert "broker" not in (node.module or ""), \
                    f"{path.name} imports from {node.module}"
                for alias in node.names:
                    assert "roker" not in alias.name, f"{path.name} imports {alias.name}"
            elif isinstance(node, ast.Attribute):
                assert node.attr != "place_order", f"{path.name} calls place_order"
            elif isinstance(node, ast.Name):
                assert node.id != "place_order", f"{path.name} calls place_order"
