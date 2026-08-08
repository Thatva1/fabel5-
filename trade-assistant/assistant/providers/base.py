"""DataProvider interface — the single seam all market data flows through.

To add a paid provider later (EODHD, FMP, Alpaca, …): implement this interface
in a new file and register it in router.py. Nothing else changes.
"""


class ProviderUnavailable(Exception):
    """Raised when a provider can't serve a request (no key, quota, endpoint down)."""


class DataProvider:
    name = "abstract"

    def is_available(self):
        """Cheap static check (e.g. is the API key set). Not a network probe."""
        return False

    # Each method returns provider-tagged data or raises ProviderUnavailable so
    # the router can fall through to the next provider.

    def get_prices(self, ticker, period="1y"):
        """Daily OHLCV DataFrame (Close, Open, High, Low, Volume)."""
        raise ProviderUnavailable(f"{self.name}: get_prices not supported")

    def get_fundamentals(self, ticker):
        """Dict of Fact dicts: valuation / growth / balance-sheet snapshot."""
        raise ProviderUnavailable(f"{self.name}: get_fundamentals not supported")

    def get_news(self, ticker, limit=8):
        """List of {title, publisher, published, url, source, freshness}."""
        raise ProviderUnavailable(f"{self.name}: get_news not supported")

    def get_earnings(self, ticker):
        """{next_earnings_date, recent_quarters: [...]}, provider-tagged."""
        raise ProviderUnavailable(f"{self.name}: get_earnings not supported")

    def get_macro(self):
        """Dict of Fact dicts: rates / inflation / volatility."""
        raise ProviderUnavailable(f"{self.name}: get_macro not supported")

    def get_fx_rate(self, from_ccy, to_ccy):
        """Spot rate as a float: 1 from_ccy = X to_ccy."""
        raise ProviderUnavailable(f"{self.name}: get_fx_rate not supported")

    def search_symbol(self, query, max_results=5):
        """Resolve a company name / fuzzy ticker to [{symbol, name, type, exchange}]."""
        raise ProviderUnavailable(f"{self.name}: search_symbol not supported")
