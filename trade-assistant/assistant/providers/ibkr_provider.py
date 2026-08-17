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
# How many consecutive client ids to try before giving up. Covers the dashboard,
# a CLI session and a few background jobs holding the feed at once.
CLIENT_ID_ATTEMPTS = 8


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
        """Is IBKR actually reachable — not merely switched on in config.

        This used to return `self.enabled`, and that single line is the origin
        of the project's worst failure mode. The flag says what the user WANTS;
        it says nothing about whether Gateway is running. With the flag on and
        Gateway down, `price_source()` reported "ibkr", the dashboard displayed
        the licensed-feed badge, and every price silently came from the yfinance
        fallback instead. The badge asserting the data is licensed is exactly
        the badge that must never be printed on a guess.

        The probe is cached because it opens a real socket, and `is_available()`
        sits on the hot path — the router calls it once per instrument. A failed
        probe is retried sooner than a successful one is re-verified: an outage
        that has ended should be picked up quickly, while a working connection
        does not need re-proving every few seconds.
        """
        if not self.enabled:
            return False
        return self.probe()["reachable"]

    # Cached across instances: the router builds a new provider per request, and
    # a per-instance cache would probe on every one of them.
    _probe_cache = {}
    PROBE_TTL_OK = 60.0
    PROBE_TTL_FAIL = 15.0

    def probe(self, force=False):
        """Open a real connection and report what came back.

        Returns a dict rather than a bool so the dashboard can show WHY the feed
        is down. "Gateway is not running" and "logged in but no market-data
        subscription" need completely different actions from the user, and
        collapsing both into a red dot is how a fixable problem goes unfixed.
        """
        import time as _time

        key = (self.host, self.port, self.client_id)
        cached = self._probe_cache.get(key)
        if cached and not force:
            ttl = self.PROBE_TTL_OK if cached["reachable"] else self.PROBE_TTL_FAIL
            if _time.monotonic() - cached["checked_monotonic"] < ttl:
                return cached

        from datetime import datetime, timezone
        result = {"reachable": False, "error": None, "accounts": [],
                  "server_time": None,
                  "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                  "checked_monotonic": _time.monotonic(),
                  "host": self.host, "port": self.port}
        if not self.enabled:
            result["error"] = "switched off in config (providers.ibkr.enabled)"
            self._probe_cache[key] = result
            return result

        ib = None
        try:
            ib = self._connect()
            result["reachable"] = True
            try:
                result["accounts"] = list(ib.managedAccounts() or [])
                server_time = ib.reqCurrentTime()
                result["server_time"] = (server_time.isoformat()
                                         if hasattr(server_time, "isoformat")
                                         else str(server_time))
            except Exception:
                # Connected but the detail calls failed. Still reachable — the
                # socket is what determines whether prices can flow.
                pass
        except ProviderUnavailable as exc:
            result["error"] = str(exc)
        except Exception as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            if ib is not None:
                try:
                    ib.disconnect()
                except Exception:
                    pass

        self._probe_cache[key] = result
        return result

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

        # IB allows many simultaneous API clients but insists each hold a
        # DISTINCT client id, and it refuses a duplicate by going silent rather
        # than by answering — so a collision surfaces as a connect timeout that
        # is indistinguishable from Gateway being down. With a single fixed id
        # in config, the dashboard and a CLI session could never both use the
        # licensed feed: whichever started second reported "IBKR unreachable"
        # and quietly served yfinance. Found exactly that way, by a running
        # rebalance locking the dashboard out of its own data source.
        #
        # The configured id is still tried first, so a single-process setup
        # behaves as it always did and the number in config.yaml keeps meaning
        # what it says.
        last_error = None
        for offset in range(CLIENT_ID_ATTEMPTS):
            ib = IB()
            try:
                ib.connect(self.host, self.port, clientId=self.client_id + offset,
                           timeout=CONNECT_TIMEOUT, readonly=True)
                return ib
            except Exception as exc:
                last_error = exc
                try:
                    ib.disconnect()
                except Exception:
                    pass

        raise ProviderUnavailable(
            f"{SOURCE}: cannot reach IB Gateway at {self.host}:{self.port} "
            f"({type(last_error).__name__}: {last_error}). Tried client ids "
            f"{self.client_id}-{self.client_id + CLIENT_ID_ATTEMPTS - 1}. Is it "
            f"running, logged in, and is the API enabled in its settings?")

    # -- live-ish intraday prices ------------------------------------------

    def intraday_marks(self, tickers, bar_size="5 mins"):
        """Latest intraday price per ticker, over ONE held-open connection.

        This exists because the book marks to DAILY bars, and today's daily bar
        does not exist until today's closing bell. Watching a book during the
        session therefore showed yesterday's close on every row, unmoving, for
        the entire trading day — the position had not frozen, it was simply
        being priced by a bar that had not been written yet.

        Real-time quotes are not available here: reqMktData returns NaN on this
        account for both live and delayed modes, because it carries no
        streaming market-data subscription. Intraday HISTORICAL bars sit under
        different entitlements and do work, arriving roughly 10-15 minutes
        behind. That delay is stated everywhere this data is shown rather than
        smoothed over — a number presented as live when it is a quarter-hour
        old is the same class of lie this whole provenance effort exists to
        stop.

        Returns {ticker: {price, bar_time, bar_size}}, omitting anything that
        did not answer. One connection for the batch: the handshake dominates,
        exactly as it does for the daily universe fetch.
        """
        out = {}
        ib = self._connect()
        try:
            for ticker in dict.fromkeys(tickers):
                try:
                    contract = self._contract_for(ticker)
                    if str(ticker).upper().endswith("=F"):
                        contract = self._resolve_future(ib, contract) or contract
                    bars = ib.reqHistoricalData(
                        contract, endDateTime="", durationStr="1 D",
                        barSizeSetting=bar_size, whatToShow="TRADES",
                        useRTH=True, formatDate=1)
                    if not bars:
                        continue
                    last = bars[-1]
                    out[ticker] = {
                        "price": float(last.close),
                        "bar_time": str(last.date),
                        "bar_size": bar_size,
                        "source": SOURCE,
                    }
                except Exception:
                    # One instrument failing must not cost the whole batch; the
                    # caller reports coverage from what came back.
                    continue
        finally:
            ib.disconnect()
        return out

    # IB caps how far back small bars go. Asking for more than these returns an
    # error rather than a truncated series, so the caller has to know the limit
    # before it asks — and the limit is what bounds any intraday study.
    INTRADAY_MAX_DURATION = {
        "1 min": "5 D", "5 mins": "1 M", "15 mins": "2 M",
        "30 mins": "3 M", "1 hour": "6 M", "2 hours": "1 Y",
    }

    def intraday_history(self, ticker, bar_size="5 mins", duration=None):
        """Intraday OHLCV for research — NOT for live trading on this account.

        The distinction matters and is easy to lose. Historical intraday bars
        are available here; a live intraday QUOTE is not, because the account
        carries no streaming subscription and reqMktData returns NaN. So these
        bars can answer "did an edge exist at this horizon", which is a
        question about the past, and cannot support "trade this now", which
        needs a price that is current to the second rather than 15 minutes old.

        Research first, subscription second, is the only sane order: a feed
        bought before the edge is demonstrated is a bet on a hypothesis nobody
        has tested.
        """
        import pandas as pd

        bar_size = bar_size if bar_size in self.INTRADAY_MAX_DURATION else "5 mins"
        duration = duration or self.INTRADAY_MAX_DURATION[bar_size]

        ib = self._connect()
        try:
            contract = self._contract_for(ticker)
            if str(ticker).upper().endswith("=F"):
                contract = self._resolve_future(ib, contract) or contract
            bars = ib.reqHistoricalData(
                contract, endDateTime="", durationStr=duration,
                barSizeSetting=bar_size, whatToShow="TRADES", useRTH=True,
                formatDate=1)
            if not bars:
                raise ProviderUnavailable(
                    f"{SOURCE}: no {bar_size} bars for {ticker} over {duration}")
            frame = pd.DataFrame([{
                "Open": b.open, "High": b.high, "Low": b.low,
                "Close": b.close, "Volume": b.volume} for b in bars],
                index=pd.to_datetime([b.date for b in bars]))
            return frame.dropna(subset=["Close"])
        finally:
            ib.disconnect()

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
                # An LSE line may exist under IB's dotted spelling. Only tried
                # after the plain form fails, so the common path costs nothing.
                for variant in self._symbol_variants(ticker):
                    probe = self._contract_for(ticker)
                    probe.symbol = variant
                    bars = ib.reqHistoricalData(
                        probe, endDateTime="", durationStr=self._duration(period),
                        barSizeSetting="1 day", whatToShow="TRADES", useRTH=True,
                        formatDate=1)
                    if bars:
                        break
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

    # Yahoo names a CME currency future by its floor code ("6E"); IB names the
    # same contract by the currency ("EUR") and only puts the floor code in the
    # LOCAL symbol, so IB's own reply reads "6EU6". Nothing in the failure hints
    # at this — IB returns zero contracts for "6E" exactly as it would for a
    # symbol that does not exist, so the four most liquid FX futures in the
    # world looked to this project like instruments the account cannot trade.
    #
    # This is worth separating from the FX story it was tangled up in. The
    # account has no spot-FX (IDEALPRO) permission and genuinely cannot price
    # EURUSD=X. It CAN trade these, and they are the same currency exposure in
    # a different wrapper — so the gap that looked like "buy a subscription"
    # was, for four of the twelve instruments, a typo-sized mapping bug.
    FUTURES_SYMBOL = {"6E": "EUR", "6J": "JPY", "6B": "GBP", "6A": "AUD"}

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
            return Future(symbol=cls.FUTURES_SYMBOL.get(root, root),
                          exchange=cls.FUTURES_EXCHANGE.get(root, "CME"),
                          currency="USD" if root not in ("ESTX50", "DAX") else "EUR",
                          includeExpired=False)
        for suffix, (exchange, currency) in cls.EXCHANGE_SUFFIX.items():
            if symbol.endswith(suffix):
                return Stock(symbol[:-len(suffix)], exchange, currency)
        return Stock(symbol.replace("-", " "), "SMART", "USD")

    @classmethod
    def _symbol_variants(cls, ticker):
        """Alternative IB symbols to try when the obvious one matches nothing.

        London is the case that matters. IB writes some LSE lines with a
        TRAILING DOT — BP plc is `BP.`, not `BP` — and a share-class letter
        moves inside the dot, so Yahoo's `BT-A.L` is IB's `BT.A`. Stripping
        Yahoo's `.L` therefore yields a symbol that resolves to zero contracts
        for a large, obviously tradable company.

        This was worth a general rule rather than a lookup entry. BP was found
        by hand, but the same convention hides an unknown number of other LSE
        names, and each one silently drops out of the tradable universe — the
        universe quietly shrinks and nothing reports that it did.
        """
        symbol = str(ticker).upper()
        if not symbol.endswith(".L"):
            return []
        root = symbol[:-2]
        variants = []
        if "-" in root:
            # BT-A -> BT.A (class letter after the dot).
            head, _, tail = root.partition("-")
            variants.append(f"{head}.{tail}")
        variants.append(f"{root}.")
        return variants

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
