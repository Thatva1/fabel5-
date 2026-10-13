"""London prices from the yfinance BATCH path, and the one-currency book.

On 2026-10-13 a paper session marked London positions opened in pounds with
batch-downloaded prices in pence, and closed 14 of them "at target" in one
session. No network access: the download itself is faked.
"""
import pandas as pd

from assistant.paper import session
from assistant.providers import bulk


def _grouped(frames):
    """The shape yf.download(..., group_by="ticker") returns for several symbols."""
    return pd.concat(frames, axis=1)


def _bars(close):
    index = pd.date_range("2026-10-01", periods=3, freq="D")
    return pd.DataFrame({"Open": close, "High": close, "Low": close,
                         "Close": close, "Volume": 1_000_000.0}, index=index)


def _fake_download(monkeypatch, frames):
    import yfinance as yf
    monkeypatch.setattr(yf, "download", lambda *a, **k: _grouped(frames))
    monkeypatch.setattr(bulk, "_download", lambda *a, **k: _grouped(frames))


def test_ohlcv_history_puts_london_in_pounds(monkeypatch):
    _fake_download(monkeypatch, {"GLEN.L": _bars(591.4), "AAPL": _bars(250.0)})
    out = bulk.ohlcv_history(["GLEN.L", "AAPL"])
    assert out["GLEN.L"]["Close"].iloc[-1] == 5.914
    assert out["GLEN.L"]["High"].iloc[-1] == 5.914
    assert out["GLEN.L"]["Volume"].iloc[-1] == 1_000_000.0     # shares, untouched
    assert out["AAPL"]["Close"].iloc[-1] == 250.0


def test_close_history_puts_london_in_pounds(monkeypatch):
    _fake_download(monkeypatch, {"GLEN.L": _bars(591.4), "AAPL": _bars(250.0)})
    out = bulk.close_history(["GLEN.L", "AAPL"])
    assert out["GLEN.L"].iloc[-1] == 5.914
    assert out["AAPL"].iloc[-1] == 250.0


def test_liquidity_table_values_london_in_pounds(monkeypatch):
    _fake_download(monkeypatch, {"GLEN.L": _bars(591.4), "AAPL": _bars(250.0)})
    out = bulk.liquidity_table(["GLEN.L", "AAPL"])
    assert out["GLEN.L"]["price"] == 5.914
    assert out["GLEN.L"]["dollar_volume"] == 5.914 * 1_000_000.0


def test_a_pounds_entry_is_not_a_target_hit_on_a_pence_mark():
    """The exact failure: entry 5.46, target 6.74, Yahoo close 591.4p."""
    frame = bulk._in_major_units("GLEN.L", _bars(591.4))
    assert frame["High"].iloc[-1] < 6.74


def test_old_yfinance_cache_is_converted_once():
    payload = {"units": 2, "price_source": "yfinance",
               "frames": {"GLEN.L": _bars(591.4), "AAPL": _bars(250.0)}}
    frames = session._in_current_units(payload)
    assert frames["GLEN.L"]["Close"].iloc[-1] == 5.914
    assert frames["AAPL"]["Close"].iloc[-1] == 250.0

    current = {"units": session.HISTORY_CACHE_UNITS, "price_source": "yfinance",
               "frames": {"GLEN.L": _bars(5.914)}}
    assert session._in_current_units(current)["GLEN.L"]["Close"].iloc[-1] == 5.914


def test_version_two_ibkr_cache_is_not_divided_again():
    payload = {"units": 2, "price_source": "ibkr", "frames": {"GLEN.L": _bars(5.914)}}
    assert session._in_current_units(payload)["GLEN.L"]["Close"].iloc[-1] == 5.914


def test_usd_book_keeps_foreign_listings_out():
    kept, foreign = session.split_by_currency(
        ["AAPL", "BRK-B", "GLEN.L", "SAP.DE", "ES=F", "EURUSD=X", "^GSPC"], "USD")
    assert foreign == ["GLEN.L", "SAP.DE"]
    assert kept == ["AAPL", "BRK-B", "ES=F", "EURUSD=X", "^GSPC"]


def test_gbp_book_keeps_us_listings_out():
    kept, foreign = session.split_by_currency(["AAPL", "GLEN.L"], "GBP")
    assert kept == ["GLEN.L"] and foreign == ["AAPL"]


def test_a_void_archive_is_never_carried_forward(tmp_path):
    import json
    from assistant.paper.book import Book

    book_path = str(tmp_path / "paper_book.json")
    for stamp, equity, extra in (("20260101T000000Z", 1_000.0, {}),
                                 ("20260201T000000Z", 5_000.0, {"void": "pence bug"})):
        book = Book({})
        book.start(equity, "USD", "2026-01-01")
        with open(str(tmp_path / f"paper_book-archived-{stamp}.json"), "w") as h:
            json.dump(dict(book.to_dict(), **extra), h)

    carried = session._carried_equity({"paper": {"carry_forward_equity": True}}, book_path)
    assert carried["equity"] == 1_000.0
    assert carried["file"] == "paper_book-archived-20260101T000000Z.json"
