"""Realised profit and loss — pure, deterministic, unit-tested arithmetic.

No LLM touches these numbers, and neither does anything with a network
connection: every function here takes plain floats and returns plain floats, so
the P&L in your journal is auditable rather than asserted.

Two figures are produced for every closed idea, and they answer different
questions:

  * CASH P&L tells you what actually happened to the account.
  * R-MULTIPLE tells you whether the STRATEGY was any good. R is the profit
    divided by the risk you originally took, so +2R means "made twice what it
    risked" regardless of whether the position was £500 or £50,000. That is the
    only fair way to compare a strategy that sizes small against one that sizes
    large — comparing their cash totals mostly compares their position sizes.

Expectancy (average R across closed trades) is the number that decides whether
a strategy stays in the library. Positive expectancy with a 40% hit rate beats
negative expectancy with a 70% hit rate, every time.
"""


def realised_pnl(direction, entry, exit_price, shares):
    """Cash profit or loss in the INSTRUMENT's currency.

    A short profits when the exit is below the entry, which is why direction
    flips the sign rather than the arithmetic being shared. Returns None when
    any input is missing — an unknown P&L must stay unknown, never default to 0,
    or the journal quietly reports losing trades as breakeven.
    """
    if direction not in ("long", "short"):
        return None
    if entry is None or exit_price is None or shares is None:
        return None
    try:
        entry, exit_price, shares = float(entry), float(exit_price), float(shares)
    except (TypeError, ValueError):
        return None
    per_share = (exit_price - entry) if direction == "long" else (entry - exit_price)
    return round(per_share * abs(shares), 2)


def r_multiple(direction, entry, exit_price, stop):
    """Profit as a multiple of the risk originally taken.

    Stopping out exactly at the planned stop is -1.0R by definition. Anything
    worse than -1R means the stop did not hold (a gap, or it was not honoured),
    which is worth seeing explicitly rather than clipping to -1.
    """
    if direction not in ("long", "short"):
        return None
    if entry is None or exit_price is None or stop is None:
        return None
    try:
        entry, exit_price, stop = float(entry), float(exit_price), float(stop)
    except (TypeError, ValueError):
        return None
    risk_per_share = abs(entry - stop)
    if risk_per_share <= 0:
        return None
    per_share = (exit_price - entry) if direction == "long" else (entry - exit_price)
    return round(per_share / risk_per_share, 2)


def close_out(plan, exit_price, entry_override=None):
    """Everything derivable at close time, from the plan and one exit price.

    entry_override lets you record the price you were ACTUALLY filled at when it
    differed from the plan. The planned entry is the default because that is
    what the journal recorded, but a strategy judged on fills it never got is
    being judged on fiction.

    Returns {} when the idea had no plan — a watch item never had a position.
    """
    if not plan:
        return {}
    direction = plan.get("direction")
    entry = entry_override if entry_override is not None else plan.get("entry")
    shares = plan.get("shares")
    stop = plan.get("stop")

    return {
        "direction": direction,
        "entry_used": entry,
        "exit_price": exit_price,
        "shares": shares,
        "currency": plan.get("currency"),
        "pnl_instrument": realised_pnl(direction, entry, exit_price, shares),
        "r_multiple": r_multiple(direction, entry, exit_price, stop),
    }


def expectancy(r_multiples):
    """Average R across closed trades — the single number that says whether a
    strategy is worth keeping. None when there is nothing to average."""
    values = [r for r in r_multiples if r is not None]
    if not values:
        return None
    return round(sum(values) / len(values), 2)
