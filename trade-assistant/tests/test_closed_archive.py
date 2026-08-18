"""Closed trades live outside the book, without losing any of them."""
from assistant.paper import closed_archive, tradelog
from assistant.paper.book import Book


def trade(ticker="NVDA", pnl=100.0, exit_date="2026-08-17", **extra):
    return {"ticker": ticker, "direction": "long", "shares": 10,
            "entry_price": 100.0, "exit_price": 110.0, "entry_date": "2026-08-10",
            "exit_date": exit_date, "strategy": "ts_momentum", "pnl": pnl,
            "r_multiple": 1.0, "exit_reason": "target", **extra}


def book_with(closed):
    book = Book()
    book.start(1_000_000, "USD", "2026-08-10")
    book.closed = list(closed)
    return book


# -- identity --------------------------------------------------------------

def test_the_same_trade_gets_the_same_id_every_time():
    """Derived rather than assigned, so a trade archived before ids existed
    gets the id it would get today."""
    assert closed_archive.trade_id(trade()) == closed_archive.trade_id(trade())


def test_different_trades_get_different_ids():
    assert closed_archive.trade_id(trade("NVDA")) != closed_archive.trade_id(trade("AAPL"))


# -- append and load -------------------------------------------------------

def test_trades_round_trip_through_the_archive(tmp_path):
    path = str(tmp_path / "closed.jsonl")
    assert closed_archive.append([trade("NVDA"), trade("AAPL")], path=path) == 2
    assert {t["ticker"] for t in closed_archive.load(path)} == {"NVDA", "AAPL"}


def test_appending_the_same_trade_twice_records_it_once(tmp_path):
    path = str(tmp_path / "closed.jsonl")
    closed_archive.append([trade("NVDA")], path=path)
    assert closed_archive.append([trade("NVDA")], path=path) == 0
    assert len(closed_archive.load(path)) == 1


def test_a_truncated_line_costs_one_trade_not_the_file(tmp_path):
    """Why JSON Lines rather than one array: a half-written array will not
    parse at all."""
    path = tmp_path / "closed.jsonl"
    closed_archive.append([trade("NVDA"), trade("AAPL")], path=str(path))
    with open(path, "a") as handle:
        handle.write('{"ticker": "BROK')          # crash mid-write
    assert len(closed_archive.load(str(path))) == 2


def test_a_missing_archive_is_empty_not_an_error(tmp_path):
    assert closed_archive.load(str(tmp_path / "none.jsonl")) == []


# -- flushing --------------------------------------------------------------

def test_flush_moves_trades_out_and_keeps_a_short_tail(tmp_path):
    path = str(tmp_path / "closed.jsonl")
    book = book_with([trade(f"T{i}", exit_date=f"2026-08-{i:02d}") for i in range(1, 26)])
    closed_archive.flush(book, path=path, keep=5)
    assert len(book.closed) == 5              # book stays short
    assert len(closed_archive.load(path)) == 25   # nothing lost


def test_the_tail_is_a_copy_so_trimming_cannot_lose_a_trade(tmp_path):
    path = str(tmp_path / "closed.jsonl")
    book = book_with([trade(f"T{i}", exit_date=f"2026-08-{i:02d}") for i in range(1, 11)])
    closed_archive.flush(book, path=path, keep=3)
    archived = {t["ticker"] for t in closed_archive.load(path)}
    assert {t["ticker"] for t in book.closed} <= archived


def test_flushing_twice_does_not_duplicate(tmp_path):
    path = str(tmp_path / "closed.jsonl")
    book = book_with([trade("NVDA")])
    closed_archive.flush(book, path=path, keep=20)
    closed_archive.flush(book, path=path, keep=20)
    assert len(closed_archive.load(path)) == 1


# -- merged history --------------------------------------------------------

def test_merged_deduplicates_the_overlap(tmp_path):
    """A trade sits in the archive AND the book's tail at once; counting it
    twice would build a win rate out of duplicates."""
    path = str(tmp_path / "closed.jsonl")
    closed_archive.append([trade("NVDA")], path=path)
    book = book_with([trade("NVDA"), trade("AAPL")])
    merged = closed_archive.merged(book, path=path)
    assert len(merged) == 2


def test_merged_includes_trades_that_scrolled_out_of_the_book(tmp_path):
    path = str(tmp_path / "closed.jsonl")
    closed_archive.append([trade("OLD", pnl=-500.0, exit_date="2026-07-01")], path=path)
    book = book_with([trade("NEW", pnl=100.0)])
    tickers = {t["ticker"] for t in closed_archive.merged(book, path=path)}
    assert tickers == {"OLD", "NEW"}


# -- the figures that must not drift --------------------------------------

def test_a_win_rate_cannot_improve_by_old_losses_scrolling_off(tmp_path, monkeypatch):
    """The failure this whole module has to avoid."""
    path = str(tmp_path / "closed.jsonl")
    monkeypatch.setattr(closed_archive, "ARCHIVE_PATH", path)

    losses = [trade(f"L{i}", pnl=-100.0, exit_date=f"2026-07-{i:02d}") for i in range(1, 11)]
    wins = [trade(f"W{i}", pnl=100.0, exit_date=f"2026-08-{i:02d}") for i in range(1, 3)]
    book = book_with(losses + wins)

    closed_archive.flush(book, path=path, keep=2)      # only the 2 wins remain inline
    assert len(book.closed) == 2
    # Read off the tail alone this would be 100%. The truth is 2 of 12.
    assert book.summary()["win_rate_pct"] == round(2 / 12 * 100, 1)
    assert book.summary()["closed_trades"] == 12


def test_the_journal_reports_the_full_history(tmp_path, monkeypatch):
    path = str(tmp_path / "closed.jsonl")
    monkeypatch.setattr(closed_archive, "ARCHIVE_PATH", path)
    book = book_with([trade(f"T{i}", exit_date=f"2026-08-{i:02d}") for i in range(1, 11)])
    closed_archive.flush(book, path=path, keep=2)
    assert tradelog.report(book)["totals"]["closed"] == 10


# -- stats -----------------------------------------------------------------

def test_archive_stats_summarise_the_whole_record(tmp_path):
    path = str(tmp_path / "closed.jsonl")
    closed_archive.append(
        [trade("A", pnl=100.0, exit_date="2026-08-01"),
         trade("B", pnl=-40.0, exit_date="2026-08-05")], path=path)
    s = closed_archive.stats(path=path)
    assert s["trades"] == 2 and s["wins"] == 1 and s["losses"] == 1
    assert s["realised_pnl"] == 60.0
    assert s["first_exit"] == "2026-08-01" and s["last_exit"] == "2026-08-05"
