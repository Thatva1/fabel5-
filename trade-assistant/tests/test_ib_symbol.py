"""Audit finding B-3: Yahoo symbols must be translated before IB sees them.

Yahoo suffixes the exchange (TSCO.L); IB wants the bare symbol plus a currency
and routes with SMART. Passing the suffixed form simply fails to resolve, which
is how the borrow check came to answer UNKNOWN for every non-US name.

Imported lazily so this module skips cleanly when neither IB library is
installed — the broker libraries are conditional on the Python version.
"""
import pytest

pytest.importorskip("ib_insync", reason="needs an IB client library")

from assistant.broker.ibkr import ib_symbol   # noqa: E402


@pytest.mark.parametrize("yahoo,expected", [
    ("TSCO.L", "TSCO"),        # London
    ("VOD.L", "VOD"),
    ("AIR.PA", "AIR"),         # Paris
    ("SAP.DE", "SAP"),         # Xetra
    ("ASML.AS", "ASML"),       # Amsterdam
    ("NESN.SW", "NESN"),       # Switzerland
    ("ENI.MI", "ENI"),         # Milan
    ("SHOP.TO", "SHOP"),       # Toronto
    ("7203.T", "7203"),        # Tokyo
    ("0700.HK", "0700"),       # Hong Kong
    ("BHP.AX", "BHP"),         # Sydney
    ("RELIANCE.NS", "RELIANCE"),   # India
])
def test_exchange_suffixes_are_stripped(yahoo, expected):
    assert ib_symbol(yahoo) == expected


@pytest.mark.parametrize("symbol", ["AAPL", "MSFT", "NVDA", "BRK", "TSLA"])
def test_plain_us_symbols_are_untouched(symbol):
    assert ib_symbol(symbol) == symbol


@pytest.mark.parametrize("symbol", ["BRK.B", "BF.A", "RDS.A"])
def test_share_class_markers_survive(symbol):
    """These dots are share classes, not exchanges. Stripping them would send
    an order for the wrong security — worse than not resolving at all."""
    assert ib_symbol(symbol) == symbol


@pytest.mark.parametrize("weird", ["", None, ".", ".L"])
def test_degenerate_input_is_returned_unchanged(weird):
    assert ib_symbol(weird) == weird


def test_unknown_suffixes_are_left_alone_rather_than_guessed():
    """Better to fail to resolve loudly than to mangle a symbol silently."""
    assert ib_symbol("FOO.ZZZ") == "FOO.ZZZ"
