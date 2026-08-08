"""Trade Plan Builder — ALL of it deterministic, pure, and unit-tested.

The LLM never touches these numbers. Prices are in the instrument's currency;
the risk budget arrives already converted to that currency by the caller
(pipeline), so every function here is currency-agnostic arithmetic.
"""
import math

REWARD_MULTIPLE = 2.5     # target = entry +/- 2.5R
ATR_STOP_MULTIPLE = 1.5   # fallback stop distance
STRUCTURE_BUFFER = 0.25   # stop sits 0.25*ATR beyond the 20d extreme


def position_size(risk_budget, risk_per_share, max_shares=None):
    """Whole shares satisfying BOTH constraints:

      * shares * risk_per_share <= risk_budget   (max loss)
      * shares                  <= max_shares    (concentration cap, in SHARES)

    max_shares is a SHARE COUNT, not a currency amount. It was previously named
    max_position_value, which promised a cash figure the implementation never
    honoured — it compared the value against shares. The only caller divided by
    the entry price first, so the behaviour was right and the name was wrong;
    the next caller to trust the name would have got a position orders of
    magnitude too large. Use position_size_by_value() if you have a cash cap.

    Taking the smaller of the two is standard practice. Sizing on the risk
    budget alone means a tight stop produces a huge share count, so the position
    breaches the concentration cap and the idea gets thrown out entirely — a
    good setup with a tight stop could never be traded. Sizing down instead
    keeps both rules satisfied; you simply risk less than the full budget.
    """
    if risk_per_share <= 0 or risk_budget <= 0:
        return 0
    by_risk = math.floor(risk_budget / risk_per_share)
    if max_shares is None:
        return by_risk
    return min(by_risk, max(0, math.floor(max_shares)))


def position_size_by_value(risk_budget, risk_per_share, max_position_value, entry_price):
    """position_size() with the concentration cap expressed in CASH.

    The explicit entry_price is the point: converting a cash cap to a share cap
    requires it, and making the caller pass it is what stops the two units
    being confused again.
    """
    if entry_price is None or entry_price <= 0:
        raise ValueError("entry_price must be positive to convert a cash cap to shares")
    if max_position_value is None:
        return position_size(risk_budget, risk_per_share)
    return position_size(risk_budget, risk_per_share,
                         max_shares=max_position_value / entry_price)


def long_levels(price, atr, sma20=None, prior_low=None):
    """Entry zone / stop / target for a long setup. Stop = the tightest level
    that still sits under structure (20-day low), else 1.5*ATR.
    The 20-day MA only counts as a pullback level when it sits BELOW price."""
    support = sma20 if (sma20 and sma20 < price) else price - atr
    entry_low = round(max(price - atr, support), 2)
    entry_high = round(price, 2)
    entry = round((entry_low + entry_high) / 2, 2)
    candidates = [entry - ATR_STOP_MULTIPLE * atr]
    if prior_low:
        candidates.append(prior_low - STRUCTURE_BUFFER * atr)
    stop = round(max(candidates), 2)
    if stop >= entry:
        stop = round(entry - ATR_STOP_MULTIPLE * atr, 2)
    risk_per_share = round(entry - stop, 2)
    target = round(entry + REWARD_MULTIPLE * risk_per_share, 2)
    return {"entry_zone": [entry_low, entry_high], "entry": entry, "stop": stop,
            "target": target, "risk_per_share": risk_per_share}


def short_levels(price, atr, sma20=None, prior_high=None):
    """Mirror of long_levels for a short setup.
    The 20-day MA only counts as a bounce level when it sits ABOVE price."""
    resistance = sma20 if (sma20 and sma20 > price) else price + atr
    entry_low = round(price, 2)
    entry_high = round(min(price + atr, resistance), 2)
    entry = round((entry_low + entry_high) / 2, 2)
    candidates = [entry + ATR_STOP_MULTIPLE * atr]
    if prior_high:
        candidates.append(prior_high + STRUCTURE_BUFFER * atr)
    stop = round(min(candidates), 2)
    if stop <= entry:
        stop = round(entry + ATR_STOP_MULTIPLE * atr, 2)
    risk_per_share = round(stop - entry, 2)
    target = round(entry - REWARD_MULTIPLE * risk_per_share, 2)
    return {"entry_zone": [entry_low, entry_high], "entry": entry, "stop": stop,
            "target": target, "risk_per_share": risk_per_share}


def confidence_score(conviction, signal_count, direction_aligned, reward_risk,
                     thesis_source):
    """Transparent rubric, 0-100: thesis conviction 50%, technical alignment 30%,
    reward:risk 20%. Rule-based theses are capped at 60."""
    reasons = []
    conviction = max(0, min(100, conviction))
    reasons.append(f"Thesis conviction {conviction}/100 ({thesis_source})")

    tech = min(100, signal_count * 25 + (25 if direction_aligned else 0))
    reasons.append(f"Technicals: {signal_count} signal(s), "
                   + ("aligned with thesis bias" if direction_aligned else "not fully aligned"))

    rr_score = max(0, min(100, (reward_risk or 0) / 3 * 100))
    reasons.append(f"Reward:risk {reward_risk}:1")

    score = round(conviction * 0.5 + tech * 0.3 + rr_score * 0.2)
    if thesis_source == "rule-based":
        score = min(score, 60)
        reasons.append("Capped at 60: rule-based thesis (no AI review of news/fundamentals)")
    return score, reasons


