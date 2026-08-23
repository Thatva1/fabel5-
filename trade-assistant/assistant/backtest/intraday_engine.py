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
from . import spread

DEFAULTS = {
    "no_new_entries_minutes_before_close": 30,
    "flat_by_minutes_before_close": 5,
    "bar_size": "5 mins",
    "costs": {"spread_bps": 4.0, "slippage_bps": 2.0, "commission_per_trade": 1.0},
    # Charge each instrument ITS OWN spread, measured from its own bars.
    #
    # This matters far more than it looks, and it matters more the wider the
    # universe gets. The apparent reversion in an illiquid name is mostly bid-ask
    # bounce — a print at the bid, then the offer, then the bid has "reverted"
    # twice and moved not at all — so charging a mega-cap's 4bp across fifteen
    # hundred names pays SPY's cost for a small-cap's illusion. The result is an
    # edge that gets more convincing the more instruments you feed it and does
    # not exist at any of them. See backtest/spread.py.
    "estimate_spreads": True,
    # Wider than this and the instrument is dropped rather than traded. Keeping
    # it adds noise dressed as signal; dropping it silently would hide how much
    # of the universe the study actually covers, so the names are reported.
    "max_spread_bps": 40.0,
    "min_spread_bps": 1.0,
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
    # One pass over the session builds the cumulative sums every bar's context
    # would otherwise rebuild from scratch. Prefix sums only, so a context
    # holding the first i bars still cannot see past them.
    precomputed = intraday_lib.precompute(frame)

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
                config=config, precomputed=precomputed)
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

def _clustered_t(trades, key="gross_pct"):
    """t on SESSION means, not on trades. The only one worth quoting.

    A one-sample t over trades assumes 26,380 independent draws. They are
    nothing of the kind: fifteen hundred instruments traded through the same
    five sessions move together, so when the market drops at 13:30 several
    hundred VWAP-reversion longs are wrong simultaneously and for one reason.
    The independent unit is much closer to the SESSION than to the trade.

    Measured on the full-universe sweep of 2026-08-23, the difference is not
    academic:

        naive, per trade      +2.15bp   t = +4.63   n = 26,380
        clustered by session  +2.08bp   t = +1.86   n = 5

    Same edge, and the evidence for it collapses from overwhelming to absent.
    The first number was the one this function used to report, and reporting it
    alone would have sent somebody to buy a market-data subscription on the
    strength of five days.

    Returns (t, cluster_count). None when there are too few sessions to say
    anything at all — which, with a week of data, is the usual answer and
    should be, rather than a number that implies otherwise.
    """
    by_session = {}
    for trade in trades:
        stamp = str(trade.get("entry_time") or "")[:10]
        if stamp:
            by_session.setdefault(stamp, []).append(float(trade.get(key) or 0.0))
    if len(by_session) < 2:
        return None, len(by_session)
    means = [sum(v) / len(v) for v in by_session.values()]
    mean = sum(means) / len(means)
    return _t_statistic(means, mean), len(means)


