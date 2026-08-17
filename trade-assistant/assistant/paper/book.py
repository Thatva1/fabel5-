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
        # The DATE above cannot express a sub-daily schedule — it cannot say
        # which half of a day a rebalance belonged to. Stored alongside rather
        # than replacing it, so a book written before sub-daily schedules
        # existed still loads and still answers the monthly question correctly.
        self.last_rebalance_at = state.get("last_rebalance_at")
        # When the book was last marked to market. Distinct from the newest
        # curve date, which is a trading date: this is wall-clock, and the gap
        # between the two is exactly what the dashboard needs to show. A book
        # last run on Saturday and read on Monday is not wrong, but it has not
        # seen Monday, and nothing on screen said which.
        self.as_of = state.get("as_of")
        # One row per trading day: what the day earned or lost, split into the
        # part that was realised by closing trades and the part that is still
        # open. The equity curve alone cannot answer "what did we make today" —
        # it holds a level, and a level does not say whether a rise came from a
        # winner being banked or from an open position drifting up.
        self.daily = list(state.get("daily", []))

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
            "last_rebalance_at": self.last_rebalance_at,
            "as_of": self.as_of,
            "daily": self.daily,
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
                      strategy, regime, date, headline="", meta=None,
                      price_source=None, bar_date=None):
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
            # Which feed produced this price and which session it belongs to.
            # Stored per POSITION, not per book, because the two can differ: a
            # book whose universe came from IBKR can still hold one instrument
            # the licensed feed cannot price, and that is precisely the position
            # that went unnoticed. A book-level source label would have called
            # that position licensed too.
            "price_source": price_source,
            "bar_date": bar_date,
            "priced_at": _now(),
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

    def mark(self, date, note=None, **fields):
        """Append today's equity point. One row per calendar date, last wins.

        `fields` carries the structured record of what the session did —
        price_source, opened, closed, universe, rebalanced. The dashboard has
        always READ those keys and they were never written: only {date, ran_at,
        note} was stored, so every session row rendered blank, including the one
        field that matters most for trusting a number, which feed priced it. The
        router computed price_source on every run and threw it away.
        """
        equity = self.equity()
        exposure = (self.market_value() / equity * 100) if equity else 0.0
        row = {"date": date, "equity": round(equity, 2),
               "exposure_pct": round(exposure, 1), "positions": len(self.positions)}
        if self.curve and self.curve[-1]["date"] == date:
            self.curve[-1] = row
        else:
            self.curve.append(row)
        if note or fields:
            entry = {"date": date, "ran_at": _now(), "note": note}
            entry.update({k: v for k, v in fields.items() if v is not None})
            self.sessions.append(entry)
        self.as_of = _now()
        return row

    def record_day(self, date, *, realised=0.0, costs=0.0, opened=0, closed=0,
                   price_source=None, note=None):
        """Close the day's books: what was made, and where it came from.

        Split into realised and unrealised deliberately. A day can be up on
        paper while every trade actually closed that day lost money, and a
        single equity number hides which — so the two halves are recorded
        separately and the split is what makes the record worth reading later.

        One row per date, last write wins, so re-running a session does not
        double-count the day.
        """
        equity = self.equity()
        previous = (self.daily[-1]["equity"] if self.daily
                    else (self.starting_equity or equity))
        change = equity - previous
        row = {
            "date": date,
            "equity": round(equity, 2),
            "previous_equity": round(previous, 2),
            "change": round(change, 2),
            "change_pct": round(change / previous * 100, 3) if previous else 0.0,
            # Realised is cash banked by trades that CLOSED today. Unrealised is
            # everything else the day did — the drift on positions still open.
            "realised": round(realised, 2),
            "costs": round(costs, 2),
            "unrealised_change": round(change - realised + costs, 2),
            "open_positions": len(self.positions),
            "opened": opened,
            "closed": closed,
            "cash": round(self.cash or 0.0, 2),
            "exposure_pct": round(self.market_value() / equity * 100, 1) if equity else 0.0,
            "price_source": price_source,
            "note": note,
            "recorded_at": _now(),
        }
        if self.daily and self.daily[-1]["date"] == date:
            # Preserve the day's original opening level across a re-run;
            # recomputing it from the last row would measure the day against
            # itself and report a change of zero.
            row["previous_equity"] = self.daily[-1]["previous_equity"]
            row["change"] = round(equity - row["previous_equity"], 2)
            row["change_pct"] = (round(row["change"] / row["previous_equity"] * 100, 3)
                                 if row["previous_equity"] else 0.0)
            row["unrealised_change"] = round(row["change"] - realised + costs, 2)
            self.daily[-1] = row
        else:
            self.daily.append(row)
        return row

    # -- provenance --------------------------------------------------------

    def price_sources(self):
        """Every distinct feed that priced something currently held.

        More than one entry means the book is a mixture, and a mixed book
        cannot honestly carry a single "licensed" badge.
        """
        return sorted({p.get("price_source") or "unknown" for p in self.positions})

    def staleness(self, now=None):
        """Per-position freshness against each instrument's own trading calendar.

        Not a timestamp comparison. A London line and a US line close at
        different times, and on a Monday morning both correctly show Friday —
        so the only meaningful question is whether the market has traded since
        the price was taken.
        """
        from ..core import market_clock

        rows, worst = [], 0
        for position in self.positions:
            status = market_clock.bar_status(position["ticker"],
                                             position.get("bar_date"), now)
            rows.append({"ticker": position["ticker"],
                         "price_source": position.get("price_source"),
                         **status})
            worst = max(worst, status["sessions_behind"] or 0)
        return {"positions": rows, "worst_sessions_behind": worst,
                "all_current": worst == 0}

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
