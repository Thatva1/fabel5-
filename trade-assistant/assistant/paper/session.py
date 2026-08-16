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
from datetime import datetime, timezone

from ..backtest import engine as backtest_engine, portfolio as portfolio_limits, simulator
from ..core import fx
from ..core.config import load_config
from ..research import scanner
from ..risk import sizing
from ..strategies import router as strategy_router
from ..strategies.cross_section import CrossSection
from . import screen
from .book import Book

DEFAULTS = {
    "max_holding_bars": 40,     # matches the backtest; the dominant exit
    "slippage_bps": 5.0,        # against you, both sides
    "starting_equity": None,    # defaults to account.portfolio_value
    "history_period": "2y",     # enough for a 12-month signal plus a 200-day MA
    # Floor on a new position, as a percentage of equity. Below this the
    # position cannot affect the result but still pays commission and occupies
    # a slot — the sliver left when the exposure cap is nearly full.
    "min_position_pct": 1.0,
}


def settings(config):
    cfg = {**DEFAULTS, **((config or {}).get("paper") or {})}
    if cfg["starting_equity"] is None:
        cfg["starting_equity"] = (config.get("account") or {}).get("portfolio_value", 100000)
    return {k: v for k, v in cfg.items() if k != "screen"}


def _today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _slipped(price, direction, bps, opening):
    """Slippage always against you: worse on entry, worse on exit."""
    drift = price * float(bps) / 10_000
    if opening:
        return price + drift if direction == "long" else price - drift
    return price - drift if direction == "long" else price + drift


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
        book.start(equity, currency, today)
        if note:
            book.sessions.append({"date": today, "ran_at": today, "note": note})

    out = {"date": today, "started_fresh": not book.closed and not book.positions,
           "universe": 0, "universe_from_cache": None, "screen": None,
           "rebalanced": False, "opened": [], "closed": [], "skipped": {},
           "notes": []}

    # --- 1. The universe -------------------------------------------------
    report_stage("screening the universe")
    universe, screen_report, cached = screen.tradable_universe(
        config, router, force_refresh=force_refresh)
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
    if held:
        report_stage(f"marking {len(held)} open positions")
        from ..providers import bulk
        frames = bulk.ohlcv_history(held, period="6mo")

    for position in list(book.positions):
        df = frames.get(position["ticker"])
        if df is None or len(df) == 0:
            out["notes"].append(
                f"{position['ticker']}: no price today, carried at its last mark")
            continue
        bar = df.iloc[-1]
        position["last_price"] = float(bar["Close"])
        position["bars_held"] += 1

        reason, level = _exit_reason(position, bar)
        if reason is None and position["bars_held"] >= int(cfg["max_holding_bars"]):
            reason, level = "time", float(bar["Close"])
        if reason is None:
            continue

        fill = _slipped(level, position["direction"], cfg["slippage_bps"], opening=False)
        book.close_position(position, fill, today, reason)
        cost = simulator.cost_config_for(position["ticker"], config)
        book.cash -= cost.get("commission_per_trade", 0.0)
        out["closed"].append({"ticker": position["ticker"], "reason": reason,
                              "pnl": position["pnl"], "r": position["r_multiple"]})

    # --- 3. Rebalance, if this is a rebalance day ------------------------
    due = _rebalance_due(book, today) if rebalance_override is None else rebalance_override
    if not due:
        row = book.mark(today, "marked only — not a rebalance day")
        book.save(book_path) if book_path else book.save()
        out.update({"equity": row["equity"], "summary": book.summary()})
        out["notes"].append(
            "Not a rebalance day. Open positions were marked and exits taken; "
            "no new positions are opened between rebalances by design.")
        return out

    out["rebalanced"] = True
    book.last_rebalance = today
    report_stage(f"fetching history for {len(universe)} instruments")
    from ..providers import bulk
    universe_frames = bulk.ohlcv_history(universe, period=cfg["history_period"])
    if len(universe_frames) < 10:
        out["notes"].append(
            f"Only {len(universe_frames)} instruments returned history; "
            "too few to rank a cross-section. Rebalance abandoned.")
        row = book.mark(today, "rebalance abandoned — insufficient data")
        book.save(book_path) if book_path else book.save()
        out.update({"equity": row["equity"], "summary": book.summary()})
        return out

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
                           f"{len(out['closed'])} closed")
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

    buckets = {}
    for idea in ideas:
        buckets.setdefault(asset_class_of(idea.ticker), []).append(idea)
    order = sorted(buckets, key=lambda k: -(buckets[k][0].reward_risk or 0))

    out = []
    while any(buckets[k] for k in order):
        for key in order:
            if buckets[key]:
                out.append(buckets[key].pop(0))
    return out