def _t_statistic(values, mean):
    """One-sample t, or None when the sample cannot support one.

    None rather than a number in two cases, and the second is the one that
    bites. A single observation obviously has no dispersion — but so does a
    set of identical trades, where floating-point residue leaves a variance of
    1e-34 and the ratio comes back as 1.3e16. A t of ten quadrillion is not
    overwhelming evidence, it is a divide by zero that did not quite divide by
    zero, and printed beside a real result it is indistinguishable from one.

    Serial correlation within a session makes even the honest number
    optimistic, so treat it as a floor on the doubt rather than a p-value.
    """
    if len(values) < 2:
        return None
    variance = sum((x - mean) ** 2 for x in values) / (len(values) - 1)
    if variance <= 0:
        return None
    stderr = math.sqrt(variance / len(values))
    if stderr <= abs(mean) * 1e-9:
        return None
    return round(mean / stderr, 2)


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
    t_stat = _t_statistic(net, mean)

    # The SAME question asked of the gross return, and it is a different
    # question with a different answer.
    #
    # t on net says whether the strategy made or lost money after costs. t on
    # GROSS says whether the rule found anything at all. The full-universe run
    # on 2026-08-23 is exactly why both are needed: net t was -19.83, which
    # reads as a total failure, while gross t was +4.74 on a 2.2bp edge. The
    # rules DO find something real and it is five times too small to pay its
    # own spread. "Find a cheaper venue" and "these rules do not work" are
    # opposite conclusions and only this number separates them.
    mean_gross = sum(gross) / len(gross)
    t_gross = _t_statistic(gross, mean_gross)
    # The honest one. See _clustered_t — the per-trade figure above overstates
    # the evidence by roughly 2.5x on a week of data, in the direction that
    # makes an untradable result look like a discovery.
    t_clustered, sessions = _clustered_t(trades)

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
        "t_gross": t_gross,
        "t_clustered": t_clustered,
        "sessions": sessions,
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

    # The CLUSTERED figure decides the verdict wherever it exists. The
    # per-trade one is kept for comparison and is never the basis of a claim.
    t_gross = summary.get("t_clustered")
    naive = summary.get("t_gross")
    sessions = summary.get("sessions") or 0
    trades = summary["trades"]

    # Below this, no arrangement of the arithmetic produces evidence. Said
    # plainly rather than dressed up as a weak result, because "not significant"
    # invites one more parameter sweep and "you have five days" does not.
    if sessions and sessions < 20:
        naive_text = f" (per-trade t reads {naive:+.2f}, which assumes the trades are independent — they are not)" if naive is not None else ""
        return (f"{sessions} session(s) is not enough to answer this. "
                f"{trades} trades sound like a lot and are not: everything "
                f"inside one session shares one market, so the independent "
                f"count is {sessions}, not {trades}. The gross edge is "
                f"{breakeven:+.1f}bp{naive_text}. Re-run over months before "
                f"reading anything into it.")
    # Every branch quotes it, and it is legitimately absent on a sample with no
    # dispersion. Formatted once here rather than guarded in four places.
    t_text = f"t = {t_gross:+.2f}" if t_gross is not None else "t undefined"

    # No dispersion means no significance test, and without one none of the
    # branches below is entitled to its conclusion. "A real edge at t
    # undefined" is a contradiction, and it was one this function printed.
    if t_gross is None:
        return (f"Cannot be judged: {trades} trade(s) with no spread of "
                f"outcomes, so there is no significance test to run. The "
                f"{breakeven:+.1f}bp figure is arithmetic on a degenerate "
                f"sample, not a measurement.")

    # Asked in the only order that makes sense: is there anything there at all,
    # then can it pay for itself. Reversing them produces the most common
    # intraday self-deception — a confident cost analysis of a number that was
    # never different from zero.
    if t_gross is not None and abs(t_gross) < 2.0:
        return (f"Nothing there to cost. {trades} trades, gross edge "
                f"{breakeven:+.1f}bp at {t_text} — inside its own "
                f"error bar, so comparing it against the {charged:.1f}bp spread "
                f"would be measuring noise. More sessions, or a rule with a "
                f"real hypothesis behind it.")

    if breakeven <= 0:
        return (f"Loses money BEFORE costs: {abs(breakeven):.1f}bp a trade "
                f"gross, {t_text} on {trades} trades. Costs are not "
                f"the problem and no venue fixes this — the rule is wrong.")

    if breakeven <= charged:
        shortfall = charged / breakeven
        return (f"A real edge, and far too small to trade. {breakeven:.1f}bp "
                f"gross at {t_text} over {trades} trades is genuine — "
                f"but this account pays {charged:.1f}bp, so costs must fall "
                f"{shortfall:.1f}x or the edge must rise {shortfall:.1f}x. "
                f"Neither is a parameter. Worth knowing the rules find "
                f"SOMETHING; not worth trading on this venue at this bar size.")

    margin = breakeven - charged
    return (f"Survives its costs: {breakeven:.1f}bp of gross edge at "
            f"{t_text} against {charged:.1f}bp charged, leaving "
            f"{margin:.1f}bp. Thin enough that a wider spread on a bad day "
            f"removes it, so size accordingly.")


