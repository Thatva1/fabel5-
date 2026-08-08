import pytest

from assistant.core.fx import MissingRateError, convert, normalize_position_value

RATES = {"EURUSD": 1.10, "GBPUSD": 1.30, "USDJPY": 150.0}


def test_identity():
    assert convert(100, "USD", "USD", {}) == 100.0


def test_direct_pair():
    assert convert(100, "EUR", "USD", RATES) == pytest.approx(110.0)


def test_inverse_pair():
    # Only EURUSD is known; USD->EUR must use its inverse
    assert convert(110, "USD", "EUR", RATES) == pytest.approx(100.0)


def test_cross_via_usd():
    # EUR->GBP with no direct pair: EUR->USD->GBP
    result = convert(100, "EUR", "GBP", RATES)
    assert result == pytest.approx(100 * 1.10 / 1.30)


def test_jpy_quoted_as_usd_base():
    # USDJPY quoted the other way round: JPY->USD uses the inverse
    assert convert(15000, "JPY", "USD", RATES) == pytest.approx(100.0)


def test_missing_rate_raises():
    with pytest.raises(MissingRateError):
        convert(100, "CHF", "AUD", RATES)


def test_case_insensitive():
    assert convert(100, "eur", "usd", RATES) == pytest.approx(110.0)


def test_position_value_normalization():
    # 10 shares @ 200 EUR = 2000 EUR = 2200 USD at 1.10
    assert normalize_position_value(10, 200, "EUR", "USD", RATES) == pytest.approx(2200.0)


# ---------- minor-unit quotes (the 100x trap) ----------
#
# Everything below needs yfinance. This module used to import the provider at
# module level, so on a clean checkout the whole file aborted COLLECTION with
# ModuleNotFoundError — taking the pure-arithmetic tests above down with it and
# turning one missing optional dependency into a suite that cannot run at all.
#
# A skipif marker rather than importorskip: importorskip at module scope would
# skip this entire file, including the conversion tests that need nothing.

import pytest as _pytest

from .optional_deps import requires_yfinance


@requires_yfinance
@_pytest.mark.parametrize("raw,expected", [
    ("GBp", ("GBP", 100.0)),    # LSE pence — Shell at 3328.50 GBp is £33.29
    ("GBX", ("GBP", 100.0)),
    ("ZAc", ("ZAR", 100.0)),    # South African cents
    ("ILA", ("ILS", 100.0)),    # Israeli agorot
    ("GBP", ("GBP", 1.0)),      # already major units
    ("USD", ("USD", 1.0)),
    ("EUR", ("EUR", 1.0)),
    ("eur", ("EUR", 1.0)),
    (None,  ("USD", 1.0)),
    ("",    ("USD", 1.0)),
])
def test_minor_units_resolved(raw, expected):
    from assistant.providers.yfinance_provider import resolve_currency
    assert resolve_currency(raw) == expected


@requires_yfinance
def test_pence_detection_is_case_sensitive():
    """'GBp' is pence, 'GBP' is pounds — uppercasing one into the other is a
    100x error in position size, exposure and max loss."""
    from assistant.providers.yfinance_provider import resolve_currency
    assert resolve_currency("GBp")[1] == 100.0
    assert resolve_currency("GBP")[1] == 1.0


@requires_yfinance
def test_prices_are_divided_for_pence_quotes(monkeypatch):
    import pandas as pd

    from assistant.providers.yfinance_provider import YFinanceProvider

    frame = pd.DataFrame({"Open": [3300.0], "High": [3350.0], "Low": [3290.0],
                          "Close": [3328.50], "Volume": [1000]})

    class FakeTicker:
        def __init__(self, *a, **k): pass
        fast_info = {"currency": "GBp"}
        def history(self, **kw): return frame.copy()

    monkeypatch.setattr("assistant.providers.yfinance_provider.yf.Ticker", FakeTicker)
    out = YFinanceProvider().get_prices("SHEL.L")
    assert out["Close"].iloc[-1] == _pytest.approx(33.285)
    assert out["High"].iloc[-1] == _pytest.approx(33.50)
    assert YFinanceProvider().get_instrument_currency("SHEL.L") == "GBP"


@requires_yfinance
def test_prices_untouched_for_major_units(monkeypatch):
    import pandas as pd

    from assistant.providers.yfinance_provider import YFinanceProvider

    class FakeTicker:
        def __init__(self, *a, **k): pass
        fast_info = {"currency": "USD"}
        def history(self, **kw): return pd.DataFrame({"Close": [309.38]})

    monkeypatch.setattr("assistant.providers.yfinance_provider.yf.Ticker", FakeTicker)
    assert YFinanceProvider().get_prices("AAPL")["Close"].iloc[-1] == _pytest.approx(309.38)


def test_gbp_base_currency_conversions():
    """Sanity: a UK-based book valued in pounds."""
    rates = {"GBPUSD": 1.3467, "EURGBP": 0.8571}
    assert convert(1000, "USD", "GBP", rates) == _pytest.approx(742.6, abs=0.5)
    assert convert(1000, "EUR", "GBP", rates) == _pytest.approx(857.1, abs=0.5)
    assert convert(1000, "GBP", "GBP", rates) == 1000.0
