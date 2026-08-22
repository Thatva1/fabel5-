"""The intraday book: a trading session, not a holding period.

WHAT MAKES THIS A TRADING SYSTEM RATHER THAN A HOLDING ONE

The daily book opens a position and lets it live for up to ten sessions against
an ATR-wide stop and a 3:1 target. That is a perfectly respectable investment
process and it is NOT a trading firm: the reward multiple takes weeks or months
to pay, so there is nothing to report on any given day except a change in
unrealised marks. You cannot show a realised P&L to anyone from a book like
that, because there mostly isn't one.

Everything here is built the other way round:

  * A position's maximum life is measured in MINUTES and is set by the rule that
    opened it. Nothing is held past its own horizon.
  * Every position is closed before the bell. Not as a risk setting that can be
    relaxed — the session runner refuses to leave anything open, and carrying
    something overnight is treated as an error rather than a warning.
  * Therefore every day produces a realised number. That is the whole point:
    the book's record is what it BANKED, not what it is holding and hoping for.

WHAT IT CANNOT DO ON THIS ACCOUNT, STATED ONCE AND PLAINLY

Prices here are IB historical intraday bars, which arrive roughly 10-15 minutes
late because this account carries no streaming subscription. A five-minute rule
priced on a fifteen-minute-old bar is not the rule — its entries and exits both
land somewhere the rule never chose. So this book runs and records, and every
figure it produces carries that delay, and none of it is a live trading result
until a subscription exists. Saying otherwise would be the same failure this
project has spent its whole history stamping out, with a faster clock.
"""
import os
from datetime import datetime, timezone

from ..backtest import spread as spread_model
from ..core import market_clock
from ..core.config import DATA_DIR
from ..risk import sizing
from ..strategies import intraday as intraday_lib
from .book import Book

BOOK_PATH = os.path.join(DATA_DIR, "intraday_book.json")

DEFAULTS = {
    "risk_per_trade_pct": 0.25,
    "max_position_pct": 5.0,
    "max_gross_exposure_pct": 60.0,
    "max_open_positions": 8,
    "max_daily_loss_pct": 2.0,
    "max_trades_per_day": 40,
    "no_new_entries_minutes_before_close": 30,
    "flat_by_minutes_before_close": 5,
    "bar_size": "5 mins",
    "estimate_spreads": True,
    "max_spread_bps": 40.0,
    "min_spread_bps": 1.0,
    "costs": {"spread_bps": 4.0, "slippage_bps": 2.0, "commission_per_trade": 1.0},
}


def settings(config):
    block = ((config or {}).get("intraday") or {})
    merged = {**DEFAULTS, **block}
    merged["costs"] = {**DEFAULTS["costs"], **(block.get("costs") or {})}
    return merged


def _now():
    return datetime.now(timezone.utc)


def _fill(price, direction, side, costs):
    """Half the spread plus slippage, always against you."""
    drift = price * (float(costs.get("spread_bps", 0.0)) / 2
                     + float(costs.get("slippage_bps", 0.0))) / 10_000
    opening = side == "entry"
    if direction == "long":
        return price + drift if opening else price - drift
    return price - drift if opening else price + drift


def _held_minutes(position, moment):
    opened = position.get("meta", {}).get("opened_at")
    if not opened:
        return 0.0
    try:
        return (moment - datetime.fromisoformat(opened)).total_seconds() / 60.0
    except (TypeError, ValueError):
        return 0.0


def _exit_for(position, bar, remaining, flat_at, moment):
    """Why this position must close now, or None.

    Ordered by what overrides what. The bell beats everything: a position at its
    target and inside the liquidation window closes as `flat_by_close` either
    way, so the ordering only decides the label — but the label is what the
    journal separates the rules' work from the clock's by, and getting it wrong
    credits a strategy with an exit the calendar made.
    """
    if remaining is not None and remaining <= flat_at:
        return "flat_by_close", float(bar["Close"])

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

    horizon = float(position.get("meta", {}).get("max_hold_minutes") or 0)
    if horizon and _held_minutes(position, moment) >= horizon:
        return "time", float(bar["Close"])
    return None, None


