"""Why each trade was taken, and why it made or lost money.

The book already recorded what happened. It could not say why, and the two
questions a person actually asks after a losing month are both "why" questions:
where is the money going, and what should change.

Everything here is DERIVED from what the strategy recorded at the time — the
headline it wrote, the measurements that triggered it, the levels it chose, and
how the position ended. Nothing is invented after the fact and no language model
is involved. That matters more here than anywhere else in the project: a
narrative written after seeing the outcome will always find a reason the outcome
was foreseeable, which is exactly the bias this record exists to defend against.

So the entry rationale is quoted from before the trade was opened, and the exit
attribution is mechanical — which level was touched, or which limit expired.
"""
from collections import defaultdict

# What each exit actually means, in the plainest terms available.
EXIT_MEANING = {
    "stop": ("Stopped out", "Price reached the stop set when the position was "
                            "opened. The loss was the one budgeted for."),
    "target": ("Target reached", "Price reached the profit target set when the "
                                 "position was opened."),
    "time": ("Time exit", "The holding-period limit expired before either the "
                          "stop or the target was reached. The position was "
                          "closed at whatever it was worth."),
}


def _measurement_lines(meta):
    """The numbers the strategy actually measured, in readable form.

    These are the evidence the rule fired on. Without them an entry reason is a
    slogan — "trend-following long" says nothing a reader can check, while
    "12-month return +20.4%, realised vol 13.7%" can be argued with.
    """
    if not meta:
        return []
    labels = {
        "trailing_return_pct": "Trailing return",
        "lookback_bars": "Lookback (bars)",
        "realized_vol_pct": "Realised volatility",
        "inverse_vol_weight": "Inverse-vol weight",
        "height_atr": "Range height (ATR)",
        "lookback_days": "Lookback (days)",
        "rsi": "RSI",
        "distance_from_high_pct": "Distance from 52-week high",
        "beta": "Beta",
        "drawdown_pct": "Drawdown from peak",
    }
    out = []
    for key, label in labels.items():
        if key in meta and meta[key] is not None:
            value = meta[key]
            if isinstance(value, float):
                value = f"{value:,.2f}".rstrip("0").rstrip(".")
            out.append(f"{label}: {value}")
    return out


def entry_reason(trade):
    """Why this position was opened — as recorded before the outcome was known."""
    lines = []
    if trade.get("headline"):
        lines.append(trade["headline"])
    strategy = trade.get("strategy") or "unknown strategy"
    regime = trade.get("regime")
    if regime:
        lines.append(f"Fired by the {strategy} rules in a {regime.lower()} market.")
    else:
        lines.append(f"Fired by the {strategy} rules.")

    entry, stop, target = (trade.get("entry_price"), trade.get("stop"),
                           trade.get("target"))
    if entry and stop and target:
        risk = abs(entry - stop)
        reward = abs(target - entry)
        if risk:
            lines.append(
                f"Planned risk {risk:,.2f} per unit against {reward:,.2f} of "
                f"reward — {reward / risk:.1f}:1, decided before entry.")
    return lines


def outcome_reason(trade):
    """Why it made or lost money. Mechanical, not narrative."""
    reason = trade.get("exit_reason")
    title, explanation = EXIT_MEANING.get(
        reason, ("Closed", "The position was closed."))
    lines = [explanation]

    pnl = trade.get("pnl")
    r = trade.get("r_multiple")
    entry, exit_price = trade.get("entry_price"), trade.get("exit_price")
    direction = trade.get("direction", "long")

    if entry and exit_price:
        move = (exit_price / entry - 1) * 100 * (1 if direction == "long" else -1)
        lines.append(
            f"{'Long' if direction == 'long' else 'Short'} from {entry:,.2f} to "
            f"{exit_price:,.2f} — {move:+.2f}% in the direction of the trade.")

    if r is not None:
        if r >= 1:
            lines.append(
                f"Returned {r:+.2f}R: the gain was {abs(r):.2f} times the amount "
                f"budgeted as the loss. This is the shape the rules are built for.")
        elif r > 0:
            lines.append(
                f"Returned {r:+.2f}R — profitable, but less than the risk taken "
                f"to get it. A book of these does not compound.")
        elif r <= -1:
            lines.append(
                f"Lost {abs(r):.2f}R — at or beyond the planned risk. Worth "
                f"checking whether the exit filled worse than the stop level.")
        else:
            lines.append(f"Lost {abs(r):.2f}R, inside the budgeted risk.")

    held = trade.get("bars_held")
    if held is not None:
        lines.append(f"Held {held} session(s).")

    # A time exit is the one outcome that says something about the RULES rather
    # than about the market: the thesis neither worked nor failed within the
    # window the strategy allowed it.
    if reason == "time":
        lines.append(
            "Neither level was reached in the time allowed, so this says more "
            "about the holding period than about the direction.")
    return title, lines


