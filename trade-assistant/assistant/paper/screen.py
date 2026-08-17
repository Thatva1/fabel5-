"""Which instruments the paper trader is allowed to consider.

"All the stocks" is 28,192 US symbols, and a cross-sectional momentum ranking
pointed at that list unscreened will put a $0.30 shell company that tripled on
one news day at the top of the leaderboard, every month. That is not the
strategy in the paper; it is a machine for finding the least tradable thing in
the market. A sample of forty random symbols from the cached list returned four
that are already delisted.

So the universe is the full list MINUS what cannot honestly be traded:

  * a minimum share price, because sub-dollar names move in ticks that are a
    large fraction of their value and the spread eats the edge
  * a minimum median daily dollar volume, because a position you cannot exit at
    the modelled price is not a position
  * a minimum history, because a 12-month momentum signal needs 12 months

Median dollar volume rather than mean, deliberately: one spike on an otherwise
untraded shell clears a mean and is exactly the name the ranking reaches for.

The screen is configurable and can be switched off. Switching it off is a real
choice with a real consequence, not a formality — the results stop describing
anything you could have executed.
"""
import json
import os
import re
import time

from ..core.config import DATA_DIR
from ..providers import bulk

CACHE_PATH = os.path.join(DATA_DIR, "paper_universe.json")
# Liquidity measurements survive a failed run. Rate limiting is not an edge
# case at this width — it is what happens — and losing an hour of successful
# measurements every time the limiter trips makes the screen impossible to
# finish rather than merely slow. Each run continues where the last stopped.
PARTIAL_PATH = os.path.join(DATA_DIR, "paper_liquidity_partial.json")
PARTIAL_MAX_AGE = 3 * 24 * 3600
# Screening the full 28,192-symbol listing takes roughly eighty minutes of
# network time. Listings and liquidity move slowly, so it is re-run weekly
# rather than on every session — otherwise the trader would spend most of its
# life re-discovering that AAPL is still liquid.
CACHE_MAX_AGE = 7 * 24 * 3600

DEFAULTS = {
    # "watchlist"  — only the names in config.watchlist
    # "us_all"     — every symbol in the cached Finnhub US listing
    # a list       — an explicit set of symbols
    "universe": "us_all",
    # 17,608 of the 30,935 symbols Finnhub lists for "US" are OOTC — the
    # over-the-counter tail of foreign ordinaries and shells. They are not
    # tradable in any useful sense, and screening them burns the entire data
    # rate limit before reaching the exchanges that matter. Excluding them more
    # than halves the work and removes nothing anyone would have held.
    "exclude_exchanges": ["OOTC"],
    # FX and futures trade alongside the shares in the same book. They bypass
    # the liquidity screen because its tests are written for equities and would
    # reject every one of them for failing a question that does not apply.
    "include_macro": True,
    "include_fx": True,
    "include_futures": True,
    # London. Hand-listed rather than screened, because the liquidity filter
    # reads a Finnhub listing that only covers US exchanges — there is no bulk
    # LSE equivalent here. Needs IBKR: yfinance serves .L symbols, but the
    # licensed feed is the point of trading them at all.
    "include_lse": True,
    # Leveraged and inverse funds price as a MULTIPLE of something else, so a
    # momentum ranking that buys them is taking leverage the risk gate never
    # sees: a 15% position in a 3x fund is a 45% position in the thing it
    # tracks. They also decay against a daily reset, which makes a multi-week
    # hold structurally different from the index it appears to track. TMF, SOXL,
    # TQQQ, UVXY and SVXY were all inside the tradable universe before this.
    "exclude_leveraged": True,
    # Volatility ETPs roll futures and bleed by construction. Momentum will
    # happily rank one first after a spike, which is exactly the wrong moment.
    "exclude_volatility": True,
    "min_price": 5.0,
    "min_dollar_volume": 5_000_000.0,   # median daily traded value
    "min_history_bars": 60,             # inside the 3-month liquidity window
    "max_symbols": 1500,                # cap AFTER screening, most liquid first
    "liquidity_period": "3mo",
    "enabled": True,
}

