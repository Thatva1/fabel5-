"""One paper-trading session — mark the book, take exits, rebalance if due.

Run this daily. Most days it does almost nothing, and that is correct: the
strategies rebalance monthly, so on an ordinary Tuesday the only work is
marking open positions to market and checking whether any hit its stop or
target. Once a month it re-ranks the universe and rebalances.

The division of labour is deliberate:

  * The SAME strategy router the backtest and the live scan use decides what to
    buy. Nothing is reimplemented here, so the paper trader cannot drift away
    from the rules that were tested.
  * The SAME portfolio limits from config apply — gross exposure, position
    count, correlation. A paper test that ignores them would report returns the
    live system could never take.
  * Costs are charged on both sides using the same per-market cost config the
    backtest uses. A paper test with free trades is a brochure.

What it deliberately does NOT do: place orders. There is no broker in this
module. Fills are modelled at the next available close with slippage, which is
optimistic against a real market open and is stated as such in the output.
"""
import os
import time
from datetime import datetime, timezone

from ..backtest import engine as backtest_engine, portfolio as portfolio_limits, simulator
from .. import markets
from ..core import fx
from ..core.config import DATA_DIR, load_config
from ..research import scanner
from ..risk import kelly, sizing
from ..strategies import router as strategy_router
from ..strategies.cross_section import CrossSection
from . import screen
from .book import Book

DEFAULTS = {
    "max_holding_bars": 10,     # ~2 weeks; a trading book, not a holding book
    "slippage_bps": 5.0,        # against you, both sides
    "starting_equity": None,    # defaults to account.portfolio_value
    "history_period": "2y",     # enough for a 12-month signal plus a 200-day MA
    # Floor on a new position, as a percentage of equity. Below this the
    # position cannot affect the result but still pays commission and occupies
    # a slot — the sliver left when the exposure cap is nearly full.
    "min_position_pct": 0.5,   # smaller floor so 30 positions can fit
}


def settings(config):
    cfg = {**DEFAULTS, **((config or {}).get("paper") or {})}
    if cfg["starting_equity"] is None:
        cfg["starting_equity"] = (config.get("account") or {}).get("portfolio_value", 100000)
    return {k: v for k, v in cfg.items() if k != "screen"}


HISTORY_CACHE_PATH = os.path.join(DATA_DIR, "universe_history_cache.pkl")


def _history_cache_key(symbols, period):
    import hashlib

    digest = hashlib.sha1("\n".join(sorted(symbols)).encode()).hexdigest()[:16]
    return f"{period}:{len(symbols)}:{digest}"


def _load_history_cache(symbols, period, max_age_hours):
    """Reuse a recent universe fetch instead of walking IBKR again.

    Why this is sound: the strategies rank on 252-day lookbacks and 200-day
    averages. Between two runs on the same day the only bar that can differ is
    today's still-forming one, and one partial bar at the end of a 252-bar
    window does not move a ranking.

    Why it is necessary: the fetch is ~1.44s per instrument over a held-open
    connection, so a 1,560-name universe costs about 38 minutes. Under the old
    monthly schedule that was paid once a month. At half-day it would be paid
    twice a day — 76 minutes of continuous pulling — which is not a schedule
    anyone would actually leave running.

    The cache is keyed on the exact symbol set, so re-screening the universe
    invalidates it rather than silently ranking against yesterday's membership.
    """
    import pickle

    if not max_age_hours:
        return None
    try:
        with open(HISTORY_CACHE_PATH, "rb") as handle:
            payload = pickle.load(handle)
    except (OSError, ValueError, EOFError, pickle.UnpicklingError, AttributeError):
        return None

    if payload.get("key") != _history_cache_key(symbols, period):
        return None
    age_h = (time.time() - payload.get("fetched_at", 0)) / 3600
    if age_h > float(max_age_hours):
        return None
    return {"frames": _in_current_units(payload),
            "price_source": payload.get("price_source"),
            "age_hours": round(age_h, 2)}


# Bumped when the units of a cached frame change. A cache written before a
# boundary correction holds numbers the rest of the project no longer means the
# same thing by, and silently reading it is how a fixed bug comes back.
#
#   1 — IB volume left in ROUND LOTS and LSE prices left in pence.
#   2 — both corrected at the provider by `normalise_bars`.
HISTORY_CACHE_UNITS = 2


def _in_current_units(payload):
    """Bring a cached fetch up to the units the code now assumes.

    Corrected on READ rather than by throwing the cache away, because throwing
    it away costs a 38-minute refetch to recover numbers that are recoverable
    arithmetically — and corrected exactly once, because a cache stamped with
    the current version is left alone. Double-normalising would move London a
    hundredfold in the other direction, which is the same bug wearing a
    different sign.
    """
    frames = payload.get("frames") or {}
    if int(payload.get("units") or 1) >= HISTORY_CACHE_UNITS:
        return frames
    from ..providers.ibkr_provider import IBKRDataProvider
    if (payload.get("price_source") or "").lower() != "ibkr":
        return frames        # yfinance already delivers shares and pounds
    return {ticker: IBKRDataProvider.normalise_bars(ticker, frame)
            for ticker, frame in frames.items()}


