"""Minor-unit (pence) handling at the data boundary.

The London Stock Exchange quotes in pence while Yahoo reports the same
company's market cap and revenue in pounds. Getting this half-right is worse
than getting it wrong consistently: the thesis engine is then handed a price of
11.88 next to a 52-week high of 1428.5 for the same share and can reasonably
conclude the stock has collapsed 99%.

No network access — a fake Ticker stands in for yfinance.
"""
import pandas as pd
import pytest

from assistant.providers import yfinance_provider as yfp
from assistant.providers.yfinance_provider import YFinanceProvider, resolve_currency


# Real NG.L (National Grid) shapes: price quoted in GBp, accounts in GBP.
LSE_INFO = {
    "currency": "GBp",
    "financialCurrency": "GBP",
    "currentPrice": 1187.5,
    "fiftyTwoWeekHigh": 1428.5,
    "fiftyTwoWeekLow": 1000.0,
    "twoHundredDayAverage": 1234.095,
    "targetMeanPrice": 1354.667,
    "targetHighPrice": 1500.0,
    "targetLowPrice": 1060.0,
    "marketCap": 59_693_256_704,        # already pounds
    "totalRevenue": 17_686_999_040,     # already pounds
    "ebitda": 7_025_999_872,            # already pounds
    "sector": "Utilities",
    "trailingPE": 15.0,
}

US_INFO = {
    "currency": "USD",
    "financialCurrency": "USD",
    "currentPrice": 311.0,
    "fiftyTwoWeekHigh": 344.57,
    "fiftyTwoWeekLow": 205.59,
    "twoHundredDayAverage": 278.23,
    "targetMeanPrice": 324.01,
    "marketCap": 4_538_800_000_000,
    "sector": "Technology",
}


class _FastInfo(dict):
    pass


class _FakeTicker:
    def __init__(self, info, close=1187.5):
        self.info = info
        self.fast_info = _FastInfo(currency=info["currency"])
        self._close = close

    def history(self, *a, **k):
        return pd.DataFrame({
            "Open": [self._close] * 5, "High": [self._close] * 5,
            "Low": [self._close] * 5, "Close": [self._close] * 5,
            "Volume": [1_000_000] * 5,
        })


@pytest.fixture
def lse(monkeypatch):
    monkeypatch.setattr(yfp.yf, "Ticker", lambda t: _FakeTicker(LSE_INFO, close=1187.5))
    return YFinanceProvider()


@pytest.fixture
def us(monkeypatch):
    monkeypatch.setattr(yfp.yf, "Ticker", lambda t: _FakeTicker(US_INFO, close=311.0))
    return YFinanceProvider()


# --- currency resolution -----------------------------------------------------

def test_pence_is_detected_case_sensitively():
    """'GBp' is pence and 'GBP' is pounds — they differ only by case, and
    treating one as the other is a straight 100x error in position size."""
    assert resolve_currency("GBp") == ("GBP", 100.0)
    assert resolve_currency("GBX") == ("GBP", 100.0)
    assert resolve_currency("USD") == ("USD", 1.0)


def test_other_minor_units_are_handled():
    assert resolve_currency("ZAc") == ("ZAR", 100.0)
    assert resolve_currency("ILA") == ("ILS", 100.0)


# --- prices ------------------------------------------------------------------

def test_lse_prices_are_converted_to_pounds(lse):
    assert float(lse.get_prices("NG.L")["Close"].iloc[-1]) == pytest.approx(11.875)
    assert lse.get_instrument_currency("NG.L") == "GBP"


def test_us_prices_are_left_alone(us):
    assert float(us.get_prices("AAPL")["Close"].iloc[-1]) == pytest.approx(311.0)


# --- fundamentals ------------------------------------------------------------

def _v(fundamentals, key):
    entry = fundamentals.get(key)
    return entry.get("value") if isinstance(entry, dict) else entry


