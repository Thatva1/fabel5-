"""The paper portfolio's state — cash, open positions, and the equity curve.

This is the piece the project did not have. PaperBroker refuses to place orders
by design and reads its positions from config.yaml; the backtest replays history
and forgets everything. Neither can answer "what would this system have done
since I turned it on", which needs state that survives between runs.

Stored as JSON rather than in the journal database on purpose. The journal
records IDEAS awaiting a human decision, and its stats measure how a person
traded. This measures how the RULES traded with nobody intervening. Mixing the
two would make both unreadable — you could no longer tell whether a good month
came from the strategy or from the trades you chose to skip.

Nothing here can place a real order. There is no broker seam in this module at
all, which is the strongest guarantee available: not a flag that could be turned
on, but an absence of the code that would do it.
"""
import json
import os
from datetime import datetime, timezone

from ..core.config import DATA_DIR

BOOK_PATH = os.path.join(DATA_DIR, "paper_book.json")
SCHEMA = 1


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Book:
    """Cash, positions and history for one paper portfolio."""

    def __init__(self, state=None):
        state = state or {}
        self.schema = state.get("schema", SCHEMA)
        self.started_at = state.get("started_at")
        self.starting_equity = state.get("starting_equity")
        self.cash = state.get("cash")
        self.base_currency = state.get("base_currency", "USD")
        self.positions = list(state.get("positions", []))
        self.closed = list(state.get("closed", []))
        self.curve = list(state.get("curve", []))          # [{date, equity, exposure_pct}]
        self.sessions = list(state.get("sessions", []))    # [{date, ran_at, note}]
        # Tracked separately from `sessions` on purpose. Deriving "have I
        # rebalanced this month" from the session log couples the trading
        # calendar to anything else that happens to write a log line — and it
        # already broke once: the note recording the opening FX conversion was
        # appended before the check ran, so a brand-new book decided it had
        # already rebalanced and opened nothing on its first day.
        self.last_rebalance = state.get("last_rebalance")

    # -- lifecycle ---------------------------------------------------------

    @classmethod
    def load(cls, path=BOOK_PATH):
        try:
            with open(path) as handle:
                return cls(json.load(handle))
        except (OSError, ValueError):
            return cls()

    def save(self, path=BOOK_PATH):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # Written to a temporary file and moved into place: a run interrupted
        # mid-write would otherwise leave a truncated book, and the next session
        # would silently start again from zero with a fresh balance.
        tmp = path + ".tmp"
        with open(tmp, "w") as handle:
            json.dump(self.to_dict(), handle, indent=2)
        os.replace(tmp, path)

    def to_dict(self):
        return {
            "schema": SCHEMA,
            "started_at": self.started_at,
            "starting_equity": self.starting_equity,
            "cash": self.cash,
            "base_currency": self.base_currency,
            "positions": self.positions,
            "closed": self.closed,
            "curve": self.curve,
            "sessions": self.sessions,
            "last_rebalance": self.last_rebalance,
        }

    @property
    def started(self):
        return self.started_at is not None

    def start(self, equity, base_currency, date):
        self.started_at = _now()
        self.starting_equity = float(equity)
        self.cash = float(equity)
        self.base_currency = base_currency
        self.curve = [{"date": date, "equity": round(float(equity), 2),
                       "exposure_pct": 0.0, "positions": 0}]

    # -- positions ---------------------------------------------------------

    def is_held(self, ticker):
        return any(p["ticker"] == ticker for p in self.positions)

    def open_position(self, *, ticker, direction, shares, price, stop, target,
                      strategy, regime, date, headline="", meta=None):
        """Open a position, charging the CAPITAL it consumes, not its notional.

        A share is paid for in full; a futures contract is carried on margin.
        Charging notional for a future overstates the cash it uses by ten to
        fifty times and silently caps the book at one or two contracts, which is
        why every futures figure this project produced before now was
        unreadable. Multiplier and margin are recorded on the position so profit
        and loss can run on the notional while capital runs on the margin.
        """
        from ..markets import capital_required, contract_multiplier, initial_margin

        multiplier = contract_multiplier(ticker)
        margin = initial_margin(ticker)
        committed = capital_required(ticker, price, shares)
        self.cash -= committed
        self.positions.append({
            "ticker": ticker, "direction": direction, "shares": float(shares),
            "entry_price": float(price), "entry_date": date,
            "stop": float(stop), "target": float(target),
            "strategy": strategy, "regime": regime, "headline": headline,
            "bars_held": 0, "last_price": float(price),
            "multiplier": multiplier,
            "margin_per_unit": margin,
            "committed": round(committed, 2),
            "meta": dict(meta or {}),
        })
        return committed

    def close_position(self, position, price, date, reason):
        self.positions = [p for p in self.positions if p is not position]
        shares, entry = position["shares"], position["entry_price"]
        multiplier = float(position.get("multiplier", 1.0) or 1.0)
        direction = 1.0 if position.get("direction", "long") == "long" else -1.0
        # Profit runs on the notional; the capital committed comes back.
        pnl = (price - entry) * multiplier * shares * direction
        self.cash += float(position.get("committed", shares * entry)) + pnl
        risk = abs(entry - position["stop"]) * multiplier * shares
        proceeds = pnl
        position.update({
            "exit_price": float(price), "exit_date": date, "exit_reason": reason,
            "pnl": round(pnl, 2),
            "r_multiple": round(pnl / risk, 2) if risk else None,
        })
        self.closed.append(position)
        return proceeds

    # -- valuation ---------------------------------------------------------

    def position_value(self, position):
        """What this position is worth to equity: capital in, plus profit so far."""
        multiplier = float(position.get("multiplier", 1.0) or 1.0)
        direction = 1.0 if position.get("direction", "long") == "long" else -1.0
        committed = float(position.get("committed",
                                       position["shares"] * position["entry_price"]))
        unrealised = ((position["last_price"] - position["entry_price"])
                      * multiplier * position["shares"] * direction)
        return committed + unrealised

    def market_value(self):
        """Capital tied up in open positions, marked to market."""
        return sum(self.position_value(p) for p in self.positions)

    def gross_exposure(self):
        """NOTIONAL at risk, which for a futures book is the number that matters.

        A margin figure says what the positions cost to hold; this says what
        they are exposed to. The two are the same for shares and differ by the
        contract's leverage for futures, so an exposure cap read off the wrong
        one is not a cap at all.
        """
        return sum(abs(p["last_price"] * float(p.get("multiplier", 1.0) or 1.0)
                       * p["shares"]) for p in self.positions)

    def equity(self):
        return (self.cash or 0.0) + self.market_value()

    def mark(self, date, note=None):
        """Append today's equity point. One row per calendar date, last wins."""
        equity = self.equity()
        exposure = (self.market_value() / equity * 100) if equity else 0.0
        row = {"date": date, "equity": round(equity, 2),
               "exposure_pct": round(exposure, 1), "positions": len(self.positions)}
        if self.curve and self.curve[-1]["date"] == date:
            self.curve[-1] = row
        else:
            self.curve.append(row)
        if note:
            self.sessions.append({"date": date, "ran_at": _now(), "note": note})
        return row

    # -- reporting ---------------------------------------------------------

    def summary(self):
        equity = self.equity()
        start = self.starting_equity or 0.0
        peak, worst = None, 0.0
        for row in self.curve:
            peak = row["equity"] if peak is None else max(peak, row["equity"])
            if peak:
                worst = max(worst, (peak - row["equity"]) / peak * 100)
        wins = [c for c in self.closed if (c.get("pnl") or 0) > 0]
        return {
            "started_at": self.started_at,
            "days": len(self.curve),
            "starting_equity": round(start, 2),
            "equity": round(equity, 2),
            "return_pct": round((equity / start - 1) * 100, 2) if start else None,
            "cash": round(self.cash or 0.0, 2),
            "exposure_pct": round(self.market_value() / equity * 100, 1) if equity else 0.0,
            "open_positions": len(self.positions),
            "closed_trades": len(self.closed),
            "win_rate_pct": round(len(wins) / len(self.closed) * 100, 1) if self.closed else None,
            "max_drawdown_pct": round(worst, 2),
            "base_currency": self.base_currency,
        }
