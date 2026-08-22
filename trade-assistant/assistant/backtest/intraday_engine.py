"""Walk one session's bars and trade it, one bar at a time.

The same structural guarantee the daily engine rests on: at bar `i` nothing may
see bar `i+1`. The strategies are handed `frame.iloc[:i+1]`, a slice that
physically cannot contain the future. That matters more here than it does daily
— an intraday rule with one bar of look-ahead does not overstate its return
slightly, it reliably turns losing rules into winners.

WHAT THIS MODELS, AND WHAT IT REFUSES TO PRETEND

  * INTRABAR ORDER IS UNKNOWABLE. When one bar's range covers both the stop and
    the target, this takes the STOP. Five-minute bars carry no tick sequence, so
    assuming the target turns every ambiguous bar into a winner — and ambiguous
    bars are common precisely in the volatile sessions where the difference
    between a good rule and a bad one is decided.

  * COSTS ARE CHARGED ON BOTH SIDES, as a spread plus slippage plus commission.
    Halving the spread to "one side" is the single most common way an intraday
    backtest flatters itself, and an intraday book pays this many times a day
    rather than a few times a month. The headline output is therefore not the
    return but the BREAKEVEN COST: the round trip at which the rule stops
    earning. If that number sits below what the account actually pays, the edge
    is not tradable however good the hit rate looks.

  * FLAT BY CLOSE IS ENFORCED, NOT ASSUMED. Positions are liquidated inside the
    flat window at the prevailing price, and no new position opens after the
    entry cutoff. A rule whose returns depend on being allowed to hold overnight
    is a different rule, and this refuses to be it.

  * THE ENTRY IS THE NEXT BAR'S OPEN. The signal is computed from a bar's close,
    which you only know once that bar has ended, so the first price you could
    actually transact at is the next bar's open. Filling at the signal bar's
    close is a quarter of a percent of free money per trade at five-minute
    resolution, and it is invisible in the output.
"""
import math
from datetime import timezone
from zoneinfo import ZoneInfo

from ..core import market_clock
from ..strategies import intraday as intraday_lib

DEFAULTS = {
    "no_new_entries_minutes_before_close": 30,
    "flat_by_minutes_before_close": 5,
    "bar_size": "5 mins",
    "costs": {"spread_bps": 4.0, "slippage_bps": 2.0, "commission_per_trade": 1.0},
}

BAR_MINUTES = {"1 min": 1, "5 mins": 5, "15 mins": 15, "30 mins": 30,
               "1 hour": 60, "2 hours": 120}


def settings(config):
    block = ((config or {}).get("intraday") or {})
    merged = {**DEFAULTS, **block}
    merged["costs"] = {**DEFAULTS["costs"], **(block.get("costs") or {})}
    return merged


def _aware(stamp, venue):
    """An intraday bar's timestamp as an aware UTC datetime.

    IB hands back local exchange time, sometimes tz-aware and sometimes not.
    A naive stamp read as UTC would move the whole American session five hours
    and put every bar outside its own trading day — so a naive one is localised
    to the venue rather than assumed.
    """
    if stamp.tzinfo is None:
        return stamp.replace(tzinfo=ZoneInfo(market_clock.VENUES[venue]["tz"]))
    return stamp.astimezone(timezone.utc)


def _round_trip_bps(costs):
    """Total cost of getting in and out, in basis points of notional.

    The spread is crossed on BOTH sides — half on the way in, half on the way
    out, which sums to the full quoted spread — and slippage is charged each
    side on top.
    """
    return float(costs.get("spread_bps", 0.0)) + 2 * float(costs.get("slippage_bps", 0.0))


def _fill(price, direction, side, costs):
    """Move the fill against you by half the spread plus slippage."""
    drift = price * (float(costs.get("spread_bps", 0.0)) / 2
                     + float(costs.get("slippage_bps", 0.0))) / 10_000
    opening = side == "entry"
    if direction == "long":
        return price + drift if opening else price - drift
    return price - drift if opening else price + drift


def _exit_level(position, bar):
    """Stop before target when one bar covers both — the order is unknowable."""
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