def run(frames, *, prev_closes=None, config=None):
    """Every instrument, every session in its frame. Returns trades + summary.

    `frames` is {ticker: intraday DataFrame spanning several days}. Sessions are
    split on the bar's own date, and each session is handed the previous
    session's closing price — the two strongest rules in the library are defined
    against it and cannot be expressed without it.

    Each instrument pays its OWN estimated spread unless that is switched off.
    Running a wide universe on one flat cost is the single largest error a
    study like this can make, and it is the error that invents an edge rather
    than hiding one.
    """
    config = config or {}
    cfg = settings(config)

    liquidity, spreads = None, {}
    if cfg.get("estimate_spreads", True):
        table = spread.cost_table(frames,
                                  floor_bps=cfg.get("min_spread_bps", 1.0),
                                  cap_bps=cfg.get("max_spread_bps"))
        spreads = table["spreads"]
        liquidity = {**spread.describe(table),
                     "excluded_names": [row["ticker"] for row in table["excluded"][:20]],
                     "cap_bps": cfg.get("max_spread_bps")}
        # An instrument too wide to trade is not traded. It was measured, it was
        # named, and it is out.
        frames = {t: f for t, f in (frames or {}).items() if t in spreads}

    all_trades, per_ticker = [], {}

    for ticker, frame in (frames or {}).items():
        if frame is None or not len(frame):
            continue
        costs = _costs_for(ticker, cfg, spreads)
        ticker_config = {**config, "intraday": {**cfg, "costs": costs}}
        trades, prev_close = [], (prev_closes or {}).get(ticker)
        for _day, rows in frame.groupby(frame.index.date):
            if len(rows) >= 6:      # too few bars to form an opening range
                trades.extend(run_session(ticker, rows, prev_close=prev_close,
                                          config=ticker_config))
            prev_close = float(rows["Close"].iloc[-1])
        per_ticker[ticker] = {**summarise(trades, costs),
                              "spread_bps": costs["spread_bps"]}
        all_trades.extend(trades)

    by_strategy = {}
    for trade in all_trades:
        by_strategy.setdefault(trade["strategy"], []).append(trade)

    # What the surviving universe actually paid, weighted by how much of it was
    # traded — not the number in config.yaml, which by this point describes
    # nothing that happened.
    charged = _blended_costs(cfg, spreads, per_ticker)
    overall = summarise(all_trades, charged)
    return {
        "bar_size": cfg["bar_size"],
        "trades": all_trades,
        "overall": overall,
        "verdict": verdict(overall),
        "liquidity": liquidity,
        "spread_source": ("measured per instrument (Corwin-Schultz)"
                          if cfg.get("estimate_spreads", True)
                          else "flat, from config.yaml"),
        "by_strategy": {name: {**summarise(rows, charged),
                               "verdict": verdict(summarise(rows, charged))}
                        for name, rows in by_strategy.items()},
        "by_ticker": per_ticker,
    }


def _costs_for(ticker, cfg, spreads):
    """This instrument's cost block: its own spread, the shared rest."""
    costs = dict(cfg["costs"])
    if ticker in spreads:
        costs["spread_bps"] = spreads[ticker]
    return costs


def _blended_costs(cfg, spreads, per_ticker):
    """The universe's cost, weighted by the trades each instrument produced.

    An unweighted mean would let a thousand names that never fired a signal
    drag the reported cost toward the illiquid tail, and a rule that only ever
    trades the tightest names would be judged against a spread it never paid.
    """
    costs = dict(cfg["costs"])
    if not spreads:
        return costs
    weighted, total = 0.0, 0
    for ticker, summary in (per_ticker or {}).items():
        count = summary.get("trades") or 0
        if count and ticker in spreads:
            weighted += spreads[ticker] * count
            total += count
    if total:
        costs["spread_bps"] = round(weighted / total, 2)
    return costs
