"""Closing a position because YOU said so.

Every other exit in this book is a rule firing: price touched the stop, price
touched the target, or the holding clock ran out. Those are decided in
`session.run`, they only happen when a session runs, and until now they were
the ONLY way a position could leave the book. A position you had changed your
mind about sat there until one of three numbers was crossed.

That is a real gap rather than a missing convenience. The rules cannot know
that a company has just been bid for, that a feed has gone wrong, or that you
want to be flat before a weekend — and a book you cannot get out of is not a
book you can run money in.

WHAT THIS IS CAREFUL ABOUT

  * PRICE PROVENANCE. A manual exit still has to fill at a real number. The
    live intraday mark is preferred and the session's last close is the
    fallback, and which one was used is recorded on the trade and returned to
    the caller. An exit price whose origin is unstated is how a book quietly
    books profits at prices that never existed.

  * COSTS ARE STILL PAID. Slippage against you and the venue's commission,
    exactly as `session.run` charges them. A manual exit that skipped costs
    would make "close it by hand" look better than the rule that would have
    closed it, which is precisely the comparison this book exists to make.

  * THE DAY'S RECORD ACCUMULATES. `record_day` is last-write-wins, so writing
    only this exit's realised P&L would erase whatever the session banked
    earlier the same day. The existing row is read and added to.

  * IT IS RECORDED AS MANUAL. `exit_reason` is "manual", so the journal's
    by-exit breakdown can separate what the rules achieved from what you
    overrode. A discretionary exit filed as a target is a corrupted track
    record.

Nothing here touches a broker. This closes a position in the PAPER book, which
is the same thing every other exit in this project does.
"""
from datetime import datetime, timezone

from ..backtest import simulator
from . import live


def _now():
    """The clock, behind a seam. The stale-close guard turns on what day it is,
    so a test has to be able to say."""
    return datetime.now(timezone.utc)


def _today():
    return _now().strftime("%Y-%m-%d")


def _slipped(price, direction, bps):
    """Exit slippage, always against you: sell lower, cover higher."""
    drift = price * float(bps) / 10_000
    return price - drift if direction == "long" else price + drift


class RefusedClose(Exception):
    """A close that would book fictional P&L, stopped before it did."""


def _guard(book, targets, config, moment=None):
    """Refuse a close that cannot be priced honestly. Raises, or returns a note.

    THIS EXISTS BECAUSE IT HAPPENED. TWICE.

    Ninety-two positions were closed against marks three days old, on a Sunday
    with every market shut, booking a realised loss of £206,947 that no market
    produced — the entire figure was the gap between a stale Wednesday mark and
    the arithmetic. The first time it was a test reaching the real book; the
    second time I could not identify the caller from the logs at all, and that
    is exactly the point.

    An operation this destructive must not depend on every caller being
    careful. `close_positions` is reachable from an HTTP endpoint, from the
    CLI, from a test, and from any future scheduler — and the damage is silent,
    because a closed trade with a plausible price looks like a trade.

    So the refusal lives HERE, at the operation, and it triggers on the
    combination that can only be a mistake: closing several positions, with no
    market open, at prices that are not today's. One position closed by hand at
    a stale mark is a judgement call and is allowed with a warning. Ninety-two
    is not a judgement call.

    `config['paper']['allow_stale_close']` overrides it for someone who
    genuinely means it.
    """
    from ..core import market_clock

    if bool(((config or {}).get("paper") or {}).get("allow_stale_close")):
        return "Stale-close guard overridden by config."

    moment = moment or _now()
    open_venues = [v for v in ("US", "LSE") if market_clock.is_open(v, moment)]
    if open_venues:
        return None

    today = moment.strftime("%Y-%m-%d")
    stale = [p["ticker"] for p in targets
             if str(p.get("bar_date") or "")[:10] != today]
    if not stale:
        return None

    if len(targets) < BULK_CLOSE_THRESHOLD:
        return (f"No market is open and {len(stale)} of these are marked at an "
                f"older close. The P&L booked is against that mark, not against "
                f"a price anyone traded today.")

    raise RefusedClose(
        f"Refusing to close {len(targets)} positions with every market shut and "
        f"{len(stale)} of them marked at an older session. The realised P&L "
        f"would be the gap between a stale mark and today's arithmetic, not "
        f"anything the market did — which is how this book lost a fictional "
        f"£206,947 on a Sunday. Wait for an open market, close fewer than "
        f"{BULK_CLOSE_THRESHOLD} by hand, or set paper.allow_stale_close.")