def entry(trade):
    """One complete journal entry for a closed trade."""
    title, outcome_lines = outcome_reason(trade)
    pnl = trade.get("pnl") or 0.0
    return {
        "ticker": trade.get("ticker"),
        "direction": trade.get("direction"),
        "strategy": trade.get("strategy"),
        "regime": trade.get("regime"),
        "entry_date": trade.get("entry_date"),
        "exit_date": trade.get("exit_date"),
        "entry_price": trade.get("entry_price"),
        "exit_price": trade.get("exit_price"),
        "shares": trade.get("shares"),
        "pnl": round(pnl, 2),
        "r_multiple": trade.get("r_multiple"),
        "won": pnl > 0,
        "exit_reason": trade.get("exit_reason"),
        "outcome_title": title,
        "why_entered": entry_reason(trade),
        "measurements": _measurement_lines(trade.get("meta")),
        "why_this_outcome": outcome_lines,
        "price_source": trade.get("price_source"),
    }


def _bucket(trades, key_fn):
    out = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0.0, "r": 0.0})
    for t in trades:
        key = key_fn(t) or "unknown"
        row = out[key]
        row["trades"] += 1
        pnl = t.get("pnl") or 0.0
        row["pnl"] += pnl
        if pnl > 0:
            row["wins"] += 1
        if t.get("r_multiple") is not None:
            row["r"] += t["r_multiple"]
    result = []
    for key, row in out.items():
        result.append({
            "key": key,
            "trades": row["trades"],
            "wins": row["wins"],
            "win_rate_pct": round(row["wins"] / row["trades"] * 100, 1) if row["trades"] else None,
            "pnl": round(row["pnl"], 2),
            "avg_r": round(row["r"] / row["trades"], 2) if row["trades"] else None,
        })
    return sorted(result, key=lambda r: r["pnl"])


def breakdown(closed):
    """Where the money is made and lost, cut four ways.

    Cut by more than one dimension on purpose. "Momentum loses money" and
    "momentum loses money in sideways markets" lead to different actions, and
    only the second is worth acting on.
    """
    from ..markets import asset_class_of

    return {
        "by_strategy": _bucket(closed, lambda t: t.get("strategy")),
        "by_regime": _bucket(closed, lambda t: t.get("regime")),
        "by_exit": _bucket(closed, lambda t: t.get("exit_reason")),
        "by_segment": _bucket(closed, lambda t: asset_class_of(t.get("ticker") or "")),
    }


def observations(closed, by):
    """Statements the data supports, with the sample size attached.

    Every line carries its trade count because the honest reading of three
    trades is "we do not know yet", and a breakdown that hides the sample
    invites the reader to act on noise.
    """
    notes = []
    if not closed:
        return ["No trades have closed yet. Nothing here can be measured, and "
                "no conclusion about any strategy is available."]

    total = len(closed)
    if total < 20:
        notes.append(
            f"Only {total} closed trade(s). Everything below is provisional — "
            f"strategy-level conclusions need roughly 30+ trades each before "
            f"they mean anything.")

    worst = by["by_strategy"][0] if by["by_strategy"] else None
    best = by["by_strategy"][-1] if by["by_strategy"] else None
    if worst and best and worst["key"] != best["key"]:
        notes.append(
            f"Most of the loss sits in {worst['key']} ({worst['pnl']:+,.0f} over "
            f"{worst['trades']} trade(s)); most of the gain in {best['key']} "
            f"({best['pnl']:+,.0f} over {best['trades']}).")

    exits = {row["key"]: row for row in by["by_exit"]}
    time_exits = exits.get("time")
    if time_exits and time_exits["trades"] >= 3:
        share = time_exits["trades"] / total * 100
        notes.append(
            f"{time_exits['trades']} of {total} trades ({share:.0f}%) ended on the "
            f"holding-period limit rather than at a stop or target. A high share "
            f"here points at the holding period, not the entry rules.")

    stops = exits.get("stop")
    targets = exits.get("target")
    if stops and targets and stops["trades"] and targets["trades"]:
        notes.append(
            f"{targets['trades']} target(s) against {stops['trades']} stop(s). "
            f"With reward:risk set above 1:1, the rules do not need to be right "
            f"more often than they are wrong — they need the winners to be bigger.")
    return notes


def report(book):
    """The full trade journal for one book."""
    closed = list(book.closed)
    entries = [entry(t) for t in closed][::-1]      # newest first
    by = breakdown(closed)
    realised = sum((t.get("pnl") or 0.0) for t in closed)
    wins = [t for t in closed if (t.get("pnl") or 0) > 0]
    losses = [t for t in closed if (t.get("pnl") or 0) < 0]

    return {
        "entries": entries,
        "breakdown": by,
        "observations": observations(closed, by),
        "totals": {
            "closed": len(closed),
            "realised_pnl": round(realised, 2),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate_pct": round(len(wins) / len(closed) * 100, 1) if closed else None,
            "avg_win": round(sum(t["pnl"] for t in wins) / len(wins), 2) if wins else None,
            "avg_loss": round(sum(t["pnl"] for t in losses) / len(losses), 2) if losses else None,
            "profit_factor": (
                round(sum(t["pnl"] for t in wins) / abs(sum(t["pnl"] for t in losses)), 2)
                if losses and sum(t["pnl"] for t in losses) else None),
            "base_currency": book.base_currency,
        },
    }
