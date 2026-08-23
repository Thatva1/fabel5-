"""Fetching bars and driving the intraday book. The live loop.

TWO JOBS, ON TWO CLOCKS, AND THEY MUST NOT BE THE SAME JOB

  * `prepare()` runs ONCE before the open. It narrows the 1,568-instrument
    universe to a candidate pool using data already on disk, spends about four
    minutes of IB requests measuring those candidates' real intraday spreads,
    and writes the working set the day will trade. Expensive, and it has all
    morning to be expensive in.

  * `tick()` runs every bar. It fetches bars for the working set plus anything
    currently held, and hands them to the book. It has to finish well inside
    one bar or the book is trading history, so it must never do the work
    `prepare` does.

Collapsing these into one function is the obvious mistake and it is fatal in a
quiet way: the loop starts taking longer than its own bar, every signal arrives
one bar late, and nothing in the output says so — the trades still look like
trades.

WHAT IT COSTS, MEASURED RATHER THAN ASSUMED

IB serves intraday bars at roughly 1.5 seconds each over a held-open
connection. A 60-instrument working set is therefore about 90 seconds, which
fits inside a five-minute bar with room to spare, and a 150-name pool is about
four minutes, which is fine once a day. Those numbers are why the working set
is 60 and not 600, and `tick` reports how long it actually took so the margin
can be watched rather than trusted.

THE DELAY, ONCE MORE

These are historical bars. This account has no streaming subscription, so they
arrive roughly 10-15 minutes late, and a five-minute rule priced on a
fifteen-minute-old bar is not that rule — both its entries and its exits land
somewhere it never chose. Everything here runs and records; none of it is a
live trading result. The staleness is measured on every tick and reported,
because the one thing worse than a delay is a delay nobody is counting.
"""
import time
from datetime import datetime, timezone

from ..core import market_clock
from ..providers import bulk
from . import intraday_book, intraday_watchlist
from .book import Book


def _now():
    return datetime.now(timezone.utc)


def _bar_size(config):
    return ((config or {}).get("intraday") or {}).get("bar_size", "5 mins")


def prepare(config, *, progress_cb=None, now=None):
    """Choose today's working set. Run once, before the open.

    Stage one is free — it reads the daily universe history already cached for
    the daily rebalance. Stage two spends real IB requests, but only on the
    shortlist stage one produced.
    """
    moment = now or _now()
    out = {"at": moment.isoformat(timespec="seconds"), "notes": []}

    frames = _cached_daily_history()
    if not frames:
        out["error"] = ("No daily universe history on disk, so there is nothing "
                        "to select from. Run `python run.py paper` once to build "
                        "it, or `run.py intraday-prepare --refresh`.")
        return out

    pool = intraday_watchlist.build(frames, config)
    out["pool"] = {k: pool[k] for k in ("size", "considered", "qualified", "rejected")}
    if not pool["symbols"]:
        out["error"] = "Nothing survived the daily pre-filter."
        return out

    started = time.monotonic()
    bars, missing, pacing = bulk.intraday_history_ibkr(
        pool["symbols"], bar_size=_bar_size(config), config=config,
        progress_cb=progress_cb)
    out["fetch_seconds"] = round(time.monotonic() - started, 1)
    out["fetched"] = len(bars)
    out["missing"] = len(missing)
    out["pacing"] = pacing

    working = intraday_watchlist.refine(pool, bars, config)
    intraday_watchlist.save(working)
    out["working_set"] = working
    out["summary"] = intraday_watchlist.describe(working)

    if not working["symbols"]:
        out["notes"].append(
            "The pool survived the daily filter and then nothing survived the "
            "intraday measurement. That is a finding about this universe's "
            "spreads, not a bug — no rule can pay a toll larger than the "
            "distance the instrument travels.")
    # A working set whose weakest member barely clears the threshold is one bad
    # week from empty, and it is better to know that in the morning.
    elif len(working["symbols"]) < intraday_watchlist.settings(config)["size"] / 2:
        out["notes"].append(
            f"Only {len(working['symbols'])} instruments qualified against a "
            f"target of {intraday_watchlist.settings(config)['size']}. The loop "
            f"will run, but on a thin selection.")
    return out


def _cached_daily_history():
    """The daily frames the daily rebalance already fetched. Zero IB requests."""
    import pickle

    from . import session as daily_session

    try:
        with open(daily_session.HISTORY_CACHE_PATH, "rb") as handle:
            payload = pickle.load(handle)
    except (OSError, ValueError, EOFError, pickle.UnpicklingError, AttributeError):
        return {}
    return daily_session._in_current_units(payload)