def _save_history_cache(symbols, period, frames, price_source):
    import pickle

    try:
        os.makedirs(os.path.dirname(HISTORY_CACHE_PATH), exist_ok=True)
        tmp = HISTORY_CACHE_PATH + ".tmp"
        with open(tmp, "wb") as handle:
            pickle.dump({"key": _history_cache_key(symbols, period),
                         "fetched_at": time.time(), "frames": frames,
                         "units": HISTORY_CACHE_UNITS,
                         "price_source": price_source}, handle,
                        protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, HISTORY_CACHE_PATH)
    except OSError:
        pass        # a cache that cannot be written must not fail the session


def _fetch(symbols, period, config, label, out, progress_cb=None, cache_hours=None):
    """Universe history from IBKR when enabled, yfinance otherwise.

    The feed is named in the session report rather than assumed. A book built
    on yfinance and one built on IBKR are not the same experiment — the second
    is licensed, includes instruments the first has forgotten, and reaches
    London — so which one produced a number has to travel with it.
    """
    from ..providers import bulk

    if cache_hours:
        cached = _load_history_cache(symbols, period, cache_hours)
        if cached and cached["frames"]:
            out["price_source"] = cached["price_source"]
            out.setdefault("notes", []).append(
                f"{label}: reused {len(cached['frames'])} instruments fetched "
                f"{cached['age_hours']}h ago (cache), priced by "
                f"{cached['price_source']}. Daily bars, so a same-day reuse "
                f"cannot change a 252-bar ranking.")
            out["universe_history_cached"] = True
            return cached["frames"]
    from ..risk import kelly  # noqa: F401  (kept for import symmetry)

    if ((config.get("providers") or {}).get("ibkr") or {}).get("enabled"):
        try:
            frames, missing = bulk.ohlcv_history_ibkr(
                symbols, period=period, config=config, progress_cb=progress_cb)
            out.setdefault("notes", []).append(
                f"{label}: {len(frames)} instruments from IBKR"
                + (f"; {len(missing)} unavailable (usually a missing market-data "
                   "subscription for that venue)" if missing else ""))
            out["price_source"] = "ibkr"
            if cache_hours:
                _save_history_cache(symbols, period, frames, "ibkr")
            return frames
        except Exception as exc:
            out.setdefault("notes", []).append(
                f"IBKR unavailable ({type(exc).__name__}), falling back to "
                "yfinance — results are NOT on licensed data.")
    out["price_source"] = "yfinance"
    frames = bulk.ohlcv_history(symbols, period=period)
    if cache_hours:
        _save_history_cache(symbols, period, frames, "yfinance")
    return frames


def _carried_equity(config, book_path=None):
    """Closing equity of the most recently archived book, or None.

    Off unless `paper.carry_forward_equity` is set, because silently inheriting
    a balance would make two runs of the same experiment incomparable — and the
    first thing anyone does with a fresh strategy is run it from a known
    starting figure.
    """
    import glob
    import json as jsonlib

    if not ((config or {}).get("paper") or {}).get("carry_forward_equity"):
        return None

    from .book import BOOK_PATH

    base = (book_path or BOOK_PATH).rsplit(".json", 1)[0]
    archives = sorted(glob.glob(f"{base}-archived-*.json"), reverse=True)
    for path in archives:
        try:
            with open(path) as handle:
                state = jsonlib.load(handle)
            previous = Book(state)
            equity = previous.equity()
            if equity and equity > 0:
                return {"equity": float(equity),
                        "currency": previous.base_currency,
                        "file": os.path.basename(path)}
        except (OSError, ValueError, KeyError, TypeError):
            continue        # a corrupt archive must not stop a new book starting
    return None


def _starved_strategies(config, bars_available):
    """Strategies whose lookback does not fit the history that was fetched.

    Checked and reported rather than assumed. long_reversal needed 1,008 bars
    against the 504 that `history_period: 2y` supplies, produced nothing in
    every session ever run, and nothing anywhere said so.
    """
    out = []
    for name, params in _strategy_lookbacks(config).items():
        need = params
        if bars_available and need > bars_available:
            out.append((name, need))
    return sorted(out, key=lambda x: -x[1])


def _strategy_lookbacks(config):
    """{strategy: bars of history it needs} from the live settings."""
    import importlib
    import pkgutil

    from .. import strategies as pkg

    needs = {}
    overrides = (config or {}).get("strategies") or {}
    for module in pkgutil.iter_modules(pkg.__path__):
        try:
            mod = importlib.import_module("assistant.strategies." + module.name)
        except Exception:
            continue
        for attr in dir(mod):
            obj = getattr(mod, attr)
            # The attribute is `defaults`, lower case, on the Strategy class.
            defaults = getattr(obj, "defaults", None)
            name = getattr(obj, "name", None)
            if not isinstance(defaults, dict) or not name:
                continue
            settings = {**defaults, **(overrides.get(name) or {})}
            lookback = settings.get("lookback_bars")
            if not lookback:
                continue
            needs[name] = int(lookback) + int(settings.get("skip_bars", 0) or 0)
    return needs


def _today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _slipped(price, direction, bps, opening):
    """Slippage always against you: worse on entry, worse on exit."""
    drift = price * float(bps) / 10_000
    if opening:
        return price + drift if direction == "long" else price - drift
    return price - drift if direction == "long" else price + drift