# Instrument types the Finnhub listing carries that a long-only equity rotation
# has no business holding. Warrants and rights expire; units are pre-split SPAC
# packages. All three price in ways that make a momentum rank meaningless.
EXCLUDED_TYPES = {"WARRANT", "RIGHT", "UNIT", "PREFERRED"}

# Matched against a FUND's name, never a company's. "Direxion Daily Semiconductor
# Bull 3X" is leveraged; a company with "Bull" in its name is not, and applying
# these to ordinary shares would quietly delete real businesses.
_LEVERAGE_MARKERS = (
    # No trailing word boundary: "Graniteshares 2Xlong Amd Etf" runs the
    # multiplier straight into the next word, and requiring one let a 2x fund
    # through. A digit immediately before an X is specific enough on its own.
    re.compile(r"\d\s*X"),          # 2X, 3X, Bl3X, 2Xlong
    re.compile(r"\bULTRA"),         # ProShares Ultra / UltraPro
    re.compile(r"\bBULL\b"),        # Direxion Daily ... Bull
    re.compile(r"\bBEAR\b"),
    re.compile(r"\bINVERSE\b"),
    re.compile(r"\bLEVERAGED\b"),
)
_VOLATILITY_MARKERS = (re.compile(r"\bVIX\b"), re.compile(r"\bVOLATILITY\b"))


def _is_derivative_fund(row, cfg):
    """True when a fund tracks a multiple of, or the inverse of, something else.

    Only ever applied to funds. A leveraged fund is not a cheaper way to hold
    the index — it is a different instrument with a daily reset, and a strategy
    that ranks it alongside ordinary shares is silently sizing 3x positions.
    """
    if str(row.get("type", "")).upper() not in {"ETP", "ETF"}:
        return False
    name = str(row.get("name", "")).upper()
    if cfg.get("exclude_leveraged") and any(p.search(name) for p in _LEVERAGE_MARKERS):
        return True
    if cfg.get("exclude_volatility") and any(p.search(name) for p in _VOLATILITY_MARKERS):
        return True
    return False


def settings(config):
    return {**DEFAULTS, **((config or {}).get("paper", {}) or {}).get("screen", {})}


def macro_symbols(config):
    """FX pairs and futures to trade alongside the shares.

    These are appended AFTER the liquidity screen rather than run through it. A
    currency pair has no share price and no daily dollar volume in any
    comparable sense, and a futures symbol is a rolled front-month series rather
    than a security, so the screen's tests are meaningless for both — and
    applying them anyway would silently drop every macro instrument on the
    grounds that it failed a test written for equities.
    """
    from ..markets import symbols as macro_catalogue

    cfg = settings(config)
    if not cfg.get("include_macro", True):
        return []
    out = list(macro_catalogue(include_fx=cfg.get("include_fx", True),
                               include_futures=cfg.get("include_futures", True)))
    if cfg.get("include_lse", True):
        from ..markets import lse_universe
        out += lse_universe()
    return out


def candidate_symbols(config, router):
    """The raw symbol list before liquidity screening."""
    cfg = settings(config)
    universe = cfg["universe"]

    if isinstance(universe, (list, tuple)):
        return [str(s).upper() for s in universe]
    if universe == "watchlist":
        return [str(s).upper() for s in (config.get("watchlist") or [])]

    rows = []
    try:
        rows = router.universe_rows()
    except Exception:
        rows = []

    excluded = {str(m).upper() for m in (cfg.get("exclude_exchanges") or ())}
    symbols = []
    for row in rows:
        if str(row.get("type", "")).upper() in EXCLUDED_TYPES:
            continue
        if excluded and str(row.get("mic", "")).upper() in excluded:
            continue
        if _is_derivative_fund(row, cfg):
            continue
        symbol = row.get("symbol")
        if symbol:
            symbols.append(symbol)
    return symbols


