"""Live profit and loss: the book repriced to where the market is NOW.

Separate from the book itself, and it never writes to it. The distinction is
the point:

  * The BOOK is the record of what the rules did. Its marks are daily closes,
    taken at a session, and they are what the strategy's performance is measured
    on. Rewriting them intraday would mean the recorded history changed every
    time somebody opened a browser tab, and a track record that moves when you
    look at it is not a track record.

  * This is a VIEW. It answers "what is this worth right now", which is a
    different question from "what did the rules achieve", and it is the question
    a person staring at an open book during the session is actually asking.

Watching the book during market hours previously showed the previous close on
every row, unmoving, all day — because today's daily bar is not written until
today's closing bell. Nothing was broken; the book was being priced by a bar
that did not exist yet, and had no way to say so.

The prices here run roughly 10-15 minutes behind (see IBKRDataProvider.
intraday_marks — this account has no streaming subscription). Every number this
module produces carries that delay, and the UI states it, because a figure
presented as live when it is a quarter-hour old is the same failure this
project has spent its whole history stamping out.
"""
import time
from datetime import datetime, timezone

# Live marks are cached briefly. Each refresh opens a socket and walks every
# holding, so an auto-refreshing page would otherwise reconnect every few
# seconds to re-read bars that only change every five minutes.
_CACHE = {"at": 0.0, "marks": {}, "key": None}
CACHE_TTL_SECONDS = 45.0


def _cache_key(tickers):
    return tuple(sorted(tickers))


def fetch_marks(tickers, config, force=False):
    """Latest intraday price per ticker, cached. Returns ({}, note) on failure.

    Never raises. A live overlay that cannot load must degrade to showing the
    session marks with an explanation — losing the overlay is a much smaller
    problem than losing the book, and the book is what shares the page.
    """
    tickers = [t for t in dict.fromkeys(tickers)]
    if not tickers:
        return {}, None

    key = _cache_key(tickers)
    if (not force and _CACHE["key"] == key
            and time.monotonic() - _CACHE["at"] < CACHE_TTL_SECONDS):
        return _CACHE["marks"], None

    from ..providers.ibkr_provider import IBKRDataProvider

    provider = IBKRDataProvider(config)
    if not provider.is_available():
        return {}, ("IBKR is not reachable, so live prices are unavailable. The "
                    "figures below are the book's session marks.")
    try:
        marks = provider.intraday_marks(tickers)
    except Exception as exc:
        return {}, (f"Live prices unavailable ({type(exc).__name__}). The figures "
                    f"below are the book's session marks.")

    _CACHE.update({"at": time.monotonic(), "marks": marks, "key": key})
    return marks, None


def _position_live(position, mark):
    """Reprice one position, keeping the session mark alongside the live one.

    Both are carried deliberately. The difference between them IS today's move,
    and collapsing to a single number would hide whether a position is up
    because the strategy worked or because the market opened higher.
    """
    multiplier = float(position.get("multiplier", 1.0) or 1.0)
    direction = 1.0 if position.get("direction", "long") == "long" else -1.0
    shares = float(position["shares"])
    entry = float(position["entry_price"])
    session_price = float(position["last_price"])
    committed = float(position.get("committed", shares * entry))

    live_price = float(mark["price"]) if mark else None
    price = live_price if live_price is not None else session_price

    unrealised = (price - entry) * multiplier * shares * direction
    session_unrealised = (session_price - entry) * multiplier * shares * direction
    today_move = unrealised - session_unrealised

    stop, target = position.get("stop"), position.get("target")
    through_stop = through_target = False
    if stop is not None:
        through_stop = price <= stop if direction > 0 else price >= stop
    if target is not None:
        through_target = price >= target if direction > 0 else price <= target

    return {
        "ticker": position["ticker"],
        "direction": position.get("direction", "long"),
        "strategy": position.get("strategy"),
        "shares": shares,
        "entry_price": round(entry, 4),
        "session_price": round(session_price, 4),
        "live_price": round(live_price, 4) if live_price is not None else None,
        "has_live": live_price is not None,
        "bar_time": (mark or {}).get("bar_time"),
        "move_pct": round((price / entry - 1) * 100 * direction, 2) if entry else 0.0,
        "today_pct": round((price / session_price - 1) * 100 * direction, 2)
        if session_price else 0.0,
        "unrealised": round(unrealised, 2),
        "today_pnl": round(today_move, 2),
        "notional": round(abs(price * multiplier * shares), 2),
        "value": round(committed + unrealised, 2),
        "stop": stop,
        "target": target,
        # Read off the LIVE price, so a stop breached at 14:00 is visible at
        # 14:00 rather than after the close when the daily bar lands.
        "through_stop": bool(through_stop),
        "through_target": bool(through_target),
    }


def snapshot(book, config, force=False):
    """The whole book, repriced. Does not persist anything."""
    if not book.started or not book.positions:
        return {"available": False, "positions": [], "note": None,
                "summary": {"open_positions": 0}}

    marks, note = fetch_marks([p["ticker"] for p in book.positions], config,
                              force=force)
    rows = [_position_live(p, marks.get(p["ticker"])) for p in book.positions]
    rows.sort(key=lambda r: -abs(r["notional"]))

    priced = [r for r in rows if r["has_live"]]
    cash = float(book.cash or 0.0)
    equity = cash + sum(r["value"] for r in rows)
    start = float(book.starting_equity or 0.0)
    session_equity = book.equity()

    breaches = [r for r in rows if r["through_stop"] or r["through_target"]]

    return {
        "available": bool(priced),
        "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "note": note,
        "delay_note": ("Intraday prices from IBKR historical bars — roughly "
                       "10-15 minutes behind. This account has no streaming "
                       "market-data subscription."),
        "positions": rows,
        "priced": len(priced),
        "unpriced": len(rows) - len(priced),
        "breaches": [{"ticker": r["ticker"],
                      "kind": "stop" if r["through_stop"] else "target",
                      "live_price": r["live_price"],
                      "level": r["stop"] if r["through_stop"] else r["target"]}
                     for r in breaches],
        "summary": {
            "open_positions": len(rows),
            "cash": round(cash, 2),
            "equity": round(equity, 2),
            "session_equity": round(session_equity, 2),
            # Today's move is the whole reason to look at this panel.
            "today_pnl": round(equity - session_equity, 2),
            "today_pct": round((equity / session_equity - 1) * 100, 3)
            if session_equity else 0.0,
            "open_pnl": round(sum(r["unrealised"] for r in rows), 2),
            "return_pct": round((equity / start - 1) * 100, 2) if start else None,
            "gross_exposure": round(sum(r["notional"] for r in rows), 2),
            "base_currency": book.base_currency,
        },
    }
