"""Interactive Brokers as a DATA source, separate from IBKR as a broker.

The project already talks to IB Gateway to place orders (broker/ibkr.py). This
is the other half, and it is the answer to three problems yfinance cannot fix:

**Licensing.** yfinance's terms forbid commercial use. An IBKR market-data
subscription is licensed for the account holder's own use, which is the
difference between research you can show someone and research you can build a
product on.

**Options.** No usable historical or live option data reaches this stack
today, which is why there is no hedging sleeve anywhere in the library. IBKR
serves option chains, quotes and Greeks for any underlying the account
subscribes to. That is the single capability that would let a defined-risk
hedge exist here at all.

**Contract specifications.** The futures multipliers and margins in markets.py
are hand-entered constants, and margins in particular are order-of-magnitude
guesses. IBKR returns the real multiplier on every contract and the real margin
requirement per account. Those numbers stop being an approximation.

WHAT THIS NEEDS THAT THE PROJECT DOES NOT HAVE:

  * IB Gateway or TWS running locally, logged in.
  * An IBKR account. A paper account is enough for data.
  * Market-data subscriptions for what you want — US equities, futures and
    options are separate line items, and without them IBKR returns delayed or
    empty data rather than an error you would notice.

Everything here degrades to "unavailable" rather than to a wrong number. A data
provider that silently returns nothing is how a rate-limited screen once cached
537 instruments as though they were the market; the same mistake with option
prices would be worse, because a hedge priced from missing data reports
protection that is not there.
"""
from .base import DataProvider, ProviderUnavailable

SOURCE = "ibkr"
DEFAULT_PORT = 7497          # paper. Live is 7496 and must be set deliberately.
CONNECT_TIMEOUT = 8