def run(config, frames, *, prev_closes=None, now=None, book_path=None,
        book=None, starting_equity=None):
    """One intraday pass: mark, exit, then open if the clock still allows it.

    `frames` is {ticker: intraday bars up to now}. Passed in rather than fetched
    so this is testable without a broker and so a caller can drive it bar by bar
    over history — the same code path either way, which is the only way the
    live book and the backtest cannot drift apart.

    Returns a report. The book is saved unless a caller supplied its own.
    """
    cfg = settings(config)
    moment = now or _now()
    path = book_path or BOOK_PATH
    owns_book = book is None
    book = book if book is not None else Book.load(path)

    out = {"at": moment.isoformat(timespec="seconds"), "opened": [], "closed": [],
           "notes": [], "phase": {}, "realised": 0.0, "costs": 0.0}

    if not book.started:
        equity = starting_equity or (config.get("account") or {}).get(
            "portfolio_value", 100_000)
        book.start(float(equity), cfg.get("trading_currency", "USD"),
                   moment.strftime("%Y-%m-%d"))
        out["notes"].append(f"Opened an intraday book at {equity:,.0f}.")

    spreads = _spreads_for(frames, cfg)

    # --- 1. mark and exit ----------------------------------------------------
    for position in list(book.positions):
        frame = frames.get(position["ticker"])
        if frame is None or not len(frame):
            out["notes"].append(
                f"{position['ticker']}: no bar — carried unmarked. An intraday "
                f"position that cannot be priced cannot be managed.")
            position["mark_failed"] = True
            continue
        bar = frame.iloc[-1]
        position["last_price"] = float(bar["Close"])
        position["mark_failed"] = False
        position["bar_date"] = str(frame.index[-1])[:19]

        venue = market_clock.venue_for(position["ticker"])
        remaining = market_clock.minutes_to_close(venue, moment)
        reason, level = _exit_for(position, bar,
                                  remaining if remaining is not None else 0.0,
                                  cfg["flat_by_minutes_before_close"], moment)
        if reason is None:
            continue

        costs = _costs_for(position["ticker"], cfg, spreads)
        fill = _fill(float(level), position["direction"], "exit", costs)
        book.close_position(position, fill, moment.strftime("%Y-%m-%d"), reason)
        commission = float(costs.get("commission_per_trade", 0.0))
        book.cash -= commission
        position["held_minutes"] = round(_held_minutes(position, moment), 1)
        out["realised"] += position.get("pnl") or 0.0
        out["costs"] += commission
        out["closed"].append({"ticker": position["ticker"], "reason": reason,
                              "pnl": position.get("pnl"),
                              "r": position.get("r_multiple"),
                              "held_minutes": position["held_minutes"]})

    # --- 2. what the clock allows next ---------------------------------------
    phase = _dominant_phase(frames, cfg, moment)
    out["phase"] = phase

    if phase["phase"] != market_clock.PHASE_OPEN:
        out["notes"].append(phase["explanation"])
        _finish(book, out, moment, path, owns_book, cfg)
        return out

    # --- 3. open, within every limit -----------------------------------------
    _open_positions(book, frames, prev_closes or {}, cfg, spreads, moment, out)
    _finish(book, out, moment, path, owns_book, cfg)
    return out


def _spreads_for(frames, cfg):
    if not cfg.get("estimate_spreads", True):
        return {}
    table = spread_model.cost_table(frames, floor_bps=cfg.get("min_spread_bps", 1.0),
                                    cap_bps=cfg.get("max_spread_bps"))
    return table["spreads"]


def _costs_for(ticker, cfg, spreads):
    costs = dict(cfg["costs"])
    if ticker in spreads:
        costs["spread_bps"] = spreads[ticker]
    return costs


def _dominant_phase(frames, cfg, moment):
    """Which session state governs this pass.

    A universe spanning New York and London is in two states at once for part of
    the afternoon. The book takes the most PERMISSIVE state for opening — some
    venue is genuinely open — while every position is still exited against its
    OWN venue's clock in step 1. Collapsing both to one calendar is how a London
    position ends up held four and a half hours past anything tradable.
    """
    venues = {market_clock.venue_for(t) for t in frames} or {"US"}
    states = {}
    for venue in venues:
        states[venue] = market_clock.session_phase(
            venue, moment,
            entry_cutoff_minutes=cfg["no_new_entries_minutes_before_close"],
            flat_minutes=cfg["flat_by_minutes_before_close"])

    order = [market_clock.PHASE_OPEN, market_clock.PHASE_NO_NEW_ENTRIES,
             market_clock.PHASE_LIQUIDATE, market_clock.PHASE_CLOSED]
    best = min(states.values(), key=order.index)
    explanation = {
        market_clock.PHASE_OPEN: "Open — new positions allowed.",
        market_clock.PHASE_NO_NEW_ENTRIES:
            "Inside the last half hour. Nothing new opens; anything still held "
            "is being managed to the bell.",
        market_clock.PHASE_LIQUIDATE:
            "Liquidation window. Everything is being closed.",
        market_clock.PHASE_CLOSED:
            "No market open. An intraday book does nothing when nothing trades.",
    }[best]
    return {"phase": best, "by_venue": states, "explanation": explanation}


