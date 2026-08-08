"""DataProvider interface — the single seam all market data flows through.

To add a paid provider later (EODHD, FMP, Alpaca, …): implement this interface
in a new file and register it in router.py. Nothing else changes.
"""
import re

# FRED and Finnhub both require the API key as a URL query parameter, so a
# failed request's exception message contains the full URL — key included.
# Those messages reach the dashboard through provider_status(), which is served
# by GET /api/state. Every string that could carry one goes through redact()
# first. Matches ?api_key=... / &token=... / "apikey": "..." and friends.
# The optional quote after the name matters: JSON error bodies come through as
# {"token": "..."}, where the name is quoted too.
_SECRET_QUERY_RE = re.compile(
    r"((?:api[_-]?key|apikey|token|access[_-]?token|secret|password)"
    r"[\"']?\s*[=:]\s*[\"']?)([^&\s\"'<>]+)", re.IGNORECASE)

# Bare key material that appears without a name in front of it (e.g. an
# Anthropic key echoed inside an SDK error). Prefixes are vendor-stable.
_BARE_KEY_RE = re.compile(r"\b(sk-ant-|sk-|xoxb-|ghp_)[A-Za-z0-9_\-]{8,}")


def redact(text):
    """Replace anything that looks like a credential with '***'.

    Belt and braces: providers scrub their own messages at the point they build
    them, and this runs again on whatever reaches the health record. The second
    pass is what protects providers added later, whose authors will not have
    read this comment.
    """
    if text is None:
        return None
    out = _SECRET_QUERY_RE.sub(lambda m: m.group(1) + "***", str(text))
    return _BARE_KEY_RE.sub(lambda m: m.group(1) + "***", out)


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
