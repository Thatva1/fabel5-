"""Closing a position by hand.

Until this existed, a position could only leave the book by crossing its stop,
crossing its target, or running out the holding clock. None of those can know
that you have changed your mind, so a book you could not get out of was the
default state.

The tests here concentrate on the ways a manual exit could quietly corrupt the
record rather than merely fail: filling at a price it cannot justify, skipping
the costs the automatic exits pay, erasing the day's realised P&L, or hiding
itself in the journal as though a rule had made the decision.
"""
import pytest

from assistant.paper import live, manual
from assistant.paper.book import Book


CONFIG = {"paper": {"slippage_bps": 5.0},
          "backtest": {"costs": {"commission_per_trade": 1.0, "slippage_bps": 5.0,
                                 "stamp_duty_pct": 0.5, "stamp_duty_suffixes": [".L"],
                                 "per_market": {"": {"commission_per_trade": 1.0},
                                                ".L": {"commission_per_trade": 6.0}}}}}


@pytest.fixture(autouse=True)
def no_broker(monkeypatch):
    """No network in the default case: the feed simply does not answer."""
    monkeypatch.setattr(live, "fetch_marks",
                        lambda tickers, config, force=False: ({}, "feed unavailable"))


def _book(tmp_path, tickers=("AAPL", "MSFT")):
    book = Book.load(str(tmp_path / "book.json"))
    book.start(100_000.0, "USD", "2026-08-18")
    for ticker in tickers:
        book.open_position(ticker=ticker, direction="long", shares=10, price=100.0,
                           stop=90.0, target=130.0, strategy="ts_momentum",
                           regime="TRENDING_UP", date="2026-08-18",
                           bar_date="2026-08-18")
        book.positions[-1]["last_price"] = 110.0
    return book


def test_closing_one_position_leaves_the_rest_alone(tmp_path):
    book = _book(tmp_path)
    out = manual.close_positions(book, ["AAPL"], CONFIG)

    assert [c["ticker"] for c in out["closed"]] == ["AAPL"]
    assert [p["ticker"] for p in book.positions] == ["MSFT"]
    assert len(book.closed) == 1


def test_a_manual_exit_is_filed_as_manual(tmp_path):
    """The journal's by-exit breakdown is how the rules are judged. A
    discretionary exit filed as a target credits the strategy with a decision
    it did not make."""
    book = _book(tmp_path)
    manual.close_positions(book, ["AAPL"], CONFIG)
    assert book.closed[0]["exit_reason"] == "manual"


def test_the_fill_is_slipped_against_you(tmp_path):
    """A long sells lower than the mark. An exit that fills AT the mark makes
    closing by hand look free, which it is not."""
    book = _book(tmp_path)
    manual.close_positions(book, ["AAPL"], CONFIG)
    # 110.00 marked, 5bp against = 110 - 0.055
    assert book.closed[0]["exit_price"] == pytest.approx(109.945)


def test_a_short_is_slipped_the_other_way(tmp_path):
    book = Book.load(str(tmp_path / "book.json"))
    book.start(100_000.0, "USD", "2026-08-18")
    book.open_position(ticker="AAPL", direction="short", shares=10, price=100.0,
                       stop=110.0, target=80.0, strategy="ts_momentum",
                       regime="TRENDING_DOWN", date="2026-08-18")
    book.positions[-1]["last_price"] = 90.0
    manual.close_positions(book, ["AAPL"], CONFIG)
    # Covering costs you MORE, not less.
    assert book.closed[0]["exit_price"] == pytest.approx(90.045)


def test_commission_is_charged(tmp_path):
    book = _book(tmp_path)
    before = book.cash
    out = manual.close_positions(book, ["AAPL"], CONFIG)
    proceeds = float(book.closed[0]["committed"]) + book.closed[0]["pnl"]
    assert book.cash == pytest.approx(before + proceeds - 1.0)
    assert out["costs"] == pytest.approx(1.0)


def test_london_pays_londons_commission(tmp_path):
    """Costs are per venue. A flat US fee on a London exit understates it
    sixfold, and the same table the session reads has to answer here."""
    book = _book(tmp_path, tickers=("TSCO.L",))
    out = manual.close_positions(book, ["TSCO.L"], CONFIG)
    assert out["costs"] == pytest.approx(6.0)


def test_the_session_mark_is_labelled_as_a_fallback(tmp_path):
    """With no live price the exit still has to fill at something. The session
    close is a real traded price — just an old one — and saying which was used
    is the difference between a fallback and a fabrication."""
    book = _book(tmp_path)
    out = manual.close_positions(book, ["AAPL"], CONFIG)
    assert out["closed"][0]["price_source"] == "session mark"
    assert book.closed[0]["exit_price_source"] == "session mark"


