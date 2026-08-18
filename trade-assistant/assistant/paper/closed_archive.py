"""Closed trades, kept out of the live book.

An open position and a closed one answer different questions. "What am I
holding" is about now and is short; "what did I do" is history and grows
without limit. Keeping both in paper_book.json meant the file the trader reads
to see twenty-odd holdings was mostly a ledger of things that were no longer
holdings, and every load and save carried the whole record.

So closed trades live here instead, in an append-only file, and the book keeps
only a short tail for display. The archive is the source of truth for history:
the trade journal and every performance figure read from it, not from the tail.

APPEND-ONLY ON PURPOSE. A track record that can be rewritten is worth nothing,
and the same argument already applies to archived books. Nothing in this module
edits or deletes an existing line.

Stored as JSON Lines rather than one JSON array: a crash midway through writing
an array leaves a file that will not parse at all, while a truncated last line
costs one trade and is skipped on read.
"""
import hashlib
import json
import os

from ..core.config import DATA_DIR

ARCHIVE_PATH = os.path.join(DATA_DIR, "closed_trades.jsonl")


def _path(path=None):
    """Resolve the archive path AT CALL TIME.

    Taking `path=ARCHIVE_PATH` as a default argument binds the value when the
    module is imported, so reassigning ARCHIVE_PATH afterwards has no effect —
    every caller silently keeps writing to the original file. That makes the
    location impossible to redirect for a test, a second book, or a different
    data directory, and the failure is invisible: the code appears to accept a
    new path and ignores it.
    """
    return path or ARCHIVE_PATH

# How many closed trades the book keeps inline. Enough for the dashboard's
# "recently closed" panel without the file growing forever.
BOOK_TAIL = 20


def trade_id(trade):
    """A stable identity for a closed trade.

    Needed because a trade can sit in the book's tail AND in the archive at the
    same time; without an id the journal would count those twice and report a
    win rate built from duplicates. Derived from the fields fixed at entry and
    exit rather than assigned, so a trade archived before this existed gets the
    same id as it would today.
    """
    if trade.get("trade_id"):
        return trade["trade_id"]
    parts = [str(trade.get(k)) for k in
             ("ticker", "direction", "entry_date", "exit_date",
              "entry_price", "exit_price", "shares", "strategy")]
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:16]


def append(trades, path=None):
    """Append closed trades, skipping any already recorded."""
    path = _path(path)
    trades = [t for t in trades if t]
    if not trades:
        return 0

    known = {t["trade_id"] for t in load(path)}
    written = 0
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a") as handle:
            for trade in trades:
                tid = trade_id(trade)
                if tid in known:
                    continue
                row = dict(trade, trade_id=tid)
                handle.write(json.dumps(row, default=str) + "\n")
                known.add(tid)
                written += 1
    except OSError:
        return written     # losing the archive must never lose the session
    return written


def load(path=None):
    """Every archived trade, oldest first. A damaged line is skipped, not fatal."""
    path = _path(path)
    out = []
    try:
        with open(path) as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                row.setdefault("trade_id", trade_id(row))
                out.append(row)
    except OSError:
        return []
    return out


def merged(book, path=None):
    """Archive plus whatever is still in the book, de-duplicated.

    This is what every performance figure should read. Using the book's tail
    alone silently truncates history the moment the archive is in use, which
    would make a win rate improve simply because old losses scrolled off.
    """
    path = _path(path)
    seen, out = set(), []
    for trade in load(path) + list(book.closed):
        tid = trade_id(trade)
        if tid in seen:
            continue
        seen.add(tid)
        out.append(dict(trade, trade_id=tid))
    out.sort(key=lambda t: (t.get("exit_date") or "", t.get("ticker") or ""))
    return out


def flush(book, path=None, keep=BOOK_TAIL):
    """Move the book's closed trades into the archive, keeping a short tail.

    Called on save. The tail is kept so the dashboard's "recently closed" panel
    needs no extra read, and it is a COPY — every trade in it is already in the
    archive, so trimming the tail can never lose a trade.
    """
    path = _path(path)
    written = append(book.closed, path)
    if len(book.closed) > keep:
        book.closed = book.closed[-keep:]
    return written


def stats(path=None):
    """Totals over the full archive, for a header that must not read the tail."""
    trades = load(_path(path))
    wins = [t for t in trades if (t.get("pnl") or 0) > 0]
    losses = [t for t in trades if (t.get("pnl") or 0) < 0]
    realised = sum((t.get("pnl") or 0) for t in trades)
    return {
        "trades": len(trades),
        "realised_pnl": round(realised, 2),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(len(wins) / len(trades) * 100, 1) if trades else None,
        "first_exit": trades[0].get("exit_date") if trades else None,
        "last_exit": trades[-1].get("exit_date") if trades else None,
    }