def run_session(ticker, frame, *, prev_close=None, config=None, venue=None):
    """Trade one instrument through one session. Returns a list of trades.

    One position at a time. Pyramiding is a separate decision with its own
    evidence, and allowing it here would silently change what every number in
    the report means.
    """
    config = config or {}
    cfg = settings(config)
    costs = cfg["costs"]
    venue = venue or market_clock.venue_for(ticker)
    bar_minutes = BAR_MINUTES.get(cfg["bar_size"], 5)

    entry_cutoff = int(cfg["no_new_entries_minutes_before_close"])
    flat_at = int(cfg["flat_by_minutes_before_close"])

    trades, position, pending = [], None, None

    for i in range(len(frame)):
        bar = frame.iloc[i]
        moment = _aware(frame.index[i].to_pydatetime(), venue)
        remaining = market_clock.minutes_to_close(venue, moment)
        # A bar outside regular hours has no session left to measure against.
        # It is carried for context but nothing is transacted on it.
        if remaining is None:
            continue

        # --- an order placed last bar fills at THIS bar's open ---------------
        if pending is not None and position is None:
            idea = pending
            pending = None
            if remaining > flat_at:
                raw_entry = float(bar["Open"])
                entry = _fill(raw_entry, idea.direction, "entry", costs)
                position = {
                    "ticker": ticker, "strategy": idea.strategy,
                    "direction": idea.direction, "entry": entry,
                    # The price before costs, kept so `gross_pct` can mean what
                    # it says. Measuring gross from the FILLED entry quietly
                    # absorbs half the round trip into the "before costs"
                    # figure, which halves the breakeven cost the whole verdict
                    # turns on — and understating a breakeven makes a tradable
                    # rule look untradable.
                    "raw_entry": raw_entry,
                    "entry_time": str(frame.index[i]),
                    "entry_bar": i,
                    "stop": idea.stop, "target": idea.target,
                    "headline": idea.headline,
                    "max_hold_minutes": idea.meta.get("max_hold_minutes", 120),
                    "risk_per_share": abs(entry - idea.stop),
                }

        # --- exits, before anything else -------------------------------------
        if position is not None:
            reason, level = _exit_level(position, bar)
            held = (i - position["entry_bar"]) * bar_minutes
            if reason is None and remaining <= flat_at:
                reason, level = "flat_by_close", float(bar["Close"])
            if reason is None and held >= position["max_hold_minutes"]:
                reason, level = "time", float(bar["Close"])
            if reason is not None:
                trades.append(_close(position, level, reason, frame.index[i],
                                     held, costs))
                position = None

        # --- signals, only while a new position is still allowed -------------
        if position is None and pending is None and remaining > entry_cutoff:
            ctx = intraday_lib.IntradayContext(
                ticker=ticker, bars=frame.iloc[:i + 1], prev_close=prev_close,
                bar_minutes=bar_minutes, minutes_to_close=remaining,
                config=config)
            ideas = intraday_lib.detect_all(ctx, config)
            if ideas:
                # The signal is computed from this bar's CLOSE, which is only
                # known once the bar has ended. The earliest transactable price
                # is therefore the next bar's open.
                pending = ideas[0]

    # A session that ends with a position open means the flat-by-close rule did
    # not fire, which can only happen if the bars stop before the close. Closed
    # at the last price rather than dropped: a silently discarded trade is a
    # survivorship filter on the worst outcomes.
    if position is not None:
        last = frame.iloc[-1]
        held = (len(frame) - 1 - position["entry_bar"]) * bar_minutes
        trades.append(_close(position, float(last["Close"]), "session_ended",
                             frame.index[-1], held, costs))

    return trades


def _close(position, level, reason, stamp, held_minutes, costs):
    """Book the trade twice: once at the prices, once at the fills.

    GROSS is raw level to raw level — what the rule saw. NET is fill to fill —
    what the account keeps. The gap between them is the entire round trip, and
    it has to be the entire round trip, because `breakeven_bps` is read off the
    gross figure and compared against what is actually paid.
    """
    exit_price = _fill(float(level), position["direction"], "exit", costs)
    sign = 1.0 if position["direction"] == "long" else -1.0
    gross_pct = (float(level) / position["raw_entry"] - 1) * 100 * sign
    net_pct = (exit_price / position["entry"] - 1) * 100 * sign
    risk = position["risk_per_share"]
    return {
        **{k: position[k] for k in ("ticker", "strategy", "direction", "entry",
                                    "entry_time", "stop", "target", "headline")},
        "exit": round(exit_price, 4),
        "exit_time": str(stamp),
        "exit_reason": reason,
        "held_minutes": held_minutes,
        "gross_pct": round(gross_pct, 4),
        "net_pct": round(net_pct, 4),
        "r_multiple": round((exit_price - position["entry"]) * sign / risk, 3)
        if risk else None,
    }


# --- reporting ---------------------------------------------------------------