def _open_positions(book, frames, prev_closes, cfg, spreads, moment, out):
    equity = book.equity()
    day = moment.strftime("%Y-%m-%d")

    # A hard stop on the day, checked before anything opens. An intraday book
    # can lose a month in an afternoon and the only reliable defence is to stop.
    banked = sum(row.get("pnl") or 0.0 for row in book.closed
                 if str(row.get("exit_date", ""))[:10] == day)
    floor = -abs(float(cfg["max_daily_loss_pct"])) / 100 * (book.starting_equity or equity)
    if banked <= floor:
        out["notes"].append(
            f"Daily loss limit hit: {banked:,.0f} realised against a floor of "
            f"{floor:,.0f}. Nothing further opens today.")
        return

    taken_today = sum(1 for row in book.closed
                      if str(row.get("exit_date", ""))[:10] == day)
    if taken_today >= int(cfg["max_trades_per_day"]):
        out["notes"].append(f"Trade limit for the day reached ({taken_today}).")
        return

    held = {p["ticker"] for p in book.positions}
    room = int(cfg["max_open_positions"]) - len(book.positions)
    if room <= 0:
        out["notes"].append("Every position slot is full.")
        return

    bar_minutes = {"1 min": 1, "5 mins": 5, "15 mins": 15, "30 mins": 30,
                   "1 hour": 60}.get(cfg["bar_size"], 5)

    candidates = []
    for ticker, frame in frames.items():
        if ticker in held or frame is None or len(frame) < 3:
            continue
        if cfg.get("estimate_spreads", True) and ticker not in spreads:
            continue        # too wide to trade, or unmeasurable
        venue = market_clock.venue_for(ticker)
        remaining = market_clock.minutes_to_close(
            venue, moment)
        if remaining is None or remaining <= cfg["no_new_entries_minutes_before_close"]:
            continue
        ctx = intraday_lib.IntradayContext(
            ticker=ticker, bars=frame, prev_close=prev_closes.get(ticker),
            bar_minutes=bar_minutes, minutes_to_close=remaining,
            config=cfg.get("_config") or {})
        for idea in intraday_lib.detect_all(ctx, {"intraday": cfg}):
            candidates.append((idea, remaining))

    # Best reward:risk first. A slot is scarce and the ordering has to be a
    # decision rather than dictionary order, which is what it silently was.
    candidates.sort(key=lambda pair: -(pair[0].reward_risk or 0))

    gross = book.gross_exposure()
    exposure_cap = equity * float(cfg["max_gross_exposure_pct"]) / 100
    risk_budget = equity * float(cfg["risk_per_trade_pct"]) / 100
    position_cap = equity * float(cfg["max_position_pct"]) / 100

    for idea, _remaining in candidates:
        if room <= 0:
            break
        costs = _costs_for(idea.ticker, cfg, spreads)
        entry = _fill(float(idea.entry), idea.direction, "entry", costs)
        risk_per_share = abs(entry - float(idea.stop))
        if risk_per_share <= 0:
            continue

        shares = sizing.position_size_by_value(
            risk_budget, risk_per_share, position_cap, entry)
        if shares < 1:
            continue

        value = shares * entry
        if gross + value > exposure_cap:
            shares = int(max(0, (exposure_cap - gross)) // entry)
            value = shares * entry
        if shares < 1:
            continue
        if value > book.cash:
            shares = int(book.cash // entry)
            value = shares * entry
        if shares < 1:
            continue

        commission = float(costs.get("commission_per_trade", 0.0))
        meta = {**(idea.meta or {}), "opened_at": moment.isoformat(timespec="seconds"),
                "spread_bps": costs["spread_bps"], "reasons": idea.reasons}
        book.open_position(
            ticker=idea.ticker, direction=idea.direction, shares=shares,
            price=entry, stop=idea.stop, target=idea.target,
            strategy=idea.strategy, regime="INTRADAY",
            date=moment.strftime("%Y-%m-%d"), headline=idea.headline, meta=meta,
            price_source="ibkr-intraday", bar_date=str(frames[idea.ticker].index[-1])[:19])
        book.cash -= commission
        out["costs"] += commission
        gross += value
        room -= 1
        out["opened"].append({
            "ticker": idea.ticker, "strategy": idea.strategy,
            "direction": idea.direction, "shares": shares,
            "entry": round(entry, 4), "stop": idea.stop, "target": idea.target,
            "headline": idea.headline, "spread_bps": costs["spread_bps"],
            "max_hold_minutes": meta.get("max_hold_minutes")})


def _finish(book, out, moment, path, owns_book, cfg):
    day = moment.strftime("%Y-%m-%d")
    note = (f"{len(out['opened'])} opened, {len(out['closed'])} closed"
            if (out["opened"] or out["closed"]) else "no change")

    previous = book.daily[-1] if book.daily and book.daily[-1]["date"] == day else {}
    book.mark(day, note, rebalanced=False, opened=len(out["opened"]),
              closed=len(out["closed"]), price_source="ibkr-intraday")
    book.record_day(
        day,
        realised=float(previous.get("realised") or 0.0) + out["realised"],
        costs=float(previous.get("costs") or 0.0) + out["costs"],
        opened=int(previous.get("opened") or 0) + len(out["opened"]),
        closed=int(previous.get("closed") or 0) + len(out["closed"]),
        price_source="ibkr-intraday", note=note)

    # The guarantee, checked rather than assumed. Everything above is arranged
    # so this cannot fire; if it ever does, something has changed that must be
    # seen immediately rather than discovered in a month's returns.
    if book.positions and out["phase"].get("phase") == market_clock.PHASE_CLOSED:
        out["notes"].append(
            f"OVERNIGHT RISK: {len(book.positions)} position(s) are still open "
            f"with every market closed — {', '.join(p['ticker'] for p in book.positions)}. "
            f"This should be impossible. Something did not price during the "
            f"liquidation window.")
        out["overnight"] = [p["ticker"] for p in book.positions]

    out["summary"] = book.summary()
    out["equity"] = book.equity()
    if owns_book:
        book.save(path)
