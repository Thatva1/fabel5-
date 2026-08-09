"""DataRouter — routes each data type to the best available provider, with
caching and graceful fallbacks. The rest of the app only ever talks to this.

Routing (v1):
  prices / fundamentals / FX / search  -> yfinance
  macro                                -> FRED, falling back to yfinance tickers
  news / earnings                      -> Finnhub, falling back to yfinance
"""
from .base import ProviderUnavailable, redact
from .cache import TTLCache
from .finnhub_provider import FinnhubProvider
from .fred_provider import FredProvider
from .yfinance_provider import YFinanceProvider

SECTOR_ETF = {
    "Technology": "XLK", "Communication Services": "XLC", "Consumer Cyclical": "XLY",
    "Consumer Defensive": "XLP", "Energy": "XLE", "Financial Services": "XLF",
    "Healthcare": "XLV", "Industrials": "XLI", "Basic Materials": "XLB",
    "Real Estate": "XLRE", "Utilities": "XLU",
}


class DataRouter:
    def __init__(self, config):
        provider_cfg = config.get("providers", {})
        self.ttl = provider_cfg.get("cache_ttl_seconds", {})
        self.cache = TTLCache()
        self.health = {}
        self.yf = YFinanceProvider()
        self.fred = FredProvider()
        self.finnhub = FinnhubProvider(provider_cfg.get("finnhub_max_calls_per_min", 55))
        from .global_macro import GlobalMacroProvider
        from .universe import SymbolUniverse
        self.global_macro = GlobalMacroProvider()
        self.universe = SymbolUniverse(self.finnhub)
        # Regions whose macro is pulled every scan. Defaults to the user's home
        # market first — this is not a US-only tool.
        self.macro_regions = config.get("macro_regions") or ["GB", "EU", "US"]
        # Yahoo exchange codes, home markets first. Drives search ranking so the
        # London line beats the Frankfurt/NYSE cross-listing for a UK trader.
        self.preferred_exchanges = config.get("preferred_exchanges") or [
            "LSE", "AMS", "PAR", "GER", "SWX", "MIL", "NMS", "NYQ"]

    def _ttl(self, kind, default):
        return self.ttl.get(kind, default)

    @staticmethod
    def _key(prefix, ticker, *parts):
        """Cache key with the ticker upper-cased, so 'aapl' and 'AAPL' share an
        entry instead of each fetching and storing their own DataFrame."""
        return ":".join([prefix, (ticker or "").upper(), *(str(p) for p in parts)])

    def _record(self, provider_name, error=None):
        """Track real request outcomes so the dashboard can report what is
        actually working, rather than just which API keys are present."""
        entry = self.health.setdefault(provider_name, {"ok": 0, "fail": 0, "last_error": None})
        if error is None:
            entry["ok"] += 1
            entry["last_error"] = None
            entry["last_error_at"] = None
        else:
            from datetime import datetime, timezone
            entry["fail"] += 1
            # Redact again here, not only in the providers. last_error is served
            # to the browser by provider_status() -> GET /api/state, and a
            # provider added later will not have scrubbed its own message.
            entry["last_error"] = redact(error)[:200]
            entry["last_error_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def _first(self, providers, method, *args, **kwargs):
        """Try providers in order; return (result, provider_name)."""
        last_error = None
        for provider in providers:
            try:
                result = getattr(provider, method)(*args, **kwargs), provider.name
                self._record(provider.name)
                return result
            except ProviderUnavailable as exc:
                self._record(provider.name, exc)
                last_error = exc
        raise ProviderUnavailable(str(last_error) if last_error else f"no provider for {method}")

    # ---- prices / FX / search (yfinance only in v1) ----

    def get_prices(self, ticker, period="1y"):
        def fetch():
            try:
                df = self.yf.get_prices(ticker, period)
            except ProviderUnavailable as exc:
                self._record("yfinance", exc)
                raise
            self._record("yfinance")
            return df
        return self.cache.get_or_fetch(
            self._key("prices", ticker, period), self._ttl("prices", 600), fetch)

    def get_instrument_currency(self, ticker):
        return self.cache.get_or_fetch(
            self._key("ccy", ticker), self._ttl("fundamentals", 3600),
            lambda: self.yf.get_instrument_currency(ticker))

    def get_fx_rates(self, currencies, base_ccy):
        """Rates dict for core.fx.convert covering currency->base and ->USD legs."""
        rates = {}
        pairs = set()
        for ccy in set(c.upper() for c in currencies if c):
            for target in {base_ccy.upper(), "USD"}:
                if ccy != target:
                    pairs.add((ccy, target))
        for from_ccy, to_ccy in pairs:
            try:
                rates[from_ccy + to_ccy] = self.cache.get_or_fetch(
                    f"fx:{from_ccy}{to_ccy}", self._ttl("fx", 3600),
                    lambda f=from_ccy, t=to_ccy: self.yf.get_fx_rate(f, t))
            except ProviderUnavailable:
                pass
        return rates

    def search_symbol(self, query, max_results=5):
        return self.yf.search_symbol(query, max_results,
                                     preferred_exchanges=self.preferred_exchanges)

    def suggest(self, query, limit=8):
        """Type-ahead across markets.

        Two sources, merged: the cached US bulk list (instant, offline) and
        Yahoo's live global search (covers LSE/Xetra/Euronext, which Finnhub's
        free tier does not). Results are ordered by your preferred exchanges, so
        a UK user searching 'tesco' gets TSCO.L rather than a Frankfurt line.
        """
        local = self.universe.search(query, limit)
        try:
            remote = self.yf.search_symbol(query, limit,
                                           preferred_exchanges=self.preferred_exchanges)
        except ProviderUnavailable:
            remote = []

        merged, seen = [], set()
        # Every preferred exchange gets the boost, not just the first few —
        # otherwise Tokyo/Swiss/Korea listings lose to US OTC lines.
        home = {e.upper() for e in self.preferred_exchanges}
        for row in remote:
            if (row.get("exchange") or "").upper() in home:
                key = row["symbol"].upper()
                if key not in seen:
                    seen.add(key)
                    merged.append({"symbol": row["symbol"], "name": row["name"],
                                   "type": row.get("type") or "", "exchange": row.get("exchange")})
        for row in local + [{"symbol": r["symbol"], "name": r["name"],
                             "type": r.get("type") or "", "exchange": "US"} for r in []]:
            key = row["symbol"].upper()
            if key not in seen:
                seen.add(key)
                merged.append(row)
        for row in remote:
            key = row["symbol"].upper()
            if key not in seen:
                seen.add(key)
                merged.append({"symbol": row["symbol"], "name": row["name"],
                               "type": row.get("type") or "", "exchange": row.get("exchange")})
        return merged[:limit]

    def universe_size(self):
        return len(self.universe.symbols())

    def universe_rows(self):
        """Every cached listing row: {symbol, name, type}.

        universe_size() answers "how many"; this answers "which", which is what
        a wide-universe strategy run needs in order to screen by instrument type
        before spending a network request on each name.
        """
        return list(self.universe.symbols())

    def get_fundamentals(self, ticker):
        return self.cache.get_or_fetch(
            self._key("fund", ticker), self._ttl("fundamentals", 3600),
            lambda: self.yf.get_fundamentals(ticker))

    # ---- news / earnings (Finnhub -> yfinance) ----

    def get_news(self, ticker, limit=8):
        def fetch():
            result, _ = self._first([self.finnhub, self.yf], "get_news", ticker, limit)
            return result
        try:
            return self.cache.get_or_fetch(self._key("news", ticker), self._ttl("news", 3600), fetch)
        except ProviderUnavailable:
            return []

    def get_earnings(self, ticker):
        def fetch():
            result, _ = self._first([self.finnhub, self.yf], "get_earnings", ticker)
            return result
        try:
            return self.cache.get_or_fetch(self._key("earn", ticker), self._ttl("earnings", 3600), fetch)
        except ProviderUnavailable:
            return {"next_earnings_date": None, "recent_quarters": []}

    def get_peer_valuations(self, ticker):
        """Peer tickers plus their headline valuation multiples, so the thesis
        can compare rather than assert 'cheap' or 'expensive' in isolation."""
        def fetch():
            try:
                peers = self.finnhub.get_peers(ticker)
            except ProviderUnavailable:
                return []
            out = []
            for peer in peers:
                try:
                    f = self.get_fundamentals(peer)
                except ProviderUnavailable:
                    continue
                out.append({
                    "ticker": peer,
                    "forward_pe": (f.get("forward_pe") or {}).get("value"),
                    "trailing_pe": (f.get("trailing_pe") or {}).get("value"),
                    "price_to_sales": (f.get("price_to_sales") or {}).get("value"),
                    "profit_margin": (f.get("profit_margin") or {}).get("value"),
                    "revenue_growth_yoy": (f.get("revenue_growth_yoy") or {}).get("value"),
                })
            return out
        try:
            return self.cache.get_or_fetch(
                self._key("peers", ticker), self._ttl("fundamentals", 3600), fetch)
        except ProviderUnavailable:
            return []

    def get_economic_calendar(self):
        """Upcoming macro events: FRED scheduled releases + FOMC meetings.
        Returns [] when FRED isn't configured — callers fall back to the
        hand-maintained macro_events list in config.yaml."""
        def fetch():
            events = []
            for getter in (self.fred.get_economic_calendar, self.fred.get_fomc_dates):
                try:
                    events += getter()
                except ProviderUnavailable:
                    continue
            return sorted(events, key=lambda e: e["date"])
        try:
            return self.cache.get_or_fetch(
                "econ_calendar", self._ttl("macro", 21600), fetch)
        except Exception:
            return []

    def get_recommendations(self, ticker):
        def fetch():
            return self.finnhub.get_recommendations(ticker)
        try:
            return self.cache.get_or_fetch(
                self._key("recs", ticker), self._ttl("fundamentals", 3600), fetch)
        except ProviderUnavailable:
            return None

    # ---- macro (FRED -> yfinance) ----

    def get_macro(self, sector=None):
        """Macro for every configured region, not just the US.

        US series come from FRED; UK from the Bank of England; euro area from
        the ECB; anything else from the World Bank. Each block degrades on its
        own so one unreachable source doesn't blank the rest.
        """
        def fetch():
            series, providers = {}, []
            if "US" in self.macro_regions:
                try:
                    result, name = self._first([self.fred, self.yf], "get_macro")
                    series.update(result)
                    providers.append(name)
                except ProviderUnavailable:
                    pass
            non_us = [r for r in self.macro_regions if r != "US"]
            if non_us:
                try:
                    series.update(self.global_macro.get_macro(regions=non_us))
                    providers.append("global-macro")
                    self._record("global-macro")
                except ProviderUnavailable as exc:
                    self._record("global-macro", exc)
            return {"series": series, "provider": "+".join(providers) or None,
                    "regions": list(self.macro_regions)}

        try:
            macro = dict(self.cache.get_or_fetch("macro", self._ttl("macro", 21600), fetch))
        except ProviderUnavailable:
            macro = {"series": {}, "provider": None, "regions": list(self.macro_regions)}

        etf = SECTOR_ETF.get(sector)
        tape = {}
        for label, symbol in (("spy", "SPY"),) + ((("sector_etf", etf),) if etf else ()):
            try:
                df = self.get_prices(symbol, period="3mo")
                closes = df["Close"]
                tape[f"{label}_symbol"] = symbol
                if len(closes) > 5:
                    tape[f"{label}_5d_return_pct"] = round((closes.iloc[-1] / closes.iloc[-6] - 1) * 100, 2)
                if len(closes) > 21:
                    tape[f"{label}_1mo_return_pct"] = round((closes.iloc[-1] / closes.iloc[-22] - 1) * 100, 2)
            except ProviderUnavailable:
                pass
        macro["tape"] = tape
        return macro

    # ---- status for the dashboard pills ----

    def provider_status(self):
        """Configuration AND observed health. 'active' means configured *and*
        not currently failing — during an outage a provider reports 'degraded'
        rather than claiming to work."""
        def entry(name, role, configured, missing_note):
            health = self.health.get(name.lower(), {})
            failing = bool(health.get("last_error"))
            if not configured:
                state, note = "off", missing_note
            elif failing:
                state, note = "degraded", f"last request failed: {health['last_error']}"
            elif health.get("ok"):
                state, note = "active", "working (verified by real requests)"
            else:
                state, note = "ready", "configured; not exercised yet this session"
            return {"name": name, "role": role, "state": state,
                    "active": state in ("active", "ready"), "note": note,
                    "last_error_at": health.get("last_error_at")}

        return [
            entry("yfinance", "prices / fundamentals / FX / search", True, ""),
            entry("FRED", "US macro: rates, inflation, unemployment",
                  self.fred.is_available(), "add FRED_API_KEY to .env (free)"),
            entry("Finnhub", "news + earnings calendar",
                  self.finnhub.is_available(), "add FINNHUB_API_KEY to .env (free)"),
            entry("global-macro", "UK (BoE) · euro area (ECB) · world (World Bank)",
                  True, ""),
        ]
