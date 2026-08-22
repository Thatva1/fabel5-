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

# Every test here exercises the yfinance provider, so skipping the whole module
# is right — but it must SKIP rather than abort collection. Importing the
# provider unconditionally used to take the entire test run down on a clean
# checkout, since requirements.txt installs some dependencies conditionally.
pytest.importorskip("yfinance", reason="these tests exercise the yfinance provider")

from assistant.providers import yfinance_provider as yfp                      # noqa: E402
from assistant.providers.yfinance_provider import (YFinanceProvider,          # noqa: E402
                                                   resolve_currency)


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


# --- the licensed feed has the same problem, with no signal to detect it -----
#
# IB reports an LSE share's currency as "GBP" and quotes it in PENCE anyway.
# Measured against the account's own coverage file: AZN.L 11840.0 (£118.40),
# BP.L 535.0 (£5.35), RIO.L 7106.0 (£71.06). Unlike Yahoo there is no "GBp"
# string to key on, so the venue is the signal — and the book prices from this
# feed, which makes it the copy of the bug that actually costs money.

def test_ibkr_lse_bars_are_divided_to_pounds():
    from assistant.providers.ibkr_provider import IBKRDataProvider

    pence = pd.DataFrame(
        {"Open": [11800.0], "High": [11900.0], "Low": [11750.0],
         "Close": [11840.0], "Volume": [1_000_000]},
        index=pd.to_datetime(["2026-08-18"]))

    pounds = IBKRDataProvider.to_major_units("AZN.L", pence)
    assert pounds["Close"].iloc[0] == pytest.approx(118.40)
    assert pounds["High"].iloc[0] == pytest.approx(119.00)
    # Volume is a share count, not a price. Dividing it would understate
    # liquidity by 100 and drop London out of every liquidity screen.
    assert pounds["Volume"].iloc[0] == 1_000_000
    # The caller's frame must not be rewritten underneath it.
    assert pence["Close"].iloc[0] == 11840.0


def test_ibkr_non_lse_bars_are_untouched():
    from assistant.providers.ibkr_provider import IBKRDataProvider

    for ticker in ("AAPL", "SAP.DE", "AIR.PA", "ES=F", "EURUSD=X"):
        assert IBKRDataProvider.minor_unit_divisor(ticker) == 1.0

    dollars = pd.DataFrame(
        {"Open": [200.0], "High": [201.0], "Low": [199.0],
         "Close": [200.5], "Volume": [10]},
        index=pd.to_datetime(["2026-08-18"]))
    assert IBKRDataProvider.to_major_units("AAPL", dollars) is dollars


def test_ibkr_lse_divisor_is_venue_not_suffix_spelling():
    from assistant.providers.ibkr_provider import IBKRDataProvider

    assert IBKRDataProvider.minor_unit_divisor("TSCO.L") == 100.0
    assert IBKRDataProvider.minor_unit_divisor("tsco.l") == 100.0
