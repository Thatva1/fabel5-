"""One answer: buy it, sell it, or leave it alone — and why.

Everything needed to reach that answer already existed and was scattered across
five objects. A strategy decided whether a setup fired, the regime classifier
decided whether the conditions suited it, the LLM wrote a bull case and a bear
case, the sizer built a plan, and the risk gate passed or refused it. All five
landed on the dashboard side by side, and the reader was left to combine them.
So the most common question a person actually has — should I do anything about
this — was the one thing the tool never said out loud.

WHAT DECIDES, AND WHAT ONLY DESCRIBES

The action is derived from the deterministic layers: the strategy rules and the
risk gate. The LLM's thesis contributes the pros and cons a human reads, and it
can lower confidence or raise a conflict, but it CANNOT turn an AVOID into a
BUY. That division is the same one the rest of this project already keeps — the
strategies decide what to flag, the model only narrates — and it matters more
here than anywhere else, because this module's output is the one a tired person
will act on without reading the five panels underneath it.

The verdict is a reading of rules the user configured, on data they can inspect.
It is not advice, and it is not a forecast. Every branch below is reachable by
reading the code; nothing here is a black box, which is the property that makes
it worth trusting at all.
"""

# The five answers. Deliberately few — a scale with nine gradations invites the
# reader to find the one that agrees with what they already wanted to do.
BUY = "BUY"
SELL = "SELL"
HOLD = "HOLD"
WAIT = "WAIT"
AVOID = "AVOID"

ACTION_MEANING = {
    BUY: "The rules fired a long setup and the risk gate permitted it.",
    SELL: "Close or short this — either an exit condition has triggered or a "
          "short setup fired and passed the gate.",
    HOLD: "Already held, and nothing has triggered an exit.",
    WAIT: "A setup exists but something unresolved is in the way. Not actionable yet.",
    AVOID: "No tradable setup, or the risk gate refused it. Nothing to do.",
}


def _news_brief(context, limit=3):
    """A few recent headlines, as context for a human.

    Context and nothing more. No branch below reads this, and that is
    deliberate: a headline becomes a trading signal only once something has
    measured that its arrival predicts a return, and nothing in this project has
    measured that. Letting news tip the action would be inventing an edge.
    """
    items = ((context or {}).get("news") or {}).get("headlines") or []
    brief = []
    for item in items[:limit]:
        brief.append({
            "headline": item.get("headline") or item.get("title") or "",
            "source": item.get("source"),
            "date": (item.get("datetime") or item.get("date") or "")[:10],
            "url": item.get("url"),
        })
    return [b for b in brief if b["headline"]]


def _confidence(strategy_idea, thesis, gate_result, conflict):
    """How much the layers agree with each other.

    Agreement, not strength. A high-conviction thesis pointing the opposite way
    from the setup that fired is LESS trustworthy than a quiet one pointing the
    same way, because the disagreement is information about the setup rather
    than about the thesis.
    """
    soft = len((gate_result or {}).get("soft_flags") or [])
    conviction = (thesis or {}).get("conviction") or 0

    if conflict:
        return "low"
    if soft >= 3:
        return "low"
    if conviction >= 7 and soft == 0:
        return "high"
    if conviction >= 4 and soft <= 1:
        return "medium"
    return "low"


def _direction_of(strategy_idea):
    return (strategy_idea or {}).get("direction")


def _conflict(direction, thesis):
    """Does the written thesis point against the setup that fired?

    Surfaced rather than resolved. The rules and the narrative disagreeing is a
    real state of the world and the honest thing to do is say so — silently
    picking a winner would hide exactly the case a human should look at.
    """
    bias = (thesis or {}).get("net_bias")
    if not direction or not bias or bias == "neutral":
        return None
    if direction == "long" and bias == "bearish":
        return ("The setup is long but the written thesis reads bearish. "
                "The rules and the narrative disagree.")
    if direction == "short" and bias == "bullish":
        return ("The setup is short but the written thesis reads bullish. "
                "The rules and the narrative disagree.")
    return None


