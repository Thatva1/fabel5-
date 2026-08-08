"""Yahoo Finance provider (via yfinance) — global prices, volume, fundamentals,
FX rates, symbol search, plus fallback news/earnings/macro when the dedicated
providers (Finnhub, FRED) aren't configured.

Freshness: quotes are delayed for most users -> everything here is tagged
'delayed' except historical bars which are 'eod' by nature.
"""
import math

import yfinance as yf

from ..core.models import FRESH_DELAYED, FRESH_EOD, fact
from .base import DataProvider, ProviderUnavailable

SOURCE = "Yahoo Finance (yfinance)"

# Secondary cross-listing venues. A search for a company name should not return
# its Stuttgart or Mexican line ahead of its primary listing — these carry the
# same economics with a fraction of the liquidity.
NOISE_EXCHANGES = {"FRA", "STU", "DUS", "BER", "HAM", "MUN", "GER_OTC",
                   "MEX", "SAO", "VIE", "TLO", "PNK", "OID", "OTC"}

# Some exchanges quote in a currency's MINOR unit. The London Stock Exchange
# quotes in pence (GBp): Shell printing 3328.50 means £33.285, not £3,328.
# Uppercasing 'GBp' to 'GBP' silently introduces a 100x error in position size,
# exposure and max loss — so minor units are converted to major here, once, at
# the data boundary.
MINOR_UNITS = {
    "GBP": ("GBP", 100.0),   # yfinance sometimes normalises the case
    "GBX": ("GBP", 100.0),   # pence, alternative code
    "ZAC": ("ZAR", 100.0),   # South African cents
    "ILA": ("ILS", 100.0),   # Israeli agorot
}

# Fundamentals fields quoted PER SHARE, and therefore in the same minor unit as
# the price. Everything else in get_fundamentals (market cap, revenue, EBITDA,
# cash, debt) comes from Yahoo in `financialCurrency` — already the MAJOR unit,
# even for an LSE listing — so it must be left alone. Verified against NG.L:
# price 1187.5 (pence) but marketCap 59.7bn (pounds).
PER_SHARE_PRICE_FIELDS = (
    "fifty_two_week_high",
    "fifty_two_week_low",
    "two_hundred_day_average",
    "analyst_target_mean",
    "analyst_target_high",
    "analyst_target_low",
)


def resolve_currency(raw):
    """(major_currency, divisor) for a raw yfinance currency string.

    Detection is case-sensitive on purpose: 'GBp' is pence, 'GBP' is pounds.
    """
    if not raw:
        return "USD", 1.0
    if raw == "GBp" or raw == "GBX":
        return "GBP", 100.0
    if raw == "ZAc":
        return "ZAR", 100.0
    if raw == "ILA":
        return "ILS", 100.0
    return raw.upper(), 1.0


def _clean(value):
    if value is None:
        return None
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


