"""When each market is open, and therefore what "current" means right now.

This module exists because of a specific, repeated misreading of the dashboard.
On a Monday morning the book showed Friday's closing prices, and Friday's close
IS the current price — there has been no trading since. But the dashboard
displayed the number with no date beside it, so a correct figure was
indistinguishable from a frozen one, and the natural conclusion was that the
feed had broken.

A price is stale when the market has traded since it was taken. It is NOT stale
merely because it is old: a Friday close read on Sunday is the freshest price
that exists. Answering "is this stale" therefore needs the trading calendar, not
a timestamp comparison, and that is all this module does.

Deliberately dependency-free. `pandas_market_calendars` knows every half-day and
exchange holiday, but it is a heavy dependency for a question that only needs to
be roughly right: the cost of missing a holiday here is one spurious "market
open" label, not a wrong price. Holidays that matter are listed below and the
list is honest about being partial.
"""
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

# Regular trading hours in each venue's own local time. Extended hours are
# deliberately excluded: the daily bars this project uses are built from RTH
# (`useRTH=True` in the IBKR provider), so a price taken at 08:00 ET does not
# correspond to any bar the strategies ever see.
VENUES = {
    "US": {
        "tz": "America/New_York",
        "open": time(9, 30),
        "close": time(16, 0),
        "label": "US equities",
    },
    "LSE": {
        "tz": "Europe/London",
        "open": time(8, 0),
        "close": time(16, 30),
        "label": "London",
    },
}

# Partial, and says so. Only the closures that fall inside a normal trading week
# are listed — a market shut on a Saturday changes nothing. Missing an entry
# means one day is labelled "open" when it was not; it never affects a price.
HOLIDAYS_2026 = {
    "US": {
        "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25",
        "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
    },
    "LSE": {
        "2026-01-01", "2026-04-03", "2026-04-06", "2026-05-04", "2026-05-25",
        "2026-08-31", "2026-12-25", "2026-12-28",
    },
}


def _venue(venue):
    if venue not in VENUES:
        raise ValueError(f"unknown venue {venue!r}; known: {sorted(VENUES)}")
    return VENUES[venue]


def _is_trading_day(day, venue):
    """Weekday and not a listed holiday."""
    if day.weekday() >= 5:
        return False
    return day.strftime("%Y-%m-%d") not in HOLIDAYS_2026.get(venue, set())


def _local_now(venue, now=None):
    spec = _venue(venue)
    now = now or datetime.now(timezone.utc)
    return now.astimezone(ZoneInfo(spec["tz"]))


def is_open(venue, now=None):
    """Is this venue trading at this instant?"""
    spec = _venue(venue)
    local = _local_now(venue, now)
    if not _is_trading_day(local.date(), venue):
        return False
    return spec["open"] <= local.time() < spec["close"]


def last_session_date(venue, now=None):
    """The date of the most recent CLOSED session — the newest daily bar that
    can possibly exist.

    Today counts only once today's closing bell has passed. Before that, the
    freshest complete bar is the previous trading day's, which is exactly the
    case that made a working dashboard look broken.
    """
    spec = _venue(venue)
    local = _local_now(venue, now)
    day = local.date()
    if not (_is_trading_day(day, venue) and local.time() >= spec["close"]):
        day -= timedelta(days=1)
        while not _is_trading_day(day, venue):
            day -= timedelta(days=1)
    return day.strftime("%Y-%m-%d")


def next_open(venue, now=None):
    """When this venue next opens, as a UTC datetime. None if open right now."""
    spec = _venue(venue)
    if is_open(venue, now):
        return None
    tz = ZoneInfo(spec["tz"])
    local = _local_now(venue, now)
    day = local.date()
    if local.time() >= spec["open"]:
        day += timedelta(days=1)
    while not _is_trading_day(day, venue):
        day += timedelta(days=1)
    return datetime.combine(day, spec["open"], tzinfo=tz).astimezone(timezone.utc)


def venue_for(ticker):
    """Which venue's calendar governs this instrument.

    Coarse on purpose. Futures trade nearly around the clock and FX genuinely
    does, so neither is a good fit for a session calendar; both are mapped to
    the US calendar because the daily bars this project consumes are stamped on
    US session boundaries regardless.
    """
    symbol = str(ticker).upper()
    if symbol.endswith(".L"):
        return "LSE"
    return "US"


def bar_status(ticker, bar_date, now=None):
    """Is this instrument's newest bar as fresh as it can be?

    `bar_date` is the date stamped on the last daily bar ('YYYY-MM-DD').
    Returns the judgement plus the material for a human-readable label, so the
    dashboard never has to recompute the calendar itself.
    """
    venue = venue_for(ticker)
    expected = last_session_date(venue, now)
    bar_date = str(bar_date)[:10] if bar_date else None
    open_now = is_open(venue, now)

    if bar_date is None:
        return {"venue": venue, "bar_date": None, "expected_date": expected,
                "current": False, "sessions_behind": None, "market_open": open_now,
                "label": "no price"}

    # Counting calendar days would call a normal Monday "3 days stale". Only
    # sessions the market actually held are counted.
    behind = 0
    if bar_date < expected:
        probe = datetime.strptime(expected, "%Y-%m-%d").date()
        cutoff = datetime.strptime(bar_date, "%Y-%m-%d").date()
        while probe > cutoff and behind < 400:
            if _is_trading_day(probe, venue):
                behind += 1
            probe -= timedelta(days=1)

    current = behind == 0
    if current and open_now:
        label = "live — market open"
    elif current:
        label = f"current — {venue} closed, last close {bar_date}"
    elif behind == 1:
        label = f"1 session behind (shows {bar_date}, latest is {expected})"
    else:
        label = f"{behind} sessions behind (shows {bar_date}, latest is {expected})"

    return {"venue": venue, "bar_date": bar_date, "expected_date": expected,
            "current": current, "sessions_behind": behind,
            "market_open": open_now, "label": label}


def summary(now=None):
    """Status of every venue, for the dashboard header."""
    out = {}
    for venue, spec in VENUES.items():
        opens = next_open(venue, now)
        out[venue] = {
            "label": spec["label"],
            "open": is_open(venue, now),
            "last_session": last_session_date(venue, now),
            "next_open": opens.isoformat(timespec="minutes") if opens else None,
        }
    return out
