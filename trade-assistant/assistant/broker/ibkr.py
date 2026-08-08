"""IBKRBroker — Layer 2 implementation of the Broker interface.

Talks to a locally running IB Gateway / TWS over its API socket. Global order
routing via SMART + the instrument's currency.

Safety model (do not weaken):
  * Never instantiated unless config execution.enabled is true.
  * place_order() is reachable ONLY from the execution service's confirm step,
    which requires a human-prepared ticket plus typed confirmation.
  * Defaults target the PAPER port (7497). Live requires editing config.yaml.

Uses ib_async when available (Python 3.10+), else ib_insync (same API — ib_async
is the maintained rename of ib_insync). Each call opens a short-lived connection
with its own event loop so it is safe from Flask worker threads.
"""
import asyncio
from contextlib import contextmanager

# The IB libraries patch asyncio at import time and need a loop in the importing
# thread. This module is imported lazily from Flask worker threads, which have
# none, so establish one before the import or it fails with a misleading
# "no current event loop" error.
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

try:
    from ib_async import IB, LimitOrder, Stock, StopOrder
    IB_LIB = "ib_async"
except ImportError:
    from ib_insync import IB, LimitOrder, Stock, StopOrder
    IB_LIB = "ib_insync"

from .base import Broker, ExecutionNotEnabled

CONNECT_TIMEOUT = 6

# IBKR paper accounts are prefixed DU (individual) or DF (advisor); live accounts
# are U / F. The socket port is only a convention and the user can change it, so
# the account id — not the port — is what actually tells us paper from live.
PAPER_ACCOUNT_PREFIXES = ("DU", "DF")


# Yahoo exchange suffixes covering this app's preferred_exchanges list. Anything
# not listed here is left untouched rather than guessed at.
YAHOO_EXCHANGE_SUFFIXES = {
    "L", "IL",                      # London (LSE, international order book)
    "AS", "PA", "BR", "LS",         # Amsterdam, Paris, Brussels, Lisbon
    "DE", "F", "BE", "DU", "HM", "HA", "MU", "SG",   # German venues
    "SW", "VX",                     # Switzerland
    "MI", "MC", "VI",               # Milan, Madrid, Vienna
    "ST", "CO", "OL", "HE",         # Stockholm, Copenhagen, Oslo, Helsinki
    "IR",                           # Dublin
    "TO", "V", "NE", "CN",          # Canada
    "SA", "MX",                     # São Paulo, Mexico
    "T", "HK", "SI", "AX", "NZ",    # Tokyo, Hong Kong, Singapore, Sydney, NZ
    "NS", "BO",                     # India (NSE, BSE)
    "KS", "KQ", "TW", "TWO",        # Korea, Taiwan
    "JO", "TA",                     # Johannesburg, Tel Aviv
    "IS",                           # Istanbul
}


def ib_symbol(ticker):
    """Yahoo-style symbol -> the plain symbol IB expects.

    Yahoo suffixes the exchange ('TSCO.L', 'AIR.PA', 'SAP.DE'); IB takes the
    bare symbol plus a currency and routes with SMART. Passing 'TSCO.L' simply
    fails to resolve, which is how the borrow check came to answer UNKNOWN for
    every non-US name.

    Only known exchange suffixes are stripped. US symbols that legitimately
    contain a dot are share-class markers (BRK.B, BF.A) and must survive
    untouched — guessing from the shape would break them.
    """
    if not ticker or "." not in ticker:
        return ticker
    stem, _, suffix = ticker.rpartition(".")
    if stem and suffix.upper() in YAHOO_EXCHANGE_SUFFIXES:
        return stem
    return ticker


def accounts_are_paper(accounts):
    """True only when EVERY reported account is a paper account.
    Empty/unknown -> False, i.e. assume live and warn loudly (fail safe)."""
    cleaned = [a.strip().upper() for a in (accounts or []) if a and a.strip()]
    if not cleaned:
        return False
    return all(a.startswith(PAPER_ACCOUNT_PREFIXES) for a in cleaned)


class GatewayUnreachable(Exception):
    """IB Gateway / TWS is not running or not accepting API connections."""


class AccountMismatch(Exception):
    """The configured account doesn't match what IB reports, or is ambiguous."""