class YFinanceProvider(DataProvider):
    name = "yfinance"

    def is_available(self):
        return True  # no key needed

    def get_prices(self, ticker, period="1y"):
        # Transport failures (DNS, timeouts, Yahoo 5xx) must surface as
        # ProviderUnavailable, not raw exceptions: an upstream outage is an
        # expected operational condition, and callers turn this into a clean
        # "data temporarily unavailable" response instead of a crash.
        try:
            df = yf.Ticker(ticker).history(period=period, auto_adjust=True)
        except Exception as exc:
            raise ProviderUnavailable(
                f"{self.name}: market data request failed for {ticker} "
                f"({type(exc).__name__}: {exc})")
        if df is None or df.empty:
            raise ProviderUnavailable(f"{self.name}: no price data for {ticker}")
        df = df.dropna(subset=["Close"])
        if df.empty:
            raise ProviderUnavailable(f"{self.name}: no usable closes for {ticker}")

        # Convert minor-unit quotes (LSE pence) to major units so every price
        # leaving this provider is in whole currency units.
        _, divisor = self._currency_and_scale(ticker)
        if divisor != 1.0:
            df = df.copy()
            for col in ("Open", "High", "Low", "Close", "Adj Close"):
                if col in df.columns:
                    df[col] = df[col] / divisor
        return df

    def _currency_and_scale(self, ticker):
        try:
            raw = yf.Ticker(ticker).fast_info.get("currency")
        except Exception:
            return "USD", 1.0
        return resolve_currency(raw)

    def get_instrument_currency(self, ticker):
        """Major-unit currency code (GBP, never GBp)."""
        return self._currency_and_scale(ticker)[0]

    def get_fx_rate(self, from_ccy, to_ccy):
        from_ccy, to_ccy = from_ccy.upper(), to_ccy.upper()
        if from_ccy == to_ccy:
            return 1.0
        pair = f"{from_ccy}{to_ccy}=X"
        try:
            df = yf.Ticker(pair).history(period="5d")
            rate = float(df["Close"].dropna().iloc[-1])
            if rate > 0:
                return rate
        except Exception:
            pass
        raise ProviderUnavailable(f"{self.name}: no FX rate for {pair}")

    def get_fundamentals(self, ticker):
        try:
            info = yf.Ticker(ticker).info or {}
        except Exception as exc:
            raise ProviderUnavailable(f"{self.name}: info failed for {ticker}: {exc}")
        raw = {
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "market_cap": info.get("marketCap"),
            "trailing_pe": info.get("trailingPE"),
            "forward_pe": info.get("forwardPE"),
            "price_to_sales": info.get("priceToSalesTrailing12Months"),
            "peg_ratio": info.get("pegRatio"),
            "revenue_growth_yoy": info.get("revenueGrowth"),
            "earnings_growth_yoy": info.get("earningsGrowth"),
            "profit_margin": info.get("profitMargins"),
            "gross_margin": info.get("grossMargins"),
            "operating_margin": info.get("operatingMargins"),
            "return_on_equity": info.get("returnOnEquity"),
            "debt_to_equity": info.get("debtToEquity"),
            "total_cash": info.get("totalCash"),
            "total_debt": info.get("totalDebt"),
            "ebitda": info.get("ebitda"),
            "total_revenue": info.get("totalRevenue"),
            "current_ratio": info.get("currentRatio"),
            "quick_ratio": info.get("quickRatio"),
            "enterprise_value": info.get("enterpriseValue"),
            "free_cash_flow": info.get("freeCashflow"),
            "shares_outstanding": info.get("sharesOutstanding"),
            "beta": info.get("beta"),
            "short_percent_of_float": info.get("shortPercentOfFloat"),
            # Dividend picture — central to the case for income/staple names.
            "dividend_yield_pct": info.get("dividendYield"),
            "payout_ratio": info.get("payoutRatio"),
            "five_year_avg_dividend_yield_pct": info.get("fiveYearAvgDividendYield"),
            # Analyst dispersion, not just the mean target.
            "analyst_target_mean": info.get("targetMeanPrice"),
            "analyst_target_high": info.get("targetHighPrice"),
            "analyst_target_low": info.get("targetLowPrice"),
            "analyst_count": info.get("numberOfAnalystOpinions"),
            "analyst_recommendation": info.get("recommendationKey"),
            # Longer-term trend context beyond the scanner's 20/50-day window.
            "fifty_two_week_high": info.get("fiftyTwoWeekHigh"),
            "fifty_two_week_low": info.get("fiftyTwoWeekLow"),
            "two_hundred_day_average": info.get("twoHundredDayAverage"),
        }
        # Same minor-unit conversion get_prices applies, but ONLY to the
        # per-share price fields. Yahoo quotes an LSE share in pence while
        # reporting marketCap/revenue/EBITDA for the same company in pounds, so
        # dividing everything would be as wrong as dividing nothing.
        #
        # Left unconverted, the thesis engine is handed "price 11.88" beside
        # "52-week high 1428.5" for the same stock and can conclude the share
        # has collapsed 99%. The risk maths never saw these fields — sizing uses
        # get_prices, which was already correct — but a bull case built on a
        # phantom crash is exactly the kind of grounded-looking nonsense the
        # citation rules exist to prevent.
        _, divisor = self._currency_and_scale(ticker)
        if divisor != 1.0:
            for key in PER_SHARE_PRICE_FIELDS:
                if isinstance(raw.get(key), (int, float)):
                    raw[key] = raw[key] / divisor

        out = {}
        for key, value in raw.items():
            value = _clean(value)
            if key in ("sector", "industry"):
                out[key] = value  # plain strings, not figures needing citation
            else:
                out[key] = fact(value, SOURCE, FRESH_DELAYED)
        return out

    def get_news(self, ticker, limit=8):
        items = []
        try:
            raw = yf.Ticker(ticker).news or []
        except Exception as exc:
            raise ProviderUnavailable(f"{self.name}: news failed for {ticker}: {exc}")
        for entry in raw[: limit * 2]:
            content = entry.get("content", entry)
            title = content.get("title")
            if not title:
                continue
            provider = content.get("provider") or {}
            items.append({
                "title": title,
                "publisher": provider.get("displayName") or entry.get("publisher"),
                "published": content.get("pubDate") or content.get("providerPublishTime"),
                "url": (content.get("canonicalUrl") or {}).get("url") or content.get("link"),
                "source": SOURCE,
                "freshness": FRESH_DELAYED,
            })
            if len(items) >= limit:
                break
        return items

    def get_earnings(self, ticker):
        from datetime import datetime, timezone
        out = {"next_earnings_date": None, "recent_quarters": [],
               "source": SOURCE, "freshness": FRESH_DELAYED}
        t = yf.Ticker(ticker)
        try:
            dates = (t.calendar or {}).get("Earnings Date") or []
            if dates:
                out["next_earnings_date"] = str(dates[0])
        except Exception:
            pass
        try:
            ed = t.earnings_dates
            if ed is not None and not ed.empty:
                now = datetime.now(timezone.utc)
                past = ed[ed.index.tz_convert("UTC") <= now] if ed.index.tz is not None else ed
                for idx, row in past.head(4).iterrows():
                    out["recent_quarters"].append({
                        "date": str(idx.date()),
                        "eps_estimate": _clean(row.get("EPS Estimate")),
                        "eps_reported": _clean(row.get("Reported EPS")),
                        "surprise_pct": _clean(row.get("Surprise(%)")),
                    })
        except Exception:
            pass
        return out

    def get_macro(self):
        """Fallback macro from index tickers when FRED isn't configured."""
        out = {}
        for key, symbol, unit in (
            ("ten_year_yield", "^TNX", "%"),
            ("three_month_yield", "^IRX", "%"),
            ("vix", "^VIX", None),
        ):
            try:
                df = self.get_prices(symbol, period="1mo")
                asof = str(df.index[-1].date())
                out[key] = fact(round(float(df["Close"].iloc[-1]), 2),
                                SOURCE, FRESH_EOD, timestamp=asof, unit=unit)
            except Exception:
                out[key] = None
        return out

    def search_symbol(self, query, max_results=5, preferred_exchanges=None):
        """Global symbol search. `preferred_exchanges` biases results toward the
        markets you actually trade — without it Yahoo returns Frankfurt or
        NYSE listings ahead of the London line for UK companies."""
        try:
            results = yf.Search(query, max_results=max(max_results * 3, 12)).quotes or []
        except Exception as exc:
            raise ProviderUnavailable(f"{self.name}: search failed: {exc}")
        out = []
        for q in results:
            if not q.get("symbol"):
                continue
            out.append({
                "symbol": q["symbol"],
                "name": q.get("shortname") or q.get("longname") or "",
                "type": q.get("quoteType"),
                "exchange": q.get("exchange"),
            })

        prefs = [e.upper() for e in (preferred_exchanges or [])]
        if prefs:
            # Yahoo's ordering already reflects the primary/most-liquid listing,
            # so preference is a BOUNDED nudge, never an override — sorting hard
            # by preference resolved "apple" to its German cross-listing.
            #
            # The bigger win is demoting cross-listing noise: German regional
            # exchanges and foreign OTC lines are almost never what you want
            # when you search a company by name.
            home = set(prefs[:2])                       # your own market
            regional = set(prefs[2:12])                 # other primary venues
            def rank(row, index):
                ex = (row.get("exchange") or "").upper()
                score = float(index)
                if ex in home:
                    score -= 2.5
                elif ex in regional:
                    score -= 1.0
                if ex in NOISE_EXCHANGES:
                    score += 3.0
                if (row.get("type") or "").upper() != "EQUITY":
                    score += 1.5
                return score
            out = [row for _, row in sorted(
                enumerate(out), key=lambda pair: rank(pair[1], pair[0]))]
        return out[:max_results]