def test_per_share_fundamentals_match_the_price_units(lse):
    """The regression this file exists for: these fields used to come back in
    pence while the price came back in pounds."""
    f = lse.get_fundamentals("NG.L")
    assert _v(f, "fifty_two_week_high") == pytest.approx(14.285)
    assert _v(f, "fifty_two_week_low") == pytest.approx(10.0)
    assert _v(f, "two_hundred_day_average") == pytest.approx(12.34095)
    assert _v(f, "analyst_target_mean") == pytest.approx(13.54667)
    assert _v(f, "analyst_target_high") == pytest.approx(15.0)
    assert _v(f, "analyst_target_low") == pytest.approx(10.6)


def test_company_level_figures_are_not_divided(lse):
    """Market cap, revenue and EBITDA arrive in `financialCurrency` — already
    pounds. Dividing them too would be as wrong as dividing nothing."""
    f = lse.get_fundamentals("NG.L")
    assert _v(f, "market_cap") == 59_693_256_704
    assert _v(f, "total_revenue") == 17_686_999_040
    assert _v(f, "ebitda") == 7_025_999_872


def test_ratios_are_never_scaled(lse):
    """A P/E is a ratio; scaling it by the currency unit would be meaningless."""
    assert _v(lse.get_fundamentals("NG.L"), "trailing_pe") == 15.0


def test_the_price_sits_sensibly_inside_its_own_52_week_range(lse):
    """The end-to-end sanity check a human would do by eye, and the exact
    comparison the thesis engine makes when writing a bull or bear case."""
    f = lse.get_fundamentals("NG.L")
    price = float(lse.get_prices("NG.L")["Close"].iloc[-1])
    assert _v(f, "fifty_two_week_low") <= price <= _v(f, "fifty_two_week_high")


def test_us_fundamentals_are_untouched(us):
    f = us.get_fundamentals("AAPL")
    assert _v(f, "fifty_two_week_high") == pytest.approx(344.57)
    assert _v(f, "analyst_target_mean") == pytest.approx(324.01)
    assert _v(f, "market_cap") == 4_538_800_000_000


# --- short interest units (audit finding B-2) --------------------------------

def test_short_interest_fraction_becomes_a_percentage(monkeypatch):
    """yfinance reports shortPercentOfFloat as a FRACTION. Normalising here is
    what lets borrow.py stop guessing from the magnitude."""
    info = {**US_INFO, "shortPercentOfFloat": 0.18}
    monkeypatch.setattr(yfp.yf, "Ticker", lambda t: _FakeTicker(info, close=311.0))
    assert _v(YFinanceProvider().get_fundamentals("AAPL"),
              "short_percent_of_float") == pytest.approx(18.0)


def test_a_lightly_shorted_name_stays_lightly_shorted(monkeypatch):
    """0.008 is 0.8% of float. The old magnitude heuristic reported it as 80%."""
    info = {**US_INFO, "shortPercentOfFloat": 0.008}
    monkeypatch.setattr(yfp.yf, "Ticker", lambda t: _FakeTicker(info, close=311.0))
    assert _v(YFinanceProvider().get_fundamentals("AAPL"),
              "short_percent_of_float") == pytest.approx(0.8)


def test_one_percent_of_float_is_not_reported_as_one_hundred(monkeypatch):
    info = {**US_INFO, "shortPercentOfFloat": 0.01}
    monkeypatch.setattr(yfp.yf, "Ticker", lambda t: _FakeTicker(info, close=311.0))
    assert _v(YFinanceProvider().get_fundamentals("AAPL"),
              "short_percent_of_float") == pytest.approx(1.0)


def test_missing_short_interest_stays_none(monkeypatch):
    monkeypatch.setattr(yfp.yf, "Ticker", lambda t: _FakeTicker(US_INFO, close=311.0))
    assert _v(YFinanceProvider().get_fundamentals("AAPL"),
              "short_percent_of_float") is None