def _open_positions(book, ideas, frames, config, cfg, limits, today, out):
    """Take the best candidates that fit inside the portfolio limits."""
    skipped = {"exposure": 0, "max_positions": 0, "correlation": 0,
               "duplicate_exposure": 0, "size_too_small": 0, "cash": 0}
    returns = {t: df["Close"].pct_change().dropna() for t, df in frames.items()}
    for series in returns.values():
        series.index = [str(d)[:10] for d in series.index]

    for idea in ideas:
        equity = book.equity()
        if len(book.positions) >= int(limits["max_open_positions"]):
            skipped["max_positions"] += 1
            continue

        exposure_cap = equity * float(limits["max_gross_exposure_pct"]) / 100
        if book.market_value() >= exposure_cap:
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
        risk_per_share = abs(idea.entry - idea.stop)
        if risk_per_share <= 0:
            skipped["size_too_small"] += 1
            continue

        fill = _slipped(idea.entry, idea.direction, cfg["slippage_bps"], opening=True)
        room = exposure_cap - book.market_value()
        shares = sizing.position_size_by_value(risk_budget, risk_per_share,
                                               min(max_position, room), fill)
        # A token position is not a position. Once the exposure cap is nearly
        # full the remaining room converts to a handful of shares, and the first
        # real session opened NOK at ONE share — nine dollars inside a
        # hundred-and-thirty-five-thousand-dollar book. It cannot move the
        # result, its commission is a pure loss, and it occupies a slot a real
        # position could have used.
        value = shares * fill
        if shares < 1 or value < equity * float(cfg["min_position_pct"]) / 100:
            skipped["size_too_small"] += 1
            continue
        cost = simulator.cost_config_for(idea.ticker, config)
        commission = cost.get("commission_per_trade", 0.0)
        if shares * fill + commission > (book.cash or 0):
            skipped["cash"] += 1
            continue

        book.open_position(
            ticker=idea.ticker, direction=idea.direction, shares=shares,
            price=fill, stop=idea.stop, target=idea.target,
            strategy=idea.strategy, regime=idea.regime, date=today,
            headline=idea.headline, meta=idea.meta)
        book.cash -= commission
        out["opened"].append({"ticker": idea.ticker, "strategy": idea.strategy,
                              "shares": shares, "price": round(fill, 2),
                              "stop": idea.stop, "headline": idea.headline})
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


def _rebalance_due(book, today):
    """First session of a new calendar month.

    Keyed on the last REBALANCE, not on the last session. A trader switched off
    for six weeks rebalances the day it comes back rather than waiting for the
    next month boundary and holding a stale book.

    Deriving this from the session log instead coupled the trading calendar to
    anything that wrote a log line, and it broke immediately: the note recording
    the opening FX conversion landed in `sessions` before this ran, so the very
    first session of a brand-new book concluded it had already rebalanced and
    opened nothing.
    """
    if not book.last_rebalance:
        return True
    return book.last_rebalance[:7] != today[:7]


def _benchmark(router, config):
    symbol = (config.get("scanner") or {}).get("benchmark")
    if not symbol:
        return None
    try:
        df = router.get_prices(symbol)
        return df["Close"] if df is not None else None
    except Exception:
        return None
