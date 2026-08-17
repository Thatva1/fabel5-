"""What the licensed feed can actually price, and what it silently cannot.

The question this answers is "what can we trade" — and until now nothing could
answer it. The watchlist was a list of things the user WANTED to trade, and the
system discovered the difference between that and what IBKR would serve one
instrument at a time, mid-session, by falling back to yfinance without saying
so. A book was opened on GBPUSD=X, the largest position in it, on an instrument
the account has no market-data permission for.

That failure is quiet by construction: a missing subscription does not raise an
error that stops anything. IBKR returns no bars, the router shrugs and asks
yfinance, and a position appears priced by an unlicensed feed with nothing on
screen to distinguish it from the rest. The universe shrinks and the shrinkage
is invisible.

So coverage is measured deliberately, written down, and shown. An instrument is
tradable here only if the LICENSED feed can price it. Everything else is
reported with the subscription that would unlock it, because "you need the
IDEALPRO FX bundle" is an action the user can take, while "no data" is not.
"""
import json
import os
import time
from datetime import datetime, timezone

from ..core.config import DATA_DIR
from .base import ProviderUnavailable

COVERAGE_PATH = os.path.join(DATA_DIR, "ibkr_coverage.json")

# How a probe failure maps onto the thing the user would have to buy or fix.
# The reason strings come from IBKR's own error text, which names the venue.
SUBSCRIPTION_HINTS = [
    ("IDEALPRO", "IDEALPRO spot FX",
     "IBKR market data → 'IDEALPRO FX' (spot currency pairs)"),
    ("CASH", "IDEALPRO spot FX",
     "IBKR market data → 'IDEALPRO FX' (spot currency pairs)"),
    ("no contract for", "contract not found",
     "This symbol does not resolve to an IBKR contract at all — usually a "
     "symbol-mapping problem rather than a subscription."),
    ("CME", "CME Group futures",
     "IBKR market data → 'CME (NYMEX/COMEX/CBOT) Top of Book'"),
    ("NYMEX", "CME Group futures",
     "IBKR market data → 'CME (NYMEX/COMEX/CBOT) Top of Book'"),
    ("LSE", "London Stock Exchange",
     "IBKR market data → 'UK LSE Equities (Level 1)'"),
]

GENERIC_HINT = ("market data permission",
                "IBKR Account Management → Settings → Market Data Subscriptions. "
                "Without a subscription IBKR returns empty bars rather than an error.")


def _classify(ticker, error):
    """Turn a probe failure into something the user can act on."""
    text = str(error or "")
    for needle, bundle, action in SUBSCRIPTION_HINTS:
        if needle.lower() in text.lower():
            return {"bundle": bundle, "action": action}
    # FX futures roots fail as "no contract" but are really a CME entitlement.
    if str(ticker).upper().endswith("=X"):
        return {"bundle": "IDEALPRO spot FX",
                "action": SUBSCRIPTION_HINTS[0][2]}
    return {"bundle": GENERIC_HINT[0], "action": GENERIC_HINT[1]}


def probe_symbols(symbols, config, progress_cb=None, period="5d"):
    """Ask IBKR for each symbol and record what came back.

    Slow and deliberately so — one historical-data request per instrument, about
    a second each. This is not on any hot path; it runs when the user asks
    "what can I trade" and the answer is then cached. Doing it lazily per-scan
    is what allowed the gap to stay hidden.
    """
    from .ibkr_provider import IBKRDataProvider

    provider = IBKRDataProvider(config)
    probe = provider.probe(force=True)
    if not probe["reachable"]:
        raise ProviderUnavailable(
            f"IBKR is not reachable at {probe['host']}:{probe['port']} — cannot "
            f"measure coverage. ({probe['error']})")

    tradable, unavailable = {}, {}
    symbols = list(dict.fromkeys(symbols))
    for index, ticker in enumerate(symbols, 1):
        try:
            frame = provider.get_prices(ticker, period=period)
            tradable[ticker] = {
                "last_bar": str(frame.index[-1])[:10],
                "last_close": round(float(frame["Close"].iloc[-1]), 6),
                "bars": int(len(frame)),
            }
        except Exception as exc:
            unavailable[ticker] = {"error": str(exc)[:160],
                                   **_classify(ticker, exc)}
        if progress_cb:
            progress_cb(index, len(symbols), ticker)

    return {
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "ibkr",
        "accounts": probe["accounts"],
        "tradable": tradable,
        "unavailable": unavailable,
        "counts": {"tradable": len(tradable), "unavailable": len(unavailable),
                   "total": len(symbols)},
    }


def save(report, path=COVERAGE_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as handle:
        json.dump(report, handle, indent=2)
    os.replace(tmp, path)
    return path


def load(path=COVERAGE_PATH):
    try:
        with open(path) as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def age_hours(report):
    if not report or not report.get("checked_at"):
        return None
    try:
        checked = datetime.fromisoformat(report["checked_at"])
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - checked).total_seconds() / 3600


def tradable_symbols(symbols, config, max_age_hours=24, path=COVERAGE_PATH):
    """Filter a symbol list down to what the licensed feed can price.

    Falls back to returning the list UNCHANGED when no coverage report exists,
    rather than to returning nothing. An empty universe would stop the book
    dead; an unfiltered one merely restores the previous behaviour, and the
    session report says which of the two happened.
    """
    report = load(path)
    if report is None:
        return list(symbols), {"filtered": False,
                               "reason": "no coverage report yet — run coverage first"}
    age = age_hours(report)
    if age is not None and max_age_hours and age > max_age_hours:
        return list(symbols), {"filtered": False,
                               "reason": f"coverage report is {age:.0f}h old (limit "
                                         f"{max_age_hours}h) — re-run coverage"}
    known_bad = set(report.get("unavailable") or {})
    kept = [s for s in symbols if s not in known_bad]
    dropped = [s for s in symbols if s in known_bad]
    return kept, {"filtered": True, "dropped": dropped,
                  "checked_at": report.get("checked_at"),
                  "reason": f"{len(dropped)} instrument(s) the licensed feed cannot price"}


def subscription_summary(report=None, path=COVERAGE_PATH):
    """Group what is missing by the subscription that would unlock it.

    The grouping is the point. Thirteen individual "no bars" errors read as a
    broken system; the same thirteen grouped as "one FX subscription covers all
    of these" reads as a decision with a price attached.
    """
    report = report if report is not None else load(path)
    if not report:
        return {"available": False,
                "message": "No coverage report yet. Run one to see what is tradable."}

    groups = {}
    for ticker, info in (report.get("unavailable") or {}).items():
        bundle = info.get("bundle") or "unknown"
        group = groups.setdefault(bundle, {"bundle": bundle,
                                           "action": info.get("action", ""),
                                           "symbols": []})
        group["symbols"].append(ticker)
    for group in groups.values():
        group["symbols"].sort()
        group["count"] = len(group["symbols"])

    return {
        "available": True,
        "checked_at": report.get("checked_at"),
        "age_hours": round(age_hours(report) or 0, 1),
        "counts": report.get("counts", {}),
        "groups": sorted(groups.values(), key=lambda g: -g["count"]),
    }