def summarise(trades, costs=None):
    """What these trades add up to, and the number that decides it.

    `breakeven_bps` is the headline. Every other figure here can look healthy
    on a rule that cannot survive its own spread.
    """
    if not trades:
        return {"trades": 0, "note": "No trade fired."}

    net = [t["net_pct"] for t in trades]
    gross = [t["gross_pct"] for t in trades]
    wins = [x for x in net if x > 0]
    losses = [x for x in net if x < 0]
    mean = sum(net) / len(net)

    # Is this distinguishable from zero at all?
    #
    # Run against a pure random walk this engine reports a few basis points of
    # gross "edge" — the breakout rule's payoff shape on noise — and without
    # this number that reads as a finding. It is not: on 144 trades a few bp
    # sits comfortably inside the standard error, and a breakeven cost computed
    # from noise is a breakeven cost for nothing.
    #
    # A plain one-sample t on the net returns. Serial correlation within a
    # session makes it optimistic, so treat it as a floor on the doubt rather
    # than a p-value worth quoting.
    if len(net) > 1:
        variance = sum((x - mean) ** 2 for x in net) / (len(net) - 1)
        stderr = math.sqrt(variance / len(net)) if variance > 0 else 0.0
        t_stat = round(mean / stderr, 2) if stderr else None
    else:
        t_stat = None

    by_reason = {}
    for trade in trades:
        entry = by_reason.setdefault(trade["exit_reason"],
                                     {"trades": 0, "net_pct": 0.0})
        entry["trades"] += 1
        entry["net_pct"] = round(entry["net_pct"] + trade["net_pct"], 4)

    return {
        "trades": len(trades),
        "win_rate_pct": round(len(wins) / len(net) * 100, 1),
        "mean_net_pct": round(mean, 4),
        "total_net_pct": round(sum(net), 3),
        "mean_gross_pct": round(sum(gross) / len(gross), 4),
        "best_pct": round(max(net), 3),
        "worst_pct": round(min(net), 3),
        "profit_factor": (round(sum(wins) / abs(sum(losses)), 2)
                          if losses and sum(losses) else None),
        "mean_r": round(sum(t["r_multiple"] for t in trades if t["r_multiple"] is not None)
                        / max(1, sum(1 for t in trades if t["r_multiple"] is not None)), 3),
        "mean_held_minutes": round(sum(t["held_minutes"] for t in trades) / len(trades), 1),
        "t_stat": t_stat,
        "by_exit_reason": by_reason,
        # The round-trip cost at which the mean GROSS edge is exactly consumed.
        # Compared against what the account actually pays, this is the whole
        # answer — a rule whose breakeven is below its own spread is not a
        # strategy, it is a way to pay a broker.
        "breakeven_bps": round(sum(gross) / len(gross) * 100, 1),
        "charged_bps": round(_round_trip_bps(costs or DEFAULTS["costs"]), 1),
    }


def verdict(summary, charged_bps=None):
    """A plain answer about whether this survives the cost it actually pays.

    Asked in the only order that makes sense: is the result distinguishable
    from noise at all, then does it clear the spread. Reversing those produces
    the most common intraday self-deception there is — a confident cost
    analysis of a number that was never different from zero.
    """
    if not summary.get("trades"):
        return "No trade fired — nothing to judge."
    breakeven = summary["breakeven_bps"]
    charged = charged_bps if charged_bps is not None else summary["charged_bps"]

    t_stat = summary.get("t_stat")
    if t_stat is not None and abs(t_stat) < 2.0:
        return (f"Indistinguishable from noise: {summary['trades']} trades, "
                f"t = {t_stat:+.2f}. The {breakeven:+.1f}bp of gross edge is "
                f"inside the error bar, so comparing it against the "
                f"{charged:.1f}bp spread would be measuring nothing. More "
                f"sessions, or a rule with a real hypothesis behind it.")

    if breakeven <= 0:
        return (f"No edge even before costs: the mean trade loses "
                f"{abs(breakeven):.1f}bp gross. Costs are not the problem.")
    if breakeven <= charged:
        return (f"Not tradable here. The gross edge is {breakeven:.1f}bp a "
                f"round trip and this account pays {charged:.1f}bp, so the "
                f"spread eats it. A cheaper venue or a larger bar would be the "
                f"only thing that changes this — not a parameter.")
    margin = breakeven - charged
    return (f"Survives its costs: {breakeven:.1f}bp of gross edge against "
            f"{charged:.1f}bp charged, leaving {margin:.1f}bp. Thin enough that "
            f"a wider spread on a bad day removes it, so size accordingly.")


def run(frames, *, prev_closes=None, config=None):
    """Every instrument, every session in its frame. Returns trades + summary.

    `frames` is {ticker: intraday DataFrame spanning several days}. Sessions are
    split on the bar's own date, and each session is handed the previous
    session's closing price — the two strongest rules in the library are defined
    against it and cannot be expressed without it.
    """
    config = config or {}
    cfg = settings(config)
    all_trades, per_ticker = [], {}

    for ticker, frame in (frames or {}).items():
        if frame is None or not len(frame):
            continue
        trades, prev_close = [], (prev_closes or {}).get(ticker)
        for _day, rows in frame.groupby(frame.index.date):
            if len(rows) >= 6:      # too few bars to form an opening range
                trades.extend(run_session(ticker, rows, prev_close=prev_close,
                                          config=config))
            prev_close = float(rows["Close"].iloc[-1])
        per_ticker[ticker] = summarise(trades, cfg["costs"])
        all_trades.extend(trades)

    by_strategy = {}
    for trade in all_trades:
        by_strategy.setdefault(trade["strategy"], []).append(trade)

    overall = summarise(all_trades, cfg["costs"])
    return {
        "bar_size": cfg["bar_size"],
        "trades": all_trades,
        "overall": overall,
        "verdict": verdict(overall),
        "by_strategy": {name: {**summarise(rows, cfg["costs"]),
                               "verdict": verdict(summarise(rows, cfg["costs"]))}
                        for name, rows in by_strategy.items()},
        "by_ticker": per_ticker,
    }