def levels_from_strategy(idea):
    """Adopt a strategy's own entry/stop/target verbatim.

    The strategy knows things the generic ATR formula cannot: a range trade
    targets the far side of its band, a mean-reversion trade targets the 20-day
    average. Overriding those with a blanket 2.5R target would throw away the
    reason the setup existed. The levels were already validated in
    strategies/base.py, so this is a straight translation, not a recalculation.
    """
    entry = idea.get("entry")
    return {
        "entry_zone": idea.get("entry_zone") or [entry, entry],
        "entry": entry,
        "stop": idea.get("stop"),
        "target": idea.get("target"),
        "risk_per_share": idea.get("risk_per_share"),
    }


def build_plan(snapshot, thesis, risk_budget_instrument_ccy, instrument_ccy="USD",
               max_position_value=None, strategy_idea=None):
    """Full plan dict, or None when no defined-risk setup exists.

    max_position_value: the concentration cap in the instrument's currency.
    When supplied, the position is sized down to respect it rather than being
    built oversized and rejected downstream.

    strategy_idea: a StrategyIdea dict. When present the strategy decides the
    direction and the levels — it is the component that identified the setup —
    and the thesis becomes a second opinion the risk gate flags on disagreement
    rather than a veto. Without one, the original thesis-driven behaviour is
    used unchanged.
    """
    if strategy_idea:
        if strategy_idea.get("status") != "actionable":
            return None      # a watch item is a heads-up, never a position
        direction = strategy_idea.get("direction")
        if direction not in ("long", "short"):
            return None
        levels = levels_from_strategy(strategy_idea)
        if not all(levels.get(k) for k in ("entry", "stop", "target", "risk_per_share")):
            return None
    else:
        if not thesis.get("supports_setup"):
            return None
        price, atr = snapshot.get("price"), snapshot.get("atr")
        if not price or not atr:
            return None

        bias = thesis.get("net_bias")
        if bias == "bullish":
            direction = "long"
            levels = long_levels(price, atr, snapshot.get("sma20"),
                                 snapshot.get("prior_low_20d"))
        elif bias == "bearish":
            direction = "short"
            levels = short_levels(price, atr, snapshot.get("sma20"),
                                  snapshot.get("prior_high_20d"))
        else:
            return None

    if levels["risk_per_share"] <= 0:
        return None

    shares_by_risk = position_size(risk_budget_instrument_ccy, levels["risk_per_share"])
    # max_position_value is a cash cap, so the conversion to shares is explicit.
    shares = position_size_by_value(risk_budget_instrument_ccy, levels["risk_per_share"],
                                    max_position_value, levels["entry"])
    if shares < 1:
        return None
    sized_down = shares < shares_by_risk

    reward_risk = round(abs(levels["target"] - levels["entry"]) / levels["risk_per_share"], 2)

    # Alignment means different things depending on where the setup came from.
    # From a strategy: does the written thesis agree with the direction the
    # rules produced? From the thesis alone: does the tape agree with it?
    thesis_bias = thesis.get("net_bias")
    if strategy_idea:
        expected = "bullish" if direction == "long" else "bearish"
        aligned = thesis_bias == expected
        evidence_count = len(strategy_idea.get("reasons") or [])
    else:
        aligned = snapshot.get("direction_hint") == thesis_bias
        evidence_count = len(snapshot.get("signals", []))

    score, reasons = confidence_score(
        thesis.get("conviction", 50), evidence_count,
        aligned, reward_risk, thesis.get("source", "unknown"))

    risk_amount = round(shares * levels["risk_per_share"], 2)
    budget_used_pct = (round(risk_amount / risk_budget_instrument_ccy * 100, 1)
                       if risk_budget_instrument_ccy else None)

    plan = {
        "direction": direction,
        "currency": instrument_ccy,
        **levels,
        "reward_risk": reward_risk,
        "shares": shares,
        "position_value": round(shares * levels["entry"], 2),
        "risk_amount": risk_amount,
        "sized_down_to_cap": sized_down,
        "shares_by_risk_alone": shares_by_risk,
        "risk_budget_used_pct": budget_used_pct,
        "confidence": score,
        "confidence_reasons": reasons,
        "note": "Invalidation: the thesis is wrong if price closes beyond the stop — exit, don't average down.",
    }
    if strategy_idea:
        plan.update({
            "strategy": strategy_idea.get("strategy"),
            "strategy_label": strategy_idea.get("strategy_label"),
            "regime": strategy_idea.get("regime"),
            "setup_headline": strategy_idea.get("headline"),
            "setup_reasons": strategy_idea.get("reasons") or [],
            "levels_source": "strategy",
        })
    else:
        plan["levels_source"] = "thesis"
    return plan