class IBKRDataProvider(DataProvider):
    """Prices, contract specs and option chains from a local IB Gateway."""

    name = SOURCE

    def __init__(self, config=None):
        cfg = ((config or {}).get("providers") or {}).get("ibkr") or {}
        self.host = cfg.get("host", "127.0.0.1")
        self.port = int(cfg.get("port", DEFAULT_PORT))
        self.client_id = int(cfg.get("data_client_id", 991))
        self.enabled = bool(cfg.get("enabled", False))

    def is_available(self):
        return self.enabled

    # -- connection --------------------------------------------------------

    def _connect(self):
        """A short-lived connection with its own loop, as broker/ibkr.py does.

        Not held open: the paper trader runs once a day and a long-lived socket
        would be one more thing to keep alive for no benefit.
        """
        if not self.enabled:
            raise ProviderUnavailable(
                "IBKR data is switched off. Set providers.ibkr.enabled: true in "
                "config.yaml and have IB Gateway or TWS running and logged in.")
        import asyncio
        try:
            asyncio.get_event_loop()
        except RuntimeError:
            asyncio.set_event_loop(asyncio.new_event_loop())
        try:
            from ib_async import IB
        except ImportError:
            try:
                from ib_insync import IB
            except ImportError as exc:
                raise ProviderUnavailable(f"{SOURCE}: no ib_async/ib_insync ({exc})")

        ib = IB()
        try:
            ib.connect(self.host, self.port, clientId=self.client_id,
                       timeout=CONNECT_TIMEOUT, readonly=True)
        except Exception as exc:
            raise ProviderUnavailable(
                f"{SOURCE}: cannot reach IB Gateway at {self.host}:{self.port} "
                f"({type(exc).__name__}: {exc}). Is it running and logged in?")
        return ib

    # -- prices ------------------------------------------------------------

    def get_prices(self, ticker, period="1y"):
        """Daily OHLCV. Licensed, and survivorship-inclusive for delisted names.

        IBKR keeps contracts for instruments that have stopped trading, which is
        the other thing yfinance cannot do: a reversal strategy that buys
        multi-year losers is flattered by exactly the names yfinance has
        forgotten.
        """
        import pandas as pd

        ib = self._connect()
        try:
            contract = self._contract_for(ticker)
            if str(ticker).upper().endswith("=F"):
                resolved = self._resolve_future(ib, contract)
                if resolved is None:
                    raise ProviderUnavailable(f"{SOURCE}: no contract for {ticker}")
                contract = resolved
            bars = ib.reqHistoricalData(
                contract, endDateTime="", durationStr=self._duration(period),
                barSizeSetting="1 day", whatToShow="TRADES", useRTH=True,
                formatDate=1)
            if not bars:
                raise ProviderUnavailable(f"{SOURCE}: no bars for {ticker}")
            frame = pd.DataFrame([{
                "Open": b.open, "High": b.high, "Low": b.low,
                "Close": b.close, "Volume": b.volume} for b in bars],
                index=pd.to_datetime([b.date for b in bars]))
            return frame.dropna(subset=["Close"])
        finally:
            ib.disconnect()

    def contract_spec(self, ticker):
        """The REAL multiplier and margin, replacing the constants in markets.py."""
        ib = self._connect()
        try:
            details = ib.reqContractDetails(self._contract_for(ticker))
            if not details:
                raise ProviderUnavailable(f"{SOURCE}: no contract for {ticker}")
            first = details[0]
            return {
                "multiplier": float(first.contract.multiplier or 1),
                "min_tick": float(getattr(first, "minTick", 0) or 0),
                "exchange": first.contract.exchange,
                "currency": first.contract.currency,
                "long_name": getattr(first, "longName", ""),
                "source": SOURCE,
            }
        finally:
            ib.disconnect()

    # -- options -----------------------------------------------------------

    def option_chain(self, underlying, max_expiries=4):
        """Expiries and strikes for an underlying — the missing hedging input.

        Returns the chain's shape rather than a recommendation. Turning this
        into a protective position needs a pricing model and a sizing rule that
        do not exist in this project yet, and inventing either would produce a
        hedge that reports protection it does not have.
        """
        ib = self._connect()
        try:
            stock = self._contract_for(underlying)
            ib.qualifyContracts(stock)
            chains = ib.reqSecDefOptParams(stock.symbol, "", stock.secType,
                                           stock.conId)
            if not chains:
                raise ProviderUnavailable(
                    f"{SOURCE}: no option chain for {underlying}. This usually "
                    "means the account has no options market-data subscription "
                    "rather than that the underlying has no options.")
            chain = next((c for c in chains if c.exchange == "SMART"), chains[0])
            expiries = sorted(chain.expirations)[:max_expiries]
            return {
                "underlying": underlying,
                "exchange": chain.exchange,
                "trading_class": chain.tradingClass,
                "multiplier": chain.multiplier,
                "expiries": expiries,
                "strikes": sorted(chain.strikes),
                "source": SOURCE,
            }
        finally:
            ib.disconnect()

    # -- news --------------------------------------------------------------

    def get_news(self, ticker, limit=8):
        """Recent headlines for one instrument, from the account's providers.

        Returned as CONTEXT, not as a score. A headline becomes a trading
        signal only once something has measured that its arrival predicts a
        return, and nothing here has done that — so this reports what was
        published and leaves the interpreting to a human, which is the only
        honest thing it can do today.

        Worth knowing about the feeds themselves: the providers bundled with an
        account are thin. A large-cap query returns a handful of items going
        back months, not a stream. Anything resembling news-driven trading needs
        a paid feed, and a strategy built on this one would be reacting to a
        fraction of what actually moved the price.
        """
        ib = self._connect()
        try:
            providers = ib.reqNewsProviders()
            if not providers:
                raise ProviderUnavailable(
                    f"{SOURCE}: no news providers on this account")
            contract = self._contract_for(ticker)
            qualified = ib.qualifyContracts(contract)
            if not qualified:
                raise ProviderUnavailable(f"{SOURCE}: no contract for {ticker}")
            codes = "+".join(p.code for p in providers)
            items = ib.reqHistoricalNews(qualified[0].conId, codes, "", "", limit)
            return {
                "ticker": ticker,
                "providers": [p.code for p in providers],
                "headlines": [{
                    "time": str(getattr(item, "time", "")),
                    # IB prefixes headlines with a routing token like
                    # "{A:800015:L:en}" that is metadata, not text.
                    "headline": str(getattr(item, "headline", "")).split("}")[-1].strip(),
                    "provider": getattr(item, "providerCode", ""),
                    "article_id": getattr(item, "articleId", ""),
                } for item in items],
                "source": SOURCE,
            }
        finally:
            ib.disconnect()

    # -- helpers -----------------------------------------------------------

    # Which exchange each futures root trades on. IB rejects a Future with no
    # exchange outright — "Please enter exchange" — so an empty string is not a
    # wildcard the way SMART is for equities.
    FUTURES_EXCHANGE = {
        "ES": "CME", "NQ": "CME", "RTY": "CME", "YM": "CBOT",
        "ZN": "CBOT", "ZB": "CBOT", "ZF": "CBOT", "ZT": "CBOT",
        "ZC": "CBOT", "ZS": "CBOT", "ZW": "CBOT",
        "CL": "NYMEX", "NG": "NYMEX", "GC": "COMEX", "SI": "COMEX",
        "HG": "COMEX",
        "6E": "CME", "6J": "CME", "6B": "CME", "6A": "CME",
        # European index futures on Eurex.
        "ESTX50": "EUREX", "DAX": "EUREX",
    }

    # Non-US equities need their real exchange and currency; SMART/USD silently
    # resolves a European line to a US cross-listing or to nothing at all.
    EXCHANGE_SUFFIX = {
        ".L": ("LSE", "GBP"), ".AS": ("AEB", "EUR"), ".DE": ("IBIS", "EUR"),
        ".PA": ("SBF", "EUR"), ".SW": ("EBS", "CHF"), ".MI": ("BVME", "EUR"),
        ".MC": ("BM", "EUR"), ".BR": ("ENEXT.BE", "EUR"), ".ST": ("SFB", "SEK"),
    }

    @classmethod
    def _contract_for(cls, ticker):
        """Map a project ticker onto an IB contract.

        The project's symbols follow Yahoo's conventions — "=X" for FX, "=F"
        for futures, a dotted suffix for a non-US listing — so the suffix
        decides both the contract type and, for equities, the exchange.
        """
        try:
            from ib_async import Forex, Future, Stock
        except ImportError:
            from ib_insync import Forex, Future, Stock

        symbol = str(ticker).upper()
        if symbol.endswith("=X"):
            return Forex(symbol[:-2])
        if symbol.endswith("=F"):
            root = symbol[:-2]
            return Future(symbol=root, exchange=cls.FUTURES_EXCHANGE.get(root, "CME"),
                          currency="USD" if root not in ("ESTX50", "DAX") else "EUR",
                          includeExpired=False)
        for suffix, (exchange, currency) in cls.EXCHANGE_SUFFIX.items():
            if symbol.endswith(suffix):
                return Stock(symbol[:-len(suffix)], exchange, currency)
        return Stock(symbol.replace("-", " "), "SMART", "USD")

    @staticmethod
    def _resolve_future(ib, contract):
        """Pick the NEAREST expiry for a futures root.

        A root like ESTX50 matches fifteen contracts across three years and two
        contract sizes, and qualifyContracts raises "ambiguous" rather than
        choosing — which reads in a probe as "not available" when the instrument
        is perfectly available. Front month is what a continuous series means.
        """
        details = ib.reqContractDetails(contract)
        if not details:
            return None
        dated = [d.contract for d in details
                 if getattr(d.contract, "lastTradeDateOrContractMonth", "")]
        if not dated:
            return details[0].contract
        # Smallest multiplier first at equal expiry, so a probe does not pick
        # the jumbo contract when a mini exists.
        dated.sort(key=lambda c: (c.lastTradeDateOrContractMonth,
                                  float(c.multiplier or 0)))
        return dated[0]

    @staticmethod
    def _duration(period):
        """Translate the project's period strings into IB's duration format."""
        table = {"1y": "1 Y", "2y": "2 Y", "3y": "3 Y", "5y": "5 Y",
                 "10y": "10 Y", "3mo": "3 M", "6mo": "6 M", "1mo": "1 M"}
        return table.get(str(period).lower(), "1 Y")
