"""Context Reader — assembles the cited, timestamped evidence bundle for a
flagged ticker. Every figure in this bundle carries source + timestamp +
freshness; the thesis engine may only reason over what appears here."""
from ..core.models import FRESH_DELAYED, fact, utcnow


def build_context(ticker, router, config):
    fundamentals = router.get_fundamentals(ticker)
    sector = fundamentals.get("sector")
    currency = router.get_instrument_currency(ticker)
    return {
        "ticker": ticker,
        "instrument_currency": currency,
        "as_of": utcnow(),
        "news": router.get_news(ticker, limit=8),
        "earnings": router.get_earnings(ticker),
        "fundamentals": fundamentals,
        "peer_valuations": router.get_peer_valuations(ticker),
        "analyst_recommendations": router.get_recommendations(ticker),
        "macro": router.get_macro(sector=sector),
        # Auto-fetched scheduled releases + FOMC meetings; the config list is
        # only for anything you want to add by hand on top.
        "economic_calendar": router.get_economic_calendar(),
        "user_macro_events": [e for e in config.get("macro_events", [])
                              if "Add upcoming" not in str(e)],
    }


def snapshot_facts(snapshot):
    """Wrap the scanner's price snapshot as cited facts for the thesis prompt."""
    src = "Yahoo Finance daily bars (yfinance)"
    keys = ("price", "change_pct", "volume_ratio", "rsi", "atr", "sma20", "sma50",
            "prior_high_20d", "prior_low_20d")
    return {k: fact(snapshot.get(k), src, FRESH_DELAYED) for k in keys if snapshot.get(k) is not None}