def for_idea(analysis, config=None):
    """Turn one pipeline result into a single action with its reasoning.

    `analysis` is what pipeline.analyze_ticker returns.
    """
    ticker = analysis.get("ticker")
    thesis = analysis.get("thesis") or {}
    gate_result = analysis.get("gate") or {}
    plan = analysis.get("plan")
    strategy_idea = analysis.get("strategy_idea") or {}
    context = analysis.get("context")
    snapshot = analysis.get("snapshot") or {}

    direction = _direction_of(strategy_idea)
    conflict = _conflict(direction, thesis)
    hard = list(gate_result.get("hard_failures") or [])
    soft = list(gate_result.get("soft_flags") or [])
    verdict = gate_result.get("verdict")

    pros = list(thesis.get("bull_case") or [])
    cons = list(thesis.get("bear_case") or [])
    if direction == "short":
        # For a short the bear case IS the argument for the trade. Presenting it
        # under "cons" would invert the whole panel.
        pros, cons = cons, pros

    because = []
    if strategy_idea.get("headline"):
        because.append(strategy_idea["headline"])
    if snapshot.get("regime_label"):
        because.append(f"Regime: {snapshot['regime_label']}")

    # --- the decision ----------------------------------------------------
    #
    # Hard failures are tested BEFORE "is there a setup", because they are the
    # more specific answer. An idea can reach here with no strategy attached —
    # every idea journalled before the strategy library existed is shaped that
    # way — and reporting those as "no setup fired" throws away the gate's
    # actual reason for refusing, which is the thing worth reading.
    has_setup = bool(strategy_idea) and strategy_idea.get("status") == "actionable" \
        and bool(direction)

    if hard:
        action = AVOID
        subject = f"this {direction} setup on {ticker}" if has_setup else ticker
        headline = f"Risk gate refused {subject}."
        because = hard + because
    elif not has_setup:
        action = AVOID
        headline = f"No setup on {ticker}."
        # The gate's soft flags carry the specific reason — "watch item only, no
        # direction yet" tells the reader far more than the generic sentence,
        # and without them a squeeze being monitored looks identical to an
        # instrument nothing has ever looked at.
        because = list(soft) or [
            "No strategy rule fired a directional setup on this instrument."]
        if snapshot.get("regime_label"):
            because.append(f"Regime: {snapshot['regime_label']}")
    elif verdict == "needs_more_research":
        action = WAIT
        headline = f"A {direction} setup fired on {ticker}, but it is not clear to trade."
        because = soft + because
    elif direction == "long":
        action = BUY
        headline = f"Long setup on {ticker}, permitted by the risk gate."
    else:
        action = SELL
        headline = f"Short setup on {ticker}, permitted by the risk gate."

    if conflict:
        cons = [conflict] + cons

    return {
        "ticker": ticker,
        "action": action,
        "meaning": ACTION_MEANING[action],
        "headline": headline,
        "confidence": _confidence(strategy_idea, thesis, gate_result, conflict),
        "direction": direction,
        "because": because,
        "pros": pros[:5],
        "cons": cons[:5],
        "conflict": conflict,
        "blockers": hard,
        "warnings": soft,
        "news": _news_brief(context),
        "thesis_summary": thesis.get("thesis_summary"),
        "thesis_source": thesis.get("source"),
        "strategy": strategy_idea.get("strategy"),
        "plan": plan,
        "gate_verdict": verdict,
    }


def for_position(position, bar=None, config=None):
    """Should an OPEN position be closed?

    Separate from for_idea because the question genuinely is different. An entry
    asks "is this worth risking capital on"; an exit asks "has what I planned
    for already happened". The exit levels were fixed when the position was
    opened, so this reads them rather than re-deciding them — re-running the
    entry logic on a held position is how a stop gets talked out of being hit.
    """
    ticker = position.get("ticker")
    direction = position.get("direction", "long")
    last = position.get("last_price")
    stop, target = position.get("stop"), position.get("target")
    entry = position.get("entry_price")

    pros, cons, because = [], [], []
    action, headline = HOLD, f"Hold {ticker} — no exit condition met."

    if last is None:
        return {"ticker": ticker, "action": WAIT,
                "meaning": ACTION_MEANING[WAIT],
                "headline": f"{ticker} has no current price, so no exit decision "
                            f"can be made.",
                "confidence": "low", "direction": direction,
                "because": ["The last mark failed — this position's value is unknown."],
                "pros": [], "cons": [], "news": [], "blockers": [], "warnings": []}

    move_pct = ((last / entry - 1) * 100 * (1 if direction == "long" else -1)
                if entry else 0.0)

    hit_stop = (last <= stop) if direction == "long" else (last >= stop)
    hit_target = (last >= target) if direction == "long" else (last <= target)

    if hit_stop:
        action = SELL
        headline = f"{ticker} has hit its stop ({stop:.2f}). Close it."
        because = [f"Price {last:.2f} is through the stop at {stop:.2f}.",
                   "The stop was set when the position was opened; moving it now "
                   "converts a planned loss into an unplanned one."]
    elif hit_target:
        action = SELL
        headline = f"{ticker} has reached its target ({target:.2f}). Take the profit."
        because = [f"Price {last:.2f} has reached the target at {target:.2f}."]
    else:
        bars_held = position.get("bars_held") or 0
        max_bars = ((config or {}).get("paper") or {}).get("max_holding_bars", 10)
        because = [f"{move_pct:+.1f}% since entry at {entry:.2f}.",
                   f"Stop {stop:.2f} · target {target:.2f} · held {bars_held} bar(s)."]
        if bars_held >= max_bars:
            action = SELL
            headline = (f"{ticker} has been held {bars_held} bars, at or past the "
                        f"{max_bars}-bar limit. Time exit.")
            because.insert(0, "The holding-period limit has been reached.")

    # A position priced by something other than the licensed feed, or marked
    # against an old session, is a position whose exit test just ran on a number
    # that may not be current. Say so next to the answer.
    if position.get("mark_failed"):
        cons.append("This position could not be priced at the last run — the "
                    "exit test above ran on a stale mark.")
    source = position.get("price_source")
    if source and source != "ibkr":
        cons.append(f"Priced by {source}, not the licensed IBKR feed.")

    return {
        "ticker": ticker, "action": action, "meaning": ACTION_MEANING[action],
        "headline": headline, "confidence": "high" if action == SELL else "medium",
        "direction": direction, "because": because, "pros": pros, "cons": cons,
        "news": [], "blockers": [], "warnings": [],
        "move_pct": round(move_pct, 2),
        "strategy": position.get("strategy"),
        "price_source": source, "bar_date": position.get("bar_date"),
    }


def summarise(verdicts):
    """Counts by action, for a dashboard header."""
    counts = {}
    for item in verdicts:
        counts[item["action"]] = counts.get(item["action"], 0) + 1
    return {"counts": counts, "total": len(verdicts)}