def test_a_live_mark_is_preferred_and_labelled(tmp_path, monkeypatch):
    monkeypatch.setattr(live, "fetch_marks", lambda tickers, config, force=False: (
        {"AAPL": {"price": 120.0, "bar_time": "2026-08-21 15:45:00"}}, None))
    book = _book(tmp_path)
    out = manual.close_positions(book, ["AAPL"], CONFIG)
    assert out["closed"][0]["price_source"] == "live"
    assert out["closed"][0]["exit_price"] == pytest.approx(119.94)
    assert book.closed[0]["exit_price_at"] == "2026-08-21 15:45:00"


def test_close_all_goes_flat(tmp_path):
    book = _book(tmp_path, tickers=("AAPL", "MSFT", "NVDA"))
    out = manual.close_all(book, CONFIG)
    assert book.positions == []
    assert len(out["closed"]) == 3
    assert out["open_positions"] == 0


def test_an_unknown_ticker_is_reported_not_swallowed(tmp_path):
    """A 'close everything' that silently missed a holding is the worst
    possible outcome of a button labelled that way."""
    book = _book(tmp_path)
    out = manual.close_positions(book, ["AAPL", "TSLA"], CONFIG)
    assert out["missing"] == ["TSLA"]
    assert [c["ticker"] for c in out["closed"]] == ["AAPL"]


def test_naming_nothing_closes_nothing(tmp_path):
    book = _book(tmp_path)
    out = manual.close_positions(book, [], CONFIG)
    assert out["closed"] == [] and len(book.positions) == 2


def test_the_days_realised_pnl_accumulates_rather_than_being_overwritten(tmp_path):
    """`record_day` keeps one row per date and the last write wins. Writing
    only this exit's P&L would erase whatever the morning's session banked."""
    book = _book(tmp_path, tickers=("AAPL", "MSFT"))
    today = manual._today()
    book.record_day(today, realised=-500.0, costs=2.0, opened=3, closed=2,
                    price_source="ibkr", note="session")

    manual.close_positions(book, ["AAPL"], CONFIG)

    row = book.daily[-1]
    assert row["date"] == today
    assert row["realised"] == pytest.approx(-500.0 + book.closed[0]["pnl"])
    assert row["costs"] == pytest.approx(3.0)
    assert row["closed"] == 3          # the session's two, plus this one
    assert row["opened"] == 3          # untouched


def test_the_session_log_says_it_was_done_by_hand(tmp_path):
    book = _book(tmp_path)
    manual.close_positions(book, ["AAPL"], CONFIG)
    assert "by hand" in book.sessions[-1]["note"]
    assert "AAPL" in book.sessions[-1]["note"]


# --- the endpoint ------------------------------------------------------------
#
# The button is the point of this feature, so the wire between it and the book
# is worth its own tests: the refusals in particular, because each of them is a
# way for the book to be quietly corrupted rather than to visibly fail.

pytest.importorskip("yfinance", reason="the web server imports the data providers")


@pytest.fixture
def book_file(tmp_path, monkeypatch):
    """Point the endpoint at a throwaway book.

    `Book.load` and `Book.save` take BOOK_PATH as a DEFAULT ARGUMENT, bound
    when the class was defined, so rebinding the module attribute does not
    reach them — the tests would have run against the real trading book. The
    defaults themselves are replaced instead.
    """
    from assistant.paper.book import Book

    path = str(tmp_path / "book.json")
    real_load, real_save = Book.load.__func__, Book.save

    monkeypatch.setattr(Book, "load", classmethod(
        lambda cls, p=None: real_load(cls, p or path)))
    monkeypatch.setattr(Book, "save", (
        lambda self, p=None, archive_closed=False: real_save(self, p or path,
                                                             archive_closed)))
    return path


@pytest.fixture
def client(book_file):
    from assistant.web.server import app

    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_the_endpoint_refuses_a_request_that_names_nothing(client):
    """'Close everything' and 'the UI sent me an empty list' must never be the
    same request."""
    r = client.post("/api/paper/close", json={})
    assert r.status_code == 400
    assert "all" in r.get_json()["error"]


def test_the_endpoint_refuses_when_there_is_no_book(client):
    r = client.post("/api/paper/close", json={"all": True})
    assert r.status_code == 400
    assert "no paper book" in r.get_json()["error"]


def test_the_endpoint_refuses_while_a_session_is_running(client, monkeypatch, tmp_path):
    """A session holds the book in memory and saves it whole at the end, so a
    manual close landing mid-run is silently reverted — the position reappears
    with no error anywhere."""
    from assistant.web import scheduler

    _book(tmp_path).save()
    monkeypatch.setattr(scheduler, "status", lambda: {"running": "session"})

    r = client.post("/api/paper/close", json={"tickers": ["AAPL"]})
    assert r.status_code == 409
    assert "session is running" in r.get_json()["error"]