def apply(symbols, config, progress_cb=None):
    """Screen to what is actually tradable. Returns (kept, report).

    report carries the counts at each stage, because "the universe is 900 names"
    is not a useful thing to read without knowing that it started at 28,000 and
    where the other 27,000 went.
    """
    cfg = settings(config)
    report = {"considered": len(symbols), "priced": 0, "kept": 0,
              "rejected_price": 0, "rejected_volume": 0, "rejected_history": 0,
              "no_data": 0, "capped": 0, "screen_enabled": bool(cfg["enabled"])}

    if not cfg["enabled"]:
        kept = list(symbols)[:int(cfg["max_symbols"])]
        report["kept"] = len(kept)
        report["capped"] = max(0, len(symbols) - len(kept))
        return kept, report

    # Resume: anything measured recently is not measured again. A run that dies
    # to the rate limiter three quarters of the way through leaves those three
    # quarters behind for the next one.
    measured = load_partial()
    outstanding = [s for s in symbols if s not in measured]
    report["resumed_from"] = len(measured)
    report["measured_now"] = len(outstanding)

    table = dict(measured)

    def remember(rows):
        # Mutates `table` rather than merging into a copy. When the limiter
        # trips, the exception unwinds before the return value is ever
        # assigned, so anything measured has to already be in `table` — a
        # version of this that merged into a temporary wrote an EMPTY file over
        # the partial results on the way out, destroying exactly what it existed
        # to preserve.
        table.update(rows)
        save_partial(table)

    throttled = None
    if outstanding:
        try:
            table.update(bulk.liquidity_table(
                outstanding, period=cfg["liquidity_period"],
                progress_cb=progress_cb, on_batch=remember))
        except bulk.ThrottleSuspected as exc:
            throttled = exc
    save_partial(table)
    if throttled is not None:
        # Re-raised only after the partial work is safely on disk.
        raise throttled

    report["priced"] = len(table)
    report["no_data"] = len(symbols) - len(table)

    survivors = []
    for symbol, row in table.items():
        if row["bars"] < int(cfg["min_history_bars"]):
            report["rejected_history"] += 1
            continue
        if row["price"] < float(cfg["min_price"]):
            report["rejected_price"] += 1
            continue
        if row["dollar_volume"] < float(cfg["min_dollar_volume"]):
            report["rejected_volume"] += 1
            continue
        survivors.append((row["dollar_volume"], symbol))

    # Most liquid first, so the cap keeps the names that can actually absorb an
    # order rather than whichever happened to sort alphabetically.
    survivors.sort(reverse=True)
    limit = int(cfg["max_symbols"])
    report["capped"] = max(0, len(survivors) - limit)
    kept = [symbol for _, symbol in survivors[:limit]]
    report["kept"] = len(kept)
    return kept, report


def load_partial():
    """Liquidity rows measured by earlier runs, or {} when absent or stale."""
    try:
        if not os.path.exists(PARTIAL_PATH):
            return {}
        if time.time() - os.path.getmtime(PARTIAL_PATH) > PARTIAL_MAX_AGE:
            return {}
        with open(PARTIAL_PATH) as handle:
            rows = json.load(handle)
        return rows if isinstance(rows, dict) else {}
    except Exception:
        return {}


def save_partial(table):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = PARTIAL_PATH + ".tmp"
        with open(tmp, "w") as handle:
            json.dump(table, handle)
        os.replace(tmp, PARTIAL_PATH)
    except Exception:
        pass


def clear_partial():
    try:
        os.remove(PARTIAL_PATH)
    except OSError:
        pass


def load_cached():
    """The last screened universe, or None when absent or stale."""
    try:
        if not os.path.exists(CACHE_PATH):
            return None
        if time.time() - os.path.getmtime(CACHE_PATH) > CACHE_MAX_AGE:
            return None
        with open(CACHE_PATH) as handle:
            payload = json.load(handle)
        return payload if payload.get("symbols") else None
    except Exception:
        return None