def _age_by_one_bar(position, bar_date):
    """Advance a position's age only when the market has produced a new bar.

    Count BARS, not RUNS. The book runs several times a day — every rebalance
    window under `half_day`, plus the mark after the US close — and each run
    used to add a bar to every position's age. Measured on the live book:
    positions opened on 2026-08-18 read 8 bars held by the 21st, four trading
    days later.

    This is not a cosmetic counter. `max_holding_bars` is read off it, so every
    position was time-exited at roughly half its intended horizon, on a
    schedule that moved whenever the dashboard was restarted or a rebalance was
    forced by hand. A position's age has to come from the market's calendar,
    and the bar's own date is the only thing here that carries it.

    Mutates and returns the position, so the caller reads as one statement.
    """
    if bar_date != position.get("bar_date"):
        position["bars_held"] = int(position.get("bars_held") or 0) + 1
    position["bar_date"] = bar_date
    return position


def _exit_reason(position, bar):
    """Stop before target when a single bar covers both — no intrabar data
    means the order is unknowable, and assuming the target turns every
    ambiguous day into a winner."""
    high, low = float(bar["High"]), float(bar["Low"])
    if position["direction"] == "long":
        if low <= position["stop"]:
            return "stop", position["stop"]
        if high >= position["target"]:
            return "target", position["target"]
    else:
        if high >= position["stop"]:
            return "stop", position["stop"]
        if low <= position["target"]:
            return "target", position["target"]
    return None, None