# Above this many positions at once, a stale close stops being a judgement call
# and becomes an accident. One position closed at an old mark is a decision;
# the whole book is not.
BULK_CLOSE_THRESHOLD = 5


def close_positions(book, tickers, config, *, force_refresh=True, now=None):
    """Close the named positions now. Returns a report; the caller saves.

    `tickers` is matched case-insensitively. An unknown ticker is reported
    rather than ignored — a "close everything" that silently missed a holding
    is the worst possible outcome of a button labelled that way.

    Raises RefusedClose when the close cannot be priced honestly; see _guard.
    """
    wanted = {str(t).strip().upper() for t in tickers if str(t).strip()}
    if not wanted:
        return {"closed": [], "missing": [], "realised": 0.0, "costs": 0.0,
                "note": "No positions named."}

    targets = [p for p in list(book.positions) if p["ticker"].upper() in wanted]
    missing = sorted(wanted - {p["ticker"].upper() for p in targets})

    # Before anything is mutated. A guard that fires halfway through has
    # already closed some of them.
    stale_note = _guard(book, targets, config, now) if targets else None

    slippage_bps = float(((config or {}).get("paper") or {}).get("slippage_bps", 5.0))

    # One fetch for the whole batch. Closing forty positions must not open
    # forty broker connections, and the live snapshot already batches.
    marks = {}
    mark_note = None
    if targets:
        marks, mark_note = live.fetch_marks([p["ticker"] for p in targets],
                                            config, force=force_refresh)

    today = _today()
    closed, realised, costs = [], 0.0, 0.0

    for position in targets:
        mark = marks.get(position["ticker"]) or {}
        live_price = mark.get("price")
        if live_price is not None:
            price, source, stamp = float(live_price), "live", mark.get("bar_time")
        else:
            # The session mark is a real traded price, just an older one. Saying
            # so is the whole difference between a fallback and a fabrication.
            price, source, stamp = (float(position["last_price"]),
                                    "session mark", position.get("bar_date"))

        fill = _slipped(price, position.get("direction", "long"), slippage_bps)
        book.close_position(position, fill, today, "manual")

        commission = float(simulator.cost_config_for(position["ticker"], config)
                           .get("commission_per_trade", 0.0))
        book.cash -= commission

        position["exit_price_source"] = source
        position["exit_price_at"] = stamp
        position["exit_note"] = "Closed by hand from the dashboard."

        realised += position.get("pnl") or 0.0
        costs += commission
        closed.append({
            "ticker": position["ticker"],
            "direction": position.get("direction", "long"),
            "strategy": position.get("strategy"),
            "shares": position["shares"],
            "exit_price": round(fill, 4),
            "price_source": source,
            "priced_at": stamp,
            "pnl": position.get("pnl"),
            "r": position.get("r_multiple"),
            "commission": commission,
        })

    if closed:
        _record(book, today, closed, realised, costs)

    return {
        "closed": closed,
        "missing": missing,
        "stale_warning": stale_note,
        "realised": round(realised, 2),
        "costs": round(costs, 2),
        "equity": round(book.equity(), 2),
        "open_positions": len(book.positions),
        "note": mark_note,
    }


def _record(book, today, closed, realised, costs):
    """Fold this exit into the day's record instead of overwriting it.

    `record_day` keeps one row per date and the last write wins, so a manual
    exit that reported only its own P&L would erase the realised figure the
    morning's session had already banked. The existing row is read back and
    added to, which is also why `closed` counts accumulate here.
    """
    previous = book.daily[-1] if book.daily and book.daily[-1]["date"] == today else {}
    names = ", ".join(c["ticker"] for c in closed)
    note = f"closed by hand — {len(closed)} position(s): {names}"

    book.mark(today, note, rebalanced=False, opened=0, closed=len(closed))
    book.record_day(
        today,
        realised=float(previous.get("realised") or 0.0) + realised,
        costs=float(previous.get("costs") or 0.0) + costs,
        opened=int(previous.get("opened") or 0),
        closed=int(previous.get("closed") or 0) + len(closed),
        price_source=previous.get("price_source"),
        note=note,
    )


def close_all(book, config, **kwargs):
    """Go flat. Every open position, closed now."""
    return close_positions(book, [p["ticker"] for p in book.positions],
                           config, **kwargs)