def save_cached(symbols, report):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(CACHE_PATH, "w") as handle:
            json.dump({"symbols": list(symbols), "report": report,
                       "built_at": time.time()}, handle)
    except Exception:
        pass


# Names that are among the most heavily traded on the US market on any given
# day. This is not a curated watchlist and nothing is ever added to the universe
# because it appears here — the list exists only as a canary. A screen of the
# full US listing that drops NVDA and SPY did not discover anything about
# liquidity; it failed, and the only question is whether it admits it.
SANITY_CANARIES = ("AAPL", "MSFT", "NVDA", "AMZN", "SPY", "QQQ", "META", "GOOGL")
MIN_CANARIES_PRESENT = 4


def sanity_check(symbols, report, config):
    """Why this screened universe cannot be trusted, or None if it can.

    Written after a full 28,192-symbol run returned 537 "tradable" names with
    NVDA, MSFT, SPY, QQQ, META and GOOGL all missing and 88% of symbols marked
    "no data" — yfinance had rate-limited partway through and whole chunks came
    back empty. Nothing detected it, so the wreckage was cached as a universe.
    A throttled run is indistinguishable from a market of delisted shells unless
    something checks, and this is that something.
    """
    cfg = settings(config)
    # Only meaningful for a wide screen. A deliberate 20-name universe has no
    # reason to contain SPY, and failing it there would be nonsense.
    if not cfg["enabled"] or report.get("considered", 0) < 1000:
        return None

    present = [c for c in SANITY_CANARIES if c in set(symbols)]
    if len(present) < MIN_CANARIES_PRESENT:
        return (f"only {len(present)} of {len(SANITY_CANARIES)} reference names "
                f"survived the screen ({', '.join(present) or 'none'}). A screen "
                "of the whole US market cannot lose the most heavily traded "
                "shares on it — this run was rate-limited, not selective.")

    considered = report.get("considered") or 0
    if considered and report.get("no_data", 0) / considered > 0.75:
        return (f"{report['no_data']:,} of {considered:,} symbols returned no data. "
                "Some of a full exchange listing really is defunct, but not "
                "three quarters of it.")
    return None


def tradable_universe(config, router, force_refresh=False, progress_cb=None):
    """The screened, cached universe. Returns (symbols, report, from_cache).

    Raises ThrottleSuspected when the result is not trustworthy. Refusing is the
    point: a caller handed a silently truncated universe will build a book on an
    arbitrary slice of the market and have no way to know.
    """
    if not force_refresh:
        cached = load_cached()
        if cached:
            # Macro instruments are cheap to re-derive and must survive a cache
            # written before they existed, so they are topped up on read.
            cached_symbols = list(cached["symbols"])
            known = set(cached_symbols)
            cached_symbols += [s for s in macro_symbols(config) if s not in known]
            return cached_symbols, cached["report"], True

    symbols, report = apply(candidate_symbols(config, router), config,
                            progress_cb=progress_cb)
    problem = sanity_check(symbols, report, config)
    if problem:
        report["rejected"] = problem
        raise bulk.ThrottleSuspected(
            f"Refusing to cache this universe: {problem} Nothing was saved, so "
            "the previous universe (if any) is still in place. Re-run when the "
            "rate limit has reset.")

    macro = [s for s in macro_symbols(config) if s not in set(symbols)]
    if macro:
        symbols = list(symbols) + macro
        report["macro_added"] = len(macro)
    save_cached(symbols, report)
    return symbols, report, False


def describe(report):
    """One line a human can read in a log."""
    if not report.get("screen_enabled"):
        return (f"Screen OFF — {report['kept']} symbols taken as-is. "
                "Results will include names you could not have traded.")
    return (f"{report['considered']:,} symbols considered · "
            f"{report['no_data']:,} no data · "
            f"{report['rejected_price']:,} too cheap · "
            f"{report['rejected_volume']:,} too thin · "
            f"{report['rejected_history']:,} too new · "
            f"{report['capped']:,} beyond the cap · "
            f"{report['kept']:,} tradable")
