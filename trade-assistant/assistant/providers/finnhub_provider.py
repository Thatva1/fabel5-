"""Finnhub provider — timely company news and the earnings calendar.

Free API key: https://finnhub.io (register), goes in .env as FINNHUB_API_KEY.
Free tier is ~60 calls/min -> every request goes through a rate limiter.
Some endpoints (economic calendar) are paid-only; those degrade gracefully.
"""
from datetime import datetime, timedelta, timezone

import requests

from ..core.config import api_key
from ..core.models import FRESH_LIVE, utcnow
from .base import DataProvider, ProviderUnavailable
from .cache import RateLimiter

BASE_URL = "https://finnhub.io/api/v1"
SOURCE = "Finnhub"


class FinnhubProvider(DataProvider):
    name = "finnhub"

    def __init__(self, max_calls_per_min=55):
        self._limiter = RateLimiter(max_calls_per_min)

    def is_available(self):
        return bool(api_key("FINNHUB_API_KEY"))

    def _get(self, path, params):
        if not self.is_available():
            raise ProviderUnavailable("finnhub: FINNHUB_API_KEY not set")
        self._limiter.acquire()
        try:
            resp = requests.get(f"{BASE_URL}/{path}",
                                params={**params, "token": api_key("FINNHUB_API_KEY")},
                                timeout=15)
        except Exception as exc:
            raise ProviderUnavailable(f"finnhub: request failed: {exc}")
        if resp.status_code in (401, 403):
            raise ProviderUnavailable(f"finnhub: {path} not available on this plan "
                                      f"(HTTP {resp.status_code})")
        if resp.status_code == 429:
            raise ProviderUnavailable("finnhub: rate limit hit")
        try:
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            raise ProviderUnavailable(f"finnhub: bad response from {path}: {exc}")

    def get_news(self, ticker, limit=8):
        now = datetime.now(timezone.utc)
        raw = self._get("company-news", {
            "symbol": ticker,
            "from": (now - timedelta(days=10)).strftime("%Y-%m-%d"),
            "to": now.strftime("%Y-%m-%d"),
        })
        if not isinstance(raw, list):
            raise ProviderUnavailable("finnhub: unexpected news payload")
        items = []
        for entry in raw:
            if not entry.get("headline"):
                continue
            published = entry.get("datetime")
            if published:
                published = datetime.fromtimestamp(published, tz=timezone.utc).isoformat(timespec="seconds")
            items.append({
                "title": entry["headline"],
                "publisher": entry.get("source"),
                "published": published,
                "url": entry.get("url"),
                "source": SOURCE,
                "freshness": FRESH_LIVE,
            })
            if len(items) >= limit:
                break
        if not items:
            raise ProviderUnavailable(f"finnhub: no news for {ticker}")
        return items

    def get_peers(self, ticker):
        """Peer tickers for valuation comparison (free tier)."""
        peers = self._get("stock/peers", {"symbol": ticker})
        if not isinstance(peers, list) or not peers:
            raise ProviderUnavailable(f"finnhub: no peers for {ticker}")
        return [p for p in peers if p and p.upper() != ticker.upper()][:6]

    def get_recommendations(self, ticker):
        """Analyst rating distribution — the spread, not just a mean target."""
        rows = self._get("stock/recommendation", {"symbol": ticker})
        if not isinstance(rows, list) or not rows:
            raise ProviderUnavailable(f"finnhub: no recommendations for {ticker}")
        latest = sorted(rows, key=lambda r: r.get("period", ""), reverse=True)[0]
        return {
            "period": latest.get("period"),
            "strong_buy": latest.get("strongBuy"), "buy": latest.get("buy"),
            "hold": latest.get("hold"), "sell": latest.get("sell"),
            "strong_sell": latest.get("strongSell"),
            "source": SOURCE, "freshness": FRESH_LIVE,
        }

    def get_earnings(self, ticker):
        now = datetime.now(timezone.utc)
        out = {"next_earnings_date": None, "recent_quarters": [],
               "source": SOURCE, "freshness": FRESH_LIVE, "as_of": utcnow()}

        upcoming = self._get("calendar/earnings", {
            "symbol": ticker,
            "from": now.strftime("%Y-%m-%d"),
            "to": (now + timedelta(days=120)).strftime("%Y-%m-%d"),
        }).get("earningsCalendar", [])
        if upcoming:
            out["next_earnings_date"] = sorted(e.get("date", "") for e in upcoming if e.get("date"))[0]

        past = self._get("calendar/earnings", {
            "symbol": ticker,
            "from": (now - timedelta(days=400)).strftime("%Y-%m-%d"),
            "to": now.strftime("%Y-%m-%d"),
        }).get("earningsCalendar", [])
        for e in sorted(past, key=lambda x: x.get("date", ""), reverse=True)[:4]:
            estimate, actual = e.get("epsEstimate"), e.get("epsActual")
            surprise = None
            if estimate not in (None, 0) and actual is not None:
                surprise = round((actual - estimate) / abs(estimate) * 100, 1)
            out["recent_quarters"].append({
                "date": e.get("date"),
                "eps_estimate": estimate,
                "eps_reported": actual,
                "surprise_pct": surprise,
            })
        if not out["next_earnings_date"] and not out["recent_quarters"]:
            raise ProviderUnavailable(f"finnhub: no earnings data for {ticker}")
        return out