def test_the_endpoint_closes_and_persists(client, monkeypatch, tmp_path):
    from assistant.paper.book import Book

    _book(tmp_path).save()
    monkeypatch.setattr(live, "fetch_marks",
                        lambda tickers, config, force=False: ({}, None))

    r = client.post("/api/paper/close", json={"tickers": ["AAPL"]})
    assert r.status_code == 200
    assert [c["ticker"] for c in r.get_json()["closed"]] == ["AAPL"]

    # Persisted, not merely reported.
    assert [p["ticker"] for p in Book.load().positions] == ["MSFT"]


def test_the_endpoint_reports_a_ticker_that_matched_nothing(client, monkeypatch, tmp_path):
    _book(tmp_path).save()
    monkeypatch.setattr(live, "fetch_marks",
                        lambda tickers, config, force=False: ({}, None))

    r = client.post("/api/paper/close", json={"tickers": ["TSLA"]})
    assert r.status_code == 404
    assert "TSLA" in r.get_json()["error"]


# --- closing when no market is open ------------------------------------------
#
# An earlier version REFUSED this, because it had twice seen 92 positions
# closed on a Sunday for a realised loss of £206,947 that no market produced.
# The second of those was not a bug — it was the owner pressing the button and
# meaning it. Refusing would have blocked a legitimate instruction: "get me
# flat" is exactly what that control is for, and whether to be flat is not this
# function's call.
#
# The action was never the problem. The ACCOUNTING was. You cannot sell on a
# Sunday, so the last mark is the only price there is, and what comes out of it
# is an assumption rather than money. It is recorded, and it is labelled.

from datetime import datetime, timezone      # noqa: E402


def _sunday():
    """2026-08-23. Every market shut."""
    return datetime(2026, 8, 23, 19, 20, tzinfo=timezone.utc)


def _weekday_open():
    """2026-08-21, 16:00 London — the US session is trading."""
    return datetime(2026, 8, 21, 15, 0, tzinfo=timezone.utc)


def _stale_book(tmp_path, count):
    book = Book.load(str(tmp_path / "book.json"))
    book.start(1_000_000.0, "USD", "2026-08-18")
    for i in range(count):
        book.open_position(ticker=f"T{i:02d}", direction="long", shares=10,
                           price=100.0, stop=90.0, target=130.0,
                           strategy="ts_momentum", regime="TRENDING_UP",
                           date="2026-08-18", bar_date="2026-08-20")
        book.positions[-1]["last_price"] = 95.0
    return book


def test_going_flat_with_markets_shut_is_allowed(tmp_path):
    """It is the owner's book and the owner's decision. Nothing here gets to
    veto it."""
    book = _stale_book(tmp_path, 92)
    out = manual.close_all(book, CONFIG, now=_sunday())
    assert len(out["closed"]) == 92
    assert book.positions == []


def test_but_the_pnl_is_labelled_provisional(tmp_path):
    """What comes out of a stale mark is what the arithmetic says, not what
    anyone was paid."""
    book = _stale_book(tmp_path, 92)
    out = manual.close_all(book, CONFIG, now=_sunday())
    assert out["provisional"] is True
    assert "PROVISIONAL" in out["stale_warning"]
    assert "Monday" in out["stale_warning"]
    assert all(c["provisional"] for c in out["closed"])


def test_the_label_is_on_the_TRADE_not_only_in_the_response(tmp_path):
    """A provisional P&L flagged only in the reply is unflagged the moment
    anyone reads the journal instead."""
    book = _stale_book(tmp_path, 3)
    manual.close_all(book, CONFIG, now=_sunday())
    assert all(t["provisional_pnl"] for t in book.closed)


def test_the_days_record_says_the_pnl_was_provisional(tmp_path):
    book = _stale_book(tmp_path, 3)
    manual.close_all(book, CONFIG, now=_sunday())
    assert "PROVISIONAL" in book.daily[-1]["note"]
    assert "PROVISIONAL" in book.sessions[-1]["note"]


def test_nothing_is_labelled_provisional_while_a_market_trades(tmp_path):
    book = _stale_book(tmp_path, 92)
    out = manual.close_all(book, CONFIG, now=_weekday_open())
    assert out["provisional"] is False
    assert out["stale_warning"] is None
    assert not any(t["provisional_pnl"] for t in book.closed)


def test_marks_from_today_are_not_stale_even_with_markets_shut(tmp_path):
    """After the close, today's marks are the freshest that exist. That is the
    ordinary end-of-day case and it is not provisional."""
    book = _stale_book(tmp_path, 92)
    today = _sunday().strftime("%Y-%m-%d")
    for position in book.positions:
        position["bar_date"] = today
    out = manual.close_all(book, CONFIG, now=_sunday())
    assert out["provisional"] is False