class IBKRBroker(Broker):
    name = "ibkr"

    def __init__(self, config):
        exec_cfg = config.get("execution", {})
        if not exec_cfg.get("enabled"):
            raise ExecutionNotEnabled(
                "Execution is disabled in config.yaml (execution.enabled: false).")
        self.host = exec_cfg.get("host", "127.0.0.1")
        self.port = int(exec_cfg.get("port", 7497))
        self.client_id = int(exec_cfg.get("client_id", 42))
        self.account = exec_cfg.get("account") or ""
        # Port is only a hint; the real answer comes from the account id once
        # connected. Default to False so anything unverified reads as LIVE.
        self.is_paper = False
        self.port_suggests_paper = self.port in (7497, 4002)

    @contextmanager
    def _session(self):
        """Short-lived IB connection with its own event loop.

        Flask serves each request on a pooled worker thread, and ib_insync
        leaves the thread's asyncio loop in an unusable state after disconnect.
        Reusing it surfaces confusing 'no current event loop' errors instead of
        the real connectivity problem, so every session gets a fresh loop and
        tears it down again.
        """
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        ib = IB()
        try:
            try:
                ib.connect(self.host, self.port, clientId=self.client_id,
                           timeout=CONNECT_TIMEOUT, readonly=False)
            except Exception as exc:
                raise GatewayUnreachable(
                    f"Could not reach IB Gateway/TWS at {self.host}:{self.port} "
                    f"({type(exc).__name__}: {exc}). Is it running with API access "
                    "enabled? (Global Configuration -> API -> Settings -> "
                    "'Enable ActiveX and Socket Clients', matching port.)")
            self._verify_account(ib)
            yield ib
        finally:
            try:
                if ib.isConnected():
                    ib.disconnect()
            finally:
                asyncio.set_event_loop(None)
                loop.close()

    def _verify_account(self, ib):
        """With more than one managed account, IB's summary/positions calls are
        ambiguous — you can end up reading one account and trading another. So
        an explicit, verified account id is required in that case."""
        accounts = [a for a in (ib.managedAccounts() or []) if a]
        if self.account:
            if self.account not in accounts:
                raise AccountMismatch(
                    f"config execution.account is '{self.account}' but IB reports "
                    f"{accounts or 'no accounts'}. Fix config.yaml before trading.")
        elif len(accounts) > 1:
            raise AccountMismatch(
                f"IB reports {len(accounts)} accounts ({', '.join(accounts)}). "
                "Set execution.account in config.yaml to the exact account you want "
                "to use — otherwise balances and orders could target different accounts.")

    def ping(self):
        """Connectivity check for the dashboard. Determines paper-vs-live from
        the account ids and caches it on the instance."""
        with self._session() as ib:
            accounts = ib.managedAccounts()
            self.is_paper = accounts_are_paper(accounts)
            return {"connected": True, "library": IB_LIB, "paper": self.is_paper,
                    "accounts": accounts,
                    "port_mismatch": self.port_suggests_paper and not self.is_paper}

    def get_account(self):
        with self._session() as ib:
            self.is_paper = accounts_are_paper(ib.managedAccounts())
            values = {r.tag: r for r in ib.accountSummary(self.account or "")}
            nlv = values.get("NetLiquidation")
            # None means "IB told us nothing", which is NOT the same as an
            # account worth zero. The caller must refuse rather than substitute
            # an assumed balance — 0.0 here used to be falsy enough to fall
            # through to the config.yaml figure. See audit finding R-2.
            portfolio_value = None
            if nlv is not None:
                try:
                    portfolio_value = float(nlv.value)
                except (TypeError, ValueError):
                    portfolio_value = None
            return {
                "portfolio_value": portfolio_value,
                "base_currency": (nlv.currency if nlv else "USD") or "USD",
                "paper": self.is_paper,
                "source": f"IBKR ({'paper' if self.is_paper else 'LIVE'})",
            }

    def get_positions(self):
        """Current holdings. `shares` keeps IB's sign (negative = short) so
        direction is visible; the risk gate takes abs() for gross exposure.
        `mark_price` is the current market value per share where IB provides it,
        which is what live risk checks should use rather than cost basis."""
        with self._session() as ib:
            out = []
            marks = {}
            try:
                for item in ib.portfolio():
                    if item.position:
                        marks[item.contract.conId] = float(item.marketPrice or 0) or None
            except Exception:
                pass  # marks are an enhancement; cost basis is the fallback

            for pos in ib.positions(self.account or ""):
                contract = pos.contract
                out.append({
                    "ticker": contract.symbol,
                    "shares": float(pos.position),          # signed: negative = short
                    "entry_price": float(pos.avgCost),
                    "mark_price": marks.get(contract.conId),
                    "currency": (contract.currency or "USD").upper(),
                    "sector": None,  # IB doesn't supply sector; gate flags this
                    "source": "IBKR",
                })
            return out

    def is_shortable(self, ticker, currency="USD"):
        """Ask IB how many shares are available to borrow (generic tick 236).

        Read-only and strictly best-effort: this opens a market-data request,
        not an order. Any failure — no subscription, no connection, contract
        not resolvable — returns UNKNOWN rather than a guess, because the risk
        gate must never read "we couldn't check" as "yes, go ahead".
        """
        symbol = ib_symbol(ticker)
        try:
            with self._session() as ib:
                contract = Stock(symbol, "SMART", (currency or "USD").upper())
                qualified = ib.qualifyContracts(contract)
                if not qualified:
                    return {"status": "unknown", "source": "IBKR",
                            "detail": (f"IB could not resolve {symbol} ({currency}) "
                                       "on SMART routing.")}
                ticker_data = ib.reqMktData(qualified[0], "236", False, False)
                ib.sleep(2)
                shares = getattr(ticker_data, "shortableShares", None)
                ib.cancelMktData(qualified[0])

                if shares is None or shares != shares:   # None or NaN
                    return {"status": "unknown", "source": "IBKR",
                            "detail": ("IB returned no borrow data — this usually means "
                                       "no market-data subscription for that exchange.")}
                shares = float(shares)
                if shares >= 1000:
                    return {"status": "yes", "source": "IBKR",
                            "detail": f"IB reports about {shares:,.0f} shares available to borrow."}
                return {"status": "no", "source": "IBKR",
                        "detail": (f"IB reports only {shares:,.0f} shares available to borrow — "
                                   "effectively hard-to-borrow.")}
        except Exception as exc:
            return {"status": "unknown", "source": "IBKR",
                    "detail": f"Borrow check failed ({type(exc).__name__}: {exc})."}

    def place_order(self, order_intent):
        """Submit the entry as a LIMIT order with an ATTACHED protective STOP
        (a bracket). Only the execution service's confirm step may call this,
        and only with a human-confirmed ticket.

        The stop is a real broker-side order, submitted as one bracket so it is
        live the moment the entry fills — the displayed max loss is only
        meaningful if the broker is actually holding the stop.
        """
        if order_intent.order_type != "limit" or not order_intent.limit_price:
            raise ValueError("Only limit orders with an explicit price are supported.")
        if not order_intent.stop_price:
            raise ValueError(
                "Refusing to place an unprotected order: no stop price on the ticket.")

        action = "BUY" if order_intent.side == "buy" else "SELL"
        exit_action = "SELL" if action == "BUY" else "BUY"
        if action == "BUY" and order_intent.stop_price >= order_intent.limit_price:
            raise ValueError("For a long, the stop must be BELOW the entry price.")
        if action == "SELL" and order_intent.stop_price <= order_intent.limit_price:
            raise ValueError("For a short, the stop must be ABOVE the entry price.")

        with self._session() as ib:
            self.is_paper = accounts_are_paper(ib.managedAccounts())
            symbol = ib_symbol(order_intent.ticker)
            contract = Stock(symbol, "SMART", order_intent.currency)
            qualified = ib.qualifyContracts(contract)
            if not qualified:
                raise ValueError(
                    f"IB could not resolve {symbol} ({order_intent.currency}) "
                    "on SMART routing — check the symbol/currency.")
            contract = qualified[0]

            parent = LimitOrder(action, order_intent.quantity, order_intent.limit_price)
            parent.orderId = ib.client.getReqId()
            parent.transmit = False          # hold until the stop is attached
            stop = StopOrder(exit_action, order_intent.quantity, order_intent.stop_price)
            stop.orderId = ib.client.getReqId()
            stop.parentId = parent.orderId
            stop.transmit = True             # transmits the whole bracket
            for order in (parent, stop):
                if self.account:
                    order.account = self.account

            parent_trade = ib.placeOrder(contract, parent)
            stop_trade = ib.placeOrder(contract, stop)
            ib.sleep(1.5)  # let the initial statuses round-trip

            return {
                "ib_order_id": parent_trade.order.orderId,
                "stop_order_id": stop_trade.order.orderId,
                "status": parent_trade.orderStatus.status,
                "stop_status": stop_trade.orderStatus.status,
                "paper": self.is_paper,
                "detail": (f"{action} {order_intent.quantity} {order_intent.ticker} "
                           f"@ limit {order_intent.limit_price} {order_intent.currency}, "
                           f"protective stop {exit_action} @ {order_intent.stop_price}"),
            }