def run(config=None, router=None, force_refresh=False, rebalance_override=None,
        progress_cb=None, book_path=None):
    """Run one session. Returns a report dict; the book is persisted."""
    from .. import pipeline

    config = config or load_config()
    router = router or pipeline.get_router(config)
    cfg = settings(config)
    limits = portfolio_limits.settings(config)
    today = _today()

    def report_stage(stage):
        if progress_cb:
            progress_cb(stage)

    book = Book.load(book_path) if book_path else Book.load()
    if not book.started:
        equity, currency, note = _opening_balance(config, router, cfg)
        # Carry the last book's closing equity into the new one, so a reset
        # continues the simulation rather than restarting it.
        #
        # Worth being precise about what this does and does not change. An
        # EXISTING book already compounds — cash and positions persist between
        # sessions, so a profitable day raises the base the next day trades
        # from, with no setting involved. The only thing that ever returned the
        # balance to its opening figure was `--reset`, and this makes even that
        # continuous.
        carried = _carried_equity(config, book_path)
        if carried is not None:
            note = (f"Continued from the previous book at "
                    f"{carried['equity']:,.2f} {carried['currency']} "
                    f"(archived {carried['file']}). The starting balance in "
                    f"config was not used, so the equity curve runs unbroken "
                    f"across the reset.")
            equity, currency = carried["equity"], carried["currency"]
        book.start(equity, currency, today)
        if note:
            book.sessions.append({"date": today, "ran_at": today, "note": note})

    out = {"date": today, "started_fresh": not book.closed and not book.positions,
           "universe": 0, "universe_from_cache": None, "screen": None,
           "rebalanced": False, "opened": [], "closed": [], "skipped": {},
           "realised_today": 0.0, "costs_today": 0.0,
           "notes": []}

    # --- 1. The universe -------------------------------------------------
    report_stage("screening the universe")
    universe, screen_report, cached = screen.tradable_universe(
        config, router, force_refresh=force_refresh)

    # Drop what the LICENSED feed cannot price, before anything is ranked.
    # Filtering here rather than at fetch time is the whole point: an
    # instrument that survives to the ranking stage and only then fails to
    # fetch gets quietly served by the fallback, which is how the book came to
    # hold a spot-FX position priced off an unlicensed feed. Fails open — with
    # no coverage report the universe passes through untouched and the report
    # says so, because an empty universe would stop the book dead.
    from ..providers import coverage as coverage_report
    if ((config.get("providers") or {}).get("ibkr") or {}).get("enabled"):
        universe, coverage_info = coverage_report.tradable_symbols(universe, config)
        out["coverage"] = coverage_info
        if coverage_info.get("filtered"):
            dropped = coverage_info.get("dropped") or []
            if dropped:
                out.setdefault("notes", []).append(
                    f"{len(dropped)} instrument(s) excluded — the licensed feed "
                    f"cannot price them: {', '.join(sorted(dropped)[:12])}"
                    + ("…" if len(dropped) > 12 else ""))
        else:
            out.setdefault("notes", []).append(
                f"Coverage NOT applied ({coverage_info.get('reason')}). Instruments "
                "IBKR cannot price may be priced by the yfinance fallback instead.")

    out["universe"], out["universe_from_cache"] = len(universe), cached
    out["screen"] = screen_report
    if not universe:
        out["notes"].append("No tradable universe — nothing can be done.")
        book.mark(today, "no universe")
        book.save(book_path) if book_path else book.save()
        return out

    # --- 2. Mark open positions and take exits ---------------------------
    held = [p["ticker"] for p in book.positions]
    frames = {}
    mark_source = None
    if held:
        report_stage(f"marking {len(held)} open positions")
        mark_out = {}
        frames = _fetch(held, "6mo", config, "marks", mark_out)
        out.setdefault("notes", []).extend(mark_out.get("notes", []))
        # Captured separately from the universe fetch below. Both write
        # price_source into the same report dict, and the universe fetch ran
        # last — so a book marked from the fallback while the universe came from
        # IBKR reported "ibkr" for everything, which is the wrong half of the
        # answer to keep.
        mark_source = mark_out.get("price_source")

    for position in list(book.positions):
        df = frames.get(position["ticker"])
        if df is None or len(df) == 0:
            out["notes"].append(
                f"{position['ticker']}: no price today, carried at its last mark "
                f"({position.get('bar_date') or 'unknown date'}) — this position is "
                f"NOT marked to today's market")
            position["mark_failed"] = True
            continue
        bar = df.iloc[-1]
        position["last_price"] = float(bar["Close"])
        _age_by_one_bar(position, str(df.index[-1])[:10])
        position["price_source"] = mark_source
        position["priced_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        position["mark_failed"] = False

        reason, level = _exit_reason(position, bar)
        if reason is None and position["bars_held"] >= int(cfg["max_holding_bars"]):
            reason, level = "time", float(bar["Close"])
        if reason is None:
            continue

        fill = _slipped(level, position["direction"], cfg["slippage_bps"], opening=False)
        book.close_position(position, fill, today, reason)
        cost = simulator.cost_config_for(position["ticker"], config)
        commission = cost.get("commission_per_trade", 0.0)
        book.cash -= commission
        # Tracked so the day's record can separate money banked by closing
        # trades from the drift on positions still open. An equity level cannot
        # tell those apart, and they mean completely different things.
        out["realised_today"] += position["pnl"] or 0.0
        out["costs_today"] += commission
        out["closed"].append({"ticker": position["ticker"], "reason": reason,
                              "pnl": position["pnl"], "r": position["r_multiple"]})

    # --- 3. Rebalance, if this is a rebalance day ------------------------
    due = (_rebalance_due(book, today, config) if rebalance_override is None
           else rebalance_override)
    if not due:
        row = book.mark(today, "marked only — not a rebalance day",
                        price_source=mark_source, rebalanced=False,
                        opened=0, closed=len(out["closed"]),
                        universe=out.get("universe"))
        out["day"] = book.record_day(
            today, realised=out["realised_today"], costs=out["costs_today"],
            opened=0, closed=len(out["closed"]), price_source=mark_source,
            note="marked only — not a rebalance day")
        book.save(book_path) if book_path else book.save()
        out.update({"equity": row["equity"], "summary": book.summary()})
        out["notes"].append(
            "Not a rebalance day. Open positions were marked and exits taken; "
            "no new positions are opened between rebalances by design.")
        return out

    out["rebalanced"] = True
    book.last_rebalance = today
    book.last_rebalance_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    report_stage(f"fetching history for {len(universe)} instruments")
    universe_frames = _fetch(
        universe, cfg["history_period"], config, "universe", out,
        progress_cb=lambda done, total, ok: report_stage(
            f"fetching {done}/{total} — {ok} with data"),
        cache_hours=cfg.get("universe_history_cache_hours"))
    if len(universe_frames) < 10:
        out["notes"].append(
            f"Only {len(universe_frames)} instruments returned history; "
            "too few to rank a cross-section. Rebalance abandoned.")
        row = book.mark(today, "rebalance abandoned — insufficient data")
        book.save(book_path) if book_path else book.save()
        out.update({"equity": row["equity"], "summary": book.summary()})
        return out

    # A strategy whose lookback does not fit the history fetched is silent,
    # and silence is indistinguishable from "found nothing". Say it instead.
    bars = max((len(f) for f in universe_frames.values()), default=0)
    starved = _starved_strategies(config, bars)
    if starved:
        out.setdefault("notes", []).append(
            f"{len(starved)} strategy/strategies cannot fire on {bars} bars of "
            f"history and were silent, not unsuccessful: "
            + ", ".join(f"{n} needs {need}" for n, need in starved)
            + ". Raise paper.history_period.")
        out["starved_strategies"] = starved

    cross = CrossSection.from_frames(universe_frames)
    benchmark = _benchmark(router, config)
    rebalance_config = _hand_the_calendar_to_the_session(config)

    report_stage(f"ranking {len(universe_frames)} instruments")
    ideas = []
    scan_cfg = config.get("scanner", {}) or {}
    for ticker, df in universe_frames.items():
        if book.is_held(ticker):
            continue
        try:
            snapshot = scanner.scan_ticker(ticker, df, scan_cfg,
                                           benchmark_closes=benchmark)
            if snapshot.get("error"):
                continue
            routed = strategy_router.route(ticker, df, snapshot, rebalance_config,
                                           cross_section=cross,
                                           benchmark_closes=benchmark)
        except Exception:
            continue
        for idea in routed["ideas"]:
            if idea.status == "actionable" and idea.direction:
                ideas.append(idea)

    out["candidates"] = len(ideas)
    ideas = _rank_candidates(ideas, config)
    _open_positions(book, ideas, universe_frames, config, cfg, limits, today, out)

    row = book.mark(today, f"rebalanced — {len(out['opened'])} opened, "
                           f"{len(out['closed'])} closed",
                    price_source=out.get("price_source"), rebalanced=True,
                    opened=len(out["opened"]), closed=len(out["closed"]),
                    universe=out.get("universe"),
                    candidates=out.get("candidates"))
    out["day"] = book.record_day(
        today, realised=out["realised_today"], costs=out["costs_today"],
        opened=len(out["opened"]), closed=len(out["closed"]),
        price_source=out.get("price_source"),
        note=f"rebalanced — {len(out['opened'])} opened, {len(out['closed'])} closed")
    book.save(book_path) if book_path else book.save()
    out.update({"equity": row["equity"], "summary": book.summary()})
    return out


def _rank_candidates(ideas, config):
    """Order candidates for the slots, in the two stages the backtest uses.

    Getting this wrong produced the opposite failure twice in a row, so the
    shape is worth stating plainly.

    Stage one, WITHIN a ticker: strategy priority decides which strategy claims
    that instrument, ties broken on reward:risk. This is engine.backtest_ticker.

    Stage two, ACROSS tickers: reward:risk alone, with no strategy priority at
    all. This is portfolio.simulate.

    Applying priority globally instead — one sort over everything — lets the
    top-ranked strategy take every slot in the book: the rotation filled all
    twelve and neither other strategy got one. Applying no priority lets the
    most frequent signal take every slot instead, which is how the first
    session ended up entirely trend-following. Only the two-stage shape gives
    each instrument to its best strategy and then lets instruments compete.

    A caveat the numbers cannot express: every strategy here targets a fixed
    multiple of its own stop, so reward:risk is 3.0, 3.0 and 2.5 by
    construction and discriminates almost nothing at stage two. Order there is
    effectively the order instruments arrive, which is liquidity order from the
    screen. That is inherited from the backtest rather than invented here, but
    it means the strategy mix in the book is not a considered judgement about
    which signal is better today.
    """
    priority = {name: rank for rank, name in enumerate(
        backtest_engine.settings(config)["strategy_priority"])}

    best_per_ticker = {}
    for idea in ideas:
        rank = (priority.get(idea.strategy, len(priority)), -(idea.reward_risk or 0))
        current = best_per_ticker.get(idea.ticker)
        if current is None or rank < current[0]:
            best_per_ticker[idea.ticker] = (rank, idea)

    winners = [idea for _, idea in best_per_ticker.values()]
    winners.sort(key=lambda i: -(i.reward_risk or 0))
    return _interleave_segments(winners)


def _already_exposed(book, ticker):
    """True when the book already holds the same underlying under another name.

    Identity, not statistics. Where two instruments are the same exposure by
    construction — spot sterling and the sterling future, SPY and the E-mini —
    no amount of measuring should be needed to notice, and in the FX case
    measuring actively misleads.
    """
    from ..markets import exposure_group

    group = exposure_group(ticker)
    if group is None:
        return False
    return any(exposure_group(p["ticker"]) == group for p in book.positions)


def _interleave_segments(ideas):
    """Offer the slots to each asset class in turn, best of each first.

    Reward:risk is 3.0, 3.0 and 2.5 by construction here — every strategy
    targets a fixed multiple of its own stop — so sorting on it decides almost
    nothing and the real tie-break is the order instruments arrive. That order
    is the universe list, which puts 1,482 shares ahead of 46 macro
    instruments, and with twelve slots the currencies and futures were never
    reached: a session produced 1,711 candidates across every segment and
    opened nine US equities.

    Round-robin fixes the arrival bias without inventing a quality score the
    signals cannot support. Each segment presents its best candidate, then its
    second, and so on. A segment with nothing to offer simply drops out, so
    this never forces a trade to fill a quota — it only stops one segment
    taking every slot because it happened to be listed first.
    """
    from ..markets import asset_class_of

    def bucket_key(ticker):
        """Asset class, and for equities the VENUE as well.

        The same arrival-order bias this function was written to fix repeats one
        level down. Every listed share — 1,000+ US names and 40 London ones —
        landed in a single "equity" bucket, and the London names sort below the
        US names on liquidity, so no UK line ever reached a slot: the book ran
        45 US equities and zero UK while holding UK names in its universe the
        whole time.

        Splitting by venue applies the existing argument where it still bites.
        It forces no trade and fills no quota — a venue with nothing to offer
        drops out — it only stops one venue taking every slot because it
        happened to be listed first.
        """
        asset_class = asset_class_of(ticker)
        if asset_class != "equity":
            return asset_class
        from ..core.market_clock import venue_for
        return f"equity:{venue_for(ticker)}"

    buckets = {}
    for idea in ideas:
        buckets.setdefault(bucket_key(idea.ticker), []).append(idea)
    order = sorted(buckets, key=lambda k: -(buckets[k][0].reward_risk or 0))

    out = []
    while any(buckets[k] for k in order):
        for key in order:
            if buckets[key]:
                out.append(buckets[key].pop(0))
    return out


def _load_kelly_edges():
    """Out-of-sample return streams per strategy, written by the study.

    Kelly needs an edge estimate and refuses an in-sample one. This file is the
    only place a legitimate estimate comes from: the study measures each
    strategy on the half of history it was NOT selected against and writes the
    returns here. No file means no out-of-sample evidence yet, and sizing falls
    back to the fixed-risk rule rather than inventing an edge.
    """
    import json
    from ..core.config import PROJECT_ROOT

    path = os.path.join(PROJECT_ROOT, "reports", "KELLY-EDGES.json")
    try:
        with open(path) as handle:
            payload = json.load(handle)
        returns = payload.get("out_of_sample_returns") or {}
    except (OSError, ValueError):
        return {}, None

    # The file itself carries no generation date, and it SIZES POSITIONS — so
    # without this the book could bet on edges measured against a strategy
    # library that has since been rewritten, with nothing on screen to say so.
    # Reported rather than enforced: a stale edge file is a reason to re-run the
    # study, not a reason to stop trading mid-session.
    try:
        age_days = (time.time() - os.path.getmtime(path)) / 86400
    except OSError:
        age_days = None
    return returns, {"path": os.path.basename(path),
                     "split_date": payload.get("split_date"),
                     "strategies": len(returns),
                     "age_days": round(age_days, 1) if age_days is not None else None}


def _kelly_units(idea, *, equity, fill, multiplier, config, edges,
                 max_position, room, group_room):
    """Units from fractional Kelly, or None to fall back to fixed risk.

    None rather than zero, deliberately: "Kelly has nothing to say about this
    strategy yet" and "Kelly says do not bet" are different answers, and
    collapsing them would silently stop trading every strategy the study has
    not yet measured out of sample.
    """
    returns = edges.get(idea.strategy)
    if not returns:
        return None
    try:
        fraction = kelly.fraction_for(returns, config=config)
    except kelly.EdgeNotEstimable:
        return None
    if fraction <= 0:
        return 0
    return kelly.apply_caps(fraction, equity=equity, price=fill,
                            multiplier=multiplier,
                            max_position_value=max_position,
                            exposure_room=room, group_room=group_room)


def _group_room(book, ticker, equity, limits):
    """Headroom left in this instrument's exposure group.

    Spot sterling and the sterling future are one bet; without this they would
    each be sized as though they were the only claim on that risk.
    """
    from ..markets import exposure_group

    group = exposure_group(ticker)
    if group is None:
        return None
    cap = equity * float(limits.get("max_position_pct", 15.0)) / 100
    used = sum(abs(p["last_price"] * float(p.get("multiplier", 1.0) or 1.0) * p["shares"])
               for p in book.positions if exposure_group(p["ticker"]) == group)
    return max(0.0, cap - used)


def _open_positions(book, ideas, frames, config, cfg, limits, today, out):
    """Take the best candidates that fit inside the portfolio limits."""
    skipped = {"exposure": 0, "max_positions": 0, "correlation": 0,
               "duplicate_exposure": 0, "size_too_small": 0, "cash": 0}
    kelly_edges, edge_provenance = (
        _load_kelly_edges() if kelly.settings(config).get("enabled") else ({}, None))
    if kelly_edges:
        age = (edge_provenance or {}).get("age_days")
        out["kelly_edges"] = edge_provenance
        out.setdefault("notes", []).append(
            f"Kelly sizing active for {len(kelly_edges)} strategies with an "
            f"out-of-sample edge (split {(edge_provenance or {}).get('split_date')}, "
            f"file {age}d old); the rest fall back to fixed-risk sizing."
            + (" That edge file is over a quarter old — re-run the study before "
               "trusting it to size positions." if age and age > 90 else ""))
    returns = {t: df["Close"].pct_change().dropna() for t, df in frames.items()}
    for series in returns.values():
        series.index = [str(d)[:10] for d in series.index]

    for idea in ideas:
        equity = book.equity()
        if len(book.positions) >= int(limits["max_open_positions"]):
            skipped["max_positions"] += 1
            continue

        # Capped on NOTIONAL, not on capital committed. For shares the two are
        # identical; for a futures contract they differ by its leverage, and a
        # cap read off the margin would let a book carry ten times the exposure
        # it thinks it has.
        exposure_cap = equity * float(limits["max_gross_exposure_pct"]) / 100
        if book.gross_exposure() >= exposure_cap:
            skipped["exposure"] += 1
            continue

        # Duplicate exposure, checked BEFORE correlation. Some instruments are
        # the same bet by construction and cannot be relied on to look like it:
        # spot sterling against the sterling future measures 0.12 correlation
        # because the two series close on different boundaries, so the cap below
        # waves both through and the book doubles its position without knowing.
        if _already_exposed(book, idea.ticker):
            skipped["duplicate_exposure"] += 1
            continue

        # The same correlation cap the portfolio simulator applies. Six mega-cap
        # tech names bought on one rebalance is one bet with six tickets.
        candidate = {"ticker": idea.ticker, "entry_date": today}
        open_like = [{"ticker": p["ticker"]} for p in book.positions]
        if portfolio_limits._too_correlated(candidate, open_like, limits, returns.get):
            skipped["correlation"] += 1
            continue

        risk_budget = equity * float(limits["risk_per_trade_pct"]) / 100
        max_position = equity * float(limits["max_position_pct"]) / 100
        # Risk per UNIT is a price move times the contract multiplier. Without
        # the multiplier a ten-year note future looks like it risks a fraction
        # of a dollar per contract and the sizer buys thousands of them.
        multiplier = markets.contract_multiplier(idea.ticker)
        risk_per_share = abs(idea.entry - idea.stop) * multiplier
        if risk_per_share <= 0:
            skipped["size_too_small"] += 1
            continue

        fill = _slipped(idea.entry, idea.direction, cfg["slippage_bps"], opening=True)
        room = exposure_cap - book.gross_exposure()
        # The cash cap converts to units through the notional value of one unit.
        unit_notional = fill * multiplier
        group_room = _group_room(book, idea.ticker, equity, limits)

        shares = None
        if kelly.settings(config).get("enabled"):
            shares = _kelly_units(idea, equity=equity, fill=fill,
                                  multiplier=multiplier, config=config,
                                  edges=kelly_edges, max_position=max_position,
                                  room=min(max_position, room),
                                  group_room=group_room)
            if shares is not None:
                sized_by = "kelly"
        if shares is None:
            sized_by = "fixed_risk"
            capped = min(max_position, room)
            if group_room is not None:
                capped = min(capped, group_room)
            shares = sizing.position_size_by_value(risk_budget, risk_per_share,
                                                   capped, unit_notional)
        # A token position is not a position. Once the exposure cap is nearly
        # full the remaining room converts to a handful of shares, and the first
        # real session opened NOK at ONE share — nine dollars inside a
        # hundred-and-thirty-five-thousand-dollar book. It cannot move the
        # result, its commission is a pure loss, and it occupies a slot a real
        # position could have used.
        value = shares * unit_notional
        if shares < 1 or value < equity * float(cfg["min_position_pct"]) / 100:
            skipped["size_too_small"] += 1
            continue
        cost = simulator.cost_config_for(idea.ticker, config)
        commission = cost.get("commission_per_trade", 0.0)
        # Cash needed is the CAPITAL, which is margin for a future.
        needed = markets.capital_required(idea.ticker, fill, shares) + commission
        if needed > (book.cash or 0):
            skipped["cash"] += 1
            continue

        book.open_position(
            ticker=idea.ticker, direction=idea.direction, shares=shares,
            price=fill, stop=idea.stop, target=idea.target,
            strategy=idea.strategy, regime=idea.regime, date=today,
            headline=idea.headline, meta=idea.meta,
            price_source=out.get("price_source"),
            bar_date=str(frames[idea.ticker].index[-1])[:10]
            if idea.ticker in frames else None)
        book.cash -= commission
        out["opened"].append({"ticker": idea.ticker, "strategy": idea.strategy,
                              "shares": shares, "price": round(fill, 2),
                              "stop": idea.stop, "headline": idea.headline,
                              "sized_by": sized_by,
                              "notional": round(value, 2),
                              "capital": round(needed - commission, 2),
                              "leverage": markets.leverage_of(idea.ticker, fill, shares)})
    out["skipped"] = skipped


def trading_currency(config):
    """The single currency this book trades in.

    A paper book holds ONE cash balance. Buying a US share subtracts its dollar
    cost from that balance, so unless every instrument prices in the same
    currency the balance is pounds and dollars added together — the exact error
    `convert_trades` exists to prevent in the backtest, and one that looks like
    a working number rather than a broken one.

    Yahoo gives US listings no suffix, so a universe of unsuffixed symbols is
    dollars. Anything else has to be stated explicitly, and a mixed universe is
    refused rather than silently summed.
    """
    cfg = (config or {}).get("paper", {}) or {}
    declared = cfg.get("trading_currency")
    if declared:
        return str(declared).upper()
    universe = (cfg.get("screen") or {}).get("universe")
    symbols = universe if isinstance(universe, (list, tuple)) else None
    if universe == "watchlist":
        symbols = config.get("watchlist") or []
    if symbols is not None:
        suffixes = {("" if "." not in str(s).upper() else
                     "." + str(s).upper().rsplit(".", 1)[-1]) for s in symbols}
        if suffixes and suffixes != {""}:
            raise ValueError(
                f"The paper book holds one cash balance, but this universe spans "
                f"{sorted(suffixes)}. Set paper.trading_currency explicitly, or "
                "restrict the universe to one market — a single balance cannot "
                "hold two currencies without silently adding them together.")
    return "USD"


def _rates_both_ways(router, account_ccy, currency):
    """Every rate leg between two currencies, asked for from both directions.

    get_fx_rates builds its pairs from {base, "USD"}, so asking for GBP->USD
    with USD as the base collapses that set to one target and yields a single
    pair — and because the router swallows a failed fetch and returns what it
    has, one transient miss comes back as an empty dict rather than an error.
    Asked the other way round the same call returns both GBPUSD and USDGBP.

    Depending on that asymmetry would be depending on an accident, so both
    directions are requested and merged. A genuinely unavailable rate still
    ends up as an empty dict, and the caller still refuses to start the book.
    """
    rates = {}
    for base in (currency, account_ccy):
        try:
            rates.update(router.get_fx_rates({account_ccy, currency}, base) or {})
        except Exception:
            continue
    return rates


def _opening_balance(config, router, cfg):
    """(equity, currency, note) for a brand-new book.

    The account is denominated in whatever the user banks in; the book trades in
    whatever the market prices in. When those differ the opening balance is
    converted ONCE, here, and the book runs in the market's currency from then
    on. Converting every position on every mark instead would bury a currency
    return inside what is supposed to be a measurement of the strategy.
    """
    currency = trading_currency(config)
    account_ccy = config.get("base_currency", "USD")
    equity = float(cfg["starting_equity"])
    if account_ccy == currency:
        return equity, currency, None

    try:
        converted = fx.convert(equity, account_ccy, currency,
                               _rates_both_ways(router, account_ccy, currency))
    except Exception as exc:
        raise ValueError(
            f"The account is in {account_ccy} and this universe trades in "
            f"{currency}, but no {account_ccy}->{currency} rate is available "
            f"({exc}). Refusing to start a book whose balance would be two "
            "currencies added together.")
    note = (f"Opened with {equity:,.0f} {account_ccy} converted to "
            f"{converted:,.0f} {currency} at {converted / equity:.4f}. The book "
            f"is denominated in {currency} from here, so its return measures the "
            "strategy rather than the strategy plus the exchange rate.")
    return converted, currency, note


def _hand_the_calendar_to_the_session(config):
    """Config with every strategy's own rebalance gate opened for this run.

    There are two clocks here and they do not agree. A strategy decides it is a
    rebalance bar by looking at its own price frame: is the last bar the first
    of a new month? The session decides by looking at the book: has a month
    turned since I last ran? Left alone, the session declares a rebalance day
    and every strategy then refuses, because today is the 9th and the last bar
    is a Friday in the middle of the month. Nothing would ever trade.

    The strategy's internal calendar exists for the BACKTEST, where nothing else
    schedules and each bar must decide for itself. In live paper trading the
    session is the scheduler, so it takes the decision and the strategies
    evaluate their signal as of today. The signal itself — the 12-1 rank, the
    trailing year, the beta — is untouched; only the question "am I allowed to
    act today" moves to the caller that actually knows.
    """
    from ..strategies import registry

    blocks = dict((config.get("strategies") or {}))
    for strategy in registry.all_strategies():
        block = dict(blocks.get(strategy.name) or {})
        if "rebalance" in strategy.defaults:
            block["rebalance"] = "any"
            blocks[strategy.name] = block
    return {**config, "strategies": blocks}


REBALANCE_SCHEDULES = ("monthly", "weekly", "daily", "half_day")


def _rebalance_window(schedule, moment, split_hour):
    """A label that changes exactly when a new rebalance becomes due.

    Comparing labels rather than doing date arithmetic keeps every schedule the
    same three lines and makes "have we already rebalanced in this window"
    answerable from one stored string.
    """
    if schedule == "monthly":
        return moment.strftime("%Y-%m")
    if schedule == "weekly":
        year, week, _ = moment.isocalendar()
        return f"{year}-W{week:02d}"
    if schedule == "daily":
        return moment.strftime("%Y-%m-%d")
    # half_day: two windows per calendar day, split at split_hour UTC.
    half = "A" if moment.hour < split_hour else "B"
    return f"{moment.strftime('%Y-%m-%d')}-{half}"


def _rebalance_due(book, today, config=None, now=None):
    """Is a rebalance due under the configured schedule?

    Keyed on the last REBALANCE, not on the last session. A trader switched off
    for six weeks rebalances the day it comes back rather than waiting for the
    next boundary and holding a stale book.

    Deriving this from the session log instead coupled the trading calendar to
    anything that wrote a log line, and it broke immediately: the note recording
    the opening FX conversion landed in `sessions` before this ran, so the very
    first session of a brand-new book concluded it had already rebalanced and
    opened nothing.

    WHAT A FASTER SCHEDULE DOES AND DOES NOT BUY. Every strategy here ranks on
    DAILY bars — a trailing year of returns, a 200-day average. Those inputs do
    not change between two runs on the same day, so a sub-daily schedule
    produces the SAME ranking each time; it cannot find a different set of
    names. What it does do is reuse capital sooner: the rebalance step only ever
    opens positions (exits are taken separately, every session), so a slot freed
    by a stop this morning is refilled this afternoon instead of standing empty
    until the next month boundary. Names already held are skipped, so running
    more often does not churn the book or pay commission twice for the same
    position.
    """
    cfg = (config or {}).get("paper") or {}
    schedule = str(cfg.get("rebalance_every", "monthly")).lower()
    if schedule not in REBALANCE_SCHEDULES:
        schedule = "monthly"
    split_hour = int(cfg.get("half_day_split_hour_utc", 12))

    previous = book.last_rebalance_at or book.last_rebalance
    if not previous:
        return True

    # The session's DATE comes from `today`, which the caller owns and which the
    # tests drive the calendar through; only the TIME OF DAY comes from the
    # clock, because that is the one thing a date string cannot supply and a
    # sub-daily window needs. Reading both from the clock made every schedule
    # ignore `today` entirely — a session replaying 2026-01-06 was judged
    # against the window the machine happened to be in when it ran.
    moment = now
    if moment is None:
        wall = datetime.now(timezone.utc)
        try:
            moment = datetime.strptime(today[:10], "%Y-%m-%d").replace(
                tzinfo=timezone.utc, hour=wall.hour, minute=wall.minute)
        except (TypeError, ValueError):
            moment = wall
    current = _rebalance_window(schedule, moment, split_hour)

    # A book written before this setting existed stored only a date. Its window
    # label is recomputed from that date so an upgrade does not read as "never
    # rebalanced" and immediately rebalance again.
    if book.last_rebalance_at:
        try:
            previous_moment = datetime.fromisoformat(book.last_rebalance_at)
        except ValueError:
            previous_moment = moment
    else:
        try:
            previous_moment = datetime.strptime(book.last_rebalance[:10], "%Y-%m-%d") \
                .replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return True
        # A legacy date carries no time of day, so it cannot say which half of
        # that day it belonged to. Treated as the first window, which at worst
        # allows one extra rebalance on upgrade rather than suppressing one.
        if schedule == "half_day":
            previous_moment = previous_moment.replace(hour=0)

    return current != _rebalance_window(schedule, previous_moment, split_hour)


def _benchmark(router, config):
    symbol = (config.get("scanner") or {}).get("benchmark")
    if not symbol:
        return None
    try:
        df = router.get_prices(symbol)
        return df["Close"] if df is not None else None
    except Exception:
        return None