def tick(config, *, now=None, book_path=None, book=None, force=False):
    """One pass of the live loop: fetch, then mark, exit and open.

    Refuses to fetch anything when no market is open. An intraday book with
    nothing to trade should cost nothing to leave running — a loop that walks
    the broker every five minutes all night is how a rate limit gets spent on
    bars that cannot be traded.
    """
    moment = now or _now()
    out = {"at": moment.isoformat(timespec="seconds"), "notes": []}

    book = book if book is not None else Book.load(book_path or intraday_book.BOOK_PATH)
    held = [p["ticker"] for p in book.positions]

    venues = {market_clock.venue_for(t) for t in held} or {"US"}
    if not force and not any(market_clock.is_open(v, moment)
                             for v in venues | {"US", "LSE"}):
        out["phase"] = {"phase": market_clock.PHASE_CLOSED,
                        "explanation": "No market open. Nothing fetched."}
        out["skipped"] = True
        return out

    working = intraday_watchlist.load()
    if working is None:
        out["error"] = ("No working set for today. Run `prepare` before the "
                        "open — the loop will not choose instruments from a "
                        "stale liquidity profile.")
        return out

    # Held positions are ALWAYS fetched, even if they have dropped out of the
    # working set since the morning. A position that cannot be priced cannot be
    # managed, and the flat-by-close guarantee depends on being able to price
    # every single thing that is open.
    symbols = list(dict.fromkeys(list(held) + list(working["symbols"])))

    started = time.monotonic()
    frames, missing, pacing = bulk.intraday_history_ibkr(
        symbols, bar_size=_bar_size(config), config=config)
    out["fetch_seconds"] = round(time.monotonic() - started, 1)
    out["fetched"] = len(frames)
    out["missing"] = len(missing)
    out["pacing"] = pacing

    unpriced = [t for t in held if t not in frames]
    if unpriced:
        out["notes"].append(
            f"HELD BUT UNPRICED: {', '.join(unpriced)}. These cannot be managed "
            f"this pass, and the flat-by-close guarantee does not cover them "
            f"until they price again.")

    out["staleness_minutes"] = _staleness(frames, moment)
    if out["staleness_minutes"] is not None and out["staleness_minutes"] > 25:
        out["notes"].append(
            f"Bars are {out['staleness_minutes']:.0f} minutes old — further "
            f"behind than this account's usual 10-15. Signals computed from "
            f"them are not describing the current market.")

    prev_closes = _previous_closes(frames, moment)
    result = intraday_book.run(config, frames, prev_closes=prev_closes, now=moment,
                               book_path=book_path, book=book)

    # MERGE, do not overwrite. A plain update() replaced this pass's notes with
    # the book's, and those notes are the only place two things are ever said:
    # that a held position could not be priced, and that the bars are older
    # than this account's usual delay. Both would have vanished silently, which
    # is precisely the class of failure they exist to report.
    notes = list(out["notes"]) + list(result.get("notes") or [])
    out.update(result)
    out["notes"] = notes

    # The loop must finish inside its own bar or every signal it produces is one
    # bar late — and nothing else in the output would say so.
    budget = _bar_minutes(config) * 60 * 0.6
    if out["fetch_seconds"] > budget:
        out["notes"].append(
            f"This pass took {out['fetch_seconds']:.0f}s against a "
            f"{_bar_minutes(config)}-minute bar. The working set is too large "
            f"for the connection: signals are arriving late. Reduce "
            f"intraday.watchlist.size.")
    return out


def _previous_closes(frames, moment):
    """Each instrument's PREVIOUS SESSION close.

    Two of the four intraday rules are defined against this — Gao et al.'s
    momentum measures the first half hour FROM the previous close, and a gap is
    by definition the distance from it — so getting it wrong does not weaken
    them, it makes them measure something else entirely.

    The first draft took the frame's first close, which at a month of
    five-minute bars is a price from four weeks ago. The rules would have
    reported enormous "gaps" every single morning and taken a position on every
    one of them.
    """
    today = moment.date()
    out = {}
    for ticker, frame in (frames or {}).items():
        if frame is None or not len(frame):
            continue
        earlier = frame[frame.index.date < today]
        if len(earlier):
            # The last bar of the most recent session that is not today's.
            last_day = earlier.index.date.max()
            out[ticker] = float(earlier[earlier.index.date == last_day]["Close"].iloc[-1])
    return out


def _bar_minutes(config):
    return {"1 min": 1, "5 mins": 5, "15 mins": 15, "30 mins": 30,
            "1 hour": 60}.get(_bar_size(config), 5)


def _staleness(frames, moment):
    """How old the newest bar is, in minutes. The number nobody was counting."""
    newest = None
    for frame in frames.values():
        if frame is None or not len(frame):
            continue
        stamp = frame.index[-1].to_pydatetime()
        if stamp.tzinfo is None:
            # IB hands back exchange-local time; the US venue is the right
            # default here because the working set is overwhelmingly US.
            from zoneinfo import ZoneInfo
            stamp = stamp.replace(tzinfo=ZoneInfo(market_clock.VENUES["US"]["tz"]))
        stamp = stamp.astimezone(timezone.utc)
        newest = stamp if newest is None else max(newest, stamp)
    if newest is None:
        return None
    return round((moment - newest).total_seconds() / 60, 1)
