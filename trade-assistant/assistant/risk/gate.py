"""Risk Gate — deterministic checks, all normalized to the base currency.

Verdicts (the human always makes the final call):
  approved_for_review  — passed all hard rules
  needs_more_research  — soft flags (correlation, thin conviction, data gaps)
  rejected             — breaks a hard rule, or no setup exists

Dependencies are injected (fx rates dict, optional price-history fetcher,
optional borrow check) so every check is unit-testable without network access.

Shorts get their own extra rules. A long can only lose what you put in; a short
has no such ceiling, can be closed against you by a borrow recall, and costs a
borrow fee while you hold it. So shorts must have a stop, must be verified as
borrowable, and are held to tighter exposure caps than longs.
"""
from ..core import fx
from . import borrow

SHORT_RULE_DEFAULTS = {
    "require_stop": True,
    "block_if_borrow_unknown": False,
    "max_short_position_pct": 8.0,
    "max_total_short_exposure_pct": 25.0,
    "max_open_shorts": 3,
    "crowding_flag_pct": 10.0,
}


def short_rules(config):
    return {**SHORT_RULE_DEFAULTS, **((config or {}).get("short_rules") or {})}


def evaluate(snapshot, thesis, plan, config, fx_rates, price_history_fn=None,
             shortability=None):
    account = config.get("account", {})
    positions = config.get("positions", [])
    base_ccy = config.get("base_currency", "USD")
    portfolio_value = account.get("portfolio_value", 0) or 1

    hard, soft = [], []

    if plan is None:
        return {
            "verdict": "rejected",
            "hard_failures": ["No trade plan: thesis does not support a defined-risk setup."],
            "soft_flags": [],
            "exposure": exposure_summary(positions, portfolio_value, None, base_ccy, fx_rates),
        }

    plan_value_base = _to_base(plan["position_value"], plan.get("currency", base_ccy),
                               base_ccy, fx_rates, soft)
    plan_risk_base = _to_base(plan["risk_amount"], plan.get("currency", base_ccy),
                              base_ccy, fx_rates, soft)

    # 1. Single-position cap
    max_pos_pct = account.get("max_position_pct", 15.0)
    pos_pct = plan_value_base / portfolio_value * 100
    if pos_pct > max_pos_pct:
        hard.append(f"Position would be {pos_pct:.1f}% of portfolio (cap {max_pos_pct}%)")

    # 2. Total exposure after adding this position
    exposure = exposure_summary(positions, portfolio_value, plan_value_base, base_ccy, fx_rates)
    max_total = account.get("max_total_exposure_pct", 60.0)
    if exposure["total_after_pct"] > max_total:
        hard.append(f"Total exposure would reach {exposure['total_after_pct']:.1f}% (cap {max_total}%)")

    # 3. Max potential loss vs configured risk budget (in base currency)
    risk_budget = portfolio_value * account.get("risk_per_trade_pct", 1.0) / 100
    if plan_risk_base > risk_budget * 1.05:  # small rounding/FX allowance
        hard.append(f"Max loss {plan_risk_base:.0f} {base_ccy} exceeds risk budget "
                    f"{risk_budget:.0f} {base_ccy}")

    # 4. Open-position count
    max_open = account.get("max_open_positions", 8)
    if len(positions) + 1 > max_open:
        hard.append(f"Would exceed max open positions ({max_open})")

    # 5. Sector concentration
    sector = (snapshot.get("_fundamentals") or {}).get("sector")
    if sector:
        max_sector = account.get("max_sector_exposure_pct", 25.0)
        unknown_sector = sum(1 for p in positions if not p.get("sector"))
        if unknown_sector:
            soft.append(
                f"{unknown_sector} existing position(s) have no sector data "
                "(IBKR doesn't supply it) — sector concentration may be understated")
        sector_value = sum(
            _position_value_base(p, base_ccy, fx_rates, soft)
            for p in positions if p.get("sector") == sector
        ) + plan_value_base
        sector_pct = sector_value / portfolio_value * 100
        if sector_pct > max_sector:
            hard.append(f"{sector} exposure would reach {sector_pct:.1f}% (cap {max_sector}%)")
        elif sector_pct > max_sector * 0.8:
            soft.append(f"{sector} exposure approaching cap ({sector_pct:.1f}% of {max_sector}%)")

    # 6. Return correlation with existing positions (60 trading days)
    if price_history_fn is not None:
        threshold = account.get("correlation_flag_threshold", 0.75)
        for p in positions:
            corr = return_correlation(snapshot["ticker"], p.get("ticker"), price_history_fn)
            if corr is not None and corr >= threshold:
                soft.append(f"High correlation with existing {p['ticker']} position "
                            f"({corr:.2f} over 60 days) — doubling the same bet?")

    # 7. Short-selling rules — additional to everything above, never instead of it.
    if plan.get("direction") == "short":
        _short_checks(snapshot, plan, config, positions, portfolio_value,
                      plan_value_base, base_ccy, fx_rates, shortability, hard, soft)

    # 8. Strategy vs thesis disagreement. The strategy decides the setup; the
    #    LLM's read is a second opinion, so a clash is a flag, not a veto.
    strategy_direction = plan.get("direction")
    thesis_bias = thesis.get("net_bias")
    if strategy_direction and thesis_bias in ("bullish", "bearish"):
        expected = "long" if thesis_bias == "bullish" else "short"
        if expected != strategy_direction:
            soft.append(
                f"The {plan.get('strategy_label') or 'strategy'} setup is {strategy_direction}, "
                f"but the written thesis reads {thesis_bias} — the technical setup and the "
                "fundamental story disagree, so read both before deciding")
    if plan.get("strategy") and thesis.get("supports_setup") is False:
        soft.append("The thesis engine did not think the fundamentals support a setup here — "
                    "this idea comes from the price action alone")

    # 9. Conviction / data-quality soft checks
    if plan.get("sized_down_to_cap"):
        soft.append(
            f"Position sized down to your {max_pos_pct}% cap: {plan['shares']} shares "
            f"instead of {plan.get('shares_by_risk_alone')} — risking "
            f"{plan.get('risk_budget_used_pct')}% of your risk budget, not the full amount")
    if plan["confidence"] < 55:
        soft.append(f"Confidence score {plan['confidence']}/100 is thin")
    if thesis.get("source") == "rule-based":
        soft.append("Thesis is rule-based only — AI/news review not performed")
    if thesis.get("ai_error"):
        soft.append(f"AI thesis failed, fell back to rules: {thesis['ai_error']}")
    for gap in thesis.get("data_gaps", []) or []:
        soft.append(f"Data gap: {gap}")

    verdict = ("rejected" if hard
               else "needs_more_research" if soft
               else "approved_for_review")
    return {"verdict": verdict, "hard_failures": hard, "soft_flags": soft, "exposure": exposure}


def _short_checks(snapshot, plan, config, positions, portfolio_value,
                  plan_value_base, base_ccy, fx_rates, shortability, hard, soft):
    """The extra bar a short has to clear. Every threshold is configurable, but
    "no stop" and "confirmed not borrowable" are always hard failures — an
    uncapped short is exactly the thing this tool must never wave through."""
    rules = short_rules(config)

    # 1. Mandatory stop. Without one the max loss is unbounded and every number
    #    downstream (risk amount, position size, exposure) is fiction.
    stop, entry = plan.get("stop"), plan.get("entry")
    if rules["require_stop"]:
        if not stop:
            hard.append("Short with no stop: unlimited theoretical loss. A short "
                        "must define its invalidation level.")
        elif entry and stop <= entry:
            hard.append(f"Short stop {stop} is not above the entry {entry} — that is not "
                        "a protective stop, it would trigger immediately.")

    # 2. Borrow availability.
    status = (shortability or {}).get("status", borrow.UNKNOWN)
    detail = (shortability or {}).get("detail", "")
    source = (shortability or {}).get("source", "")
    if status == borrow.NO:
        hard.append(f"Not available to short: {detail} (source: {source})")
    elif status == borrow.UNKNOWN:
        message = f"Borrow availability unverified. {detail}".strip()
        if rules["block_if_borrow_unknown"]:
            hard.append(message + " Your config blocks shorts that cannot be verified.")
        else:
            soft.append(message)
    else:
        soft.append(f"Borrow confirmed available ({source}). Borrow fees still apply "
                    "and can be recalled at any time.")

    # 3. Short crowding — not a blocker, but the squeeze risk you are taking on.
    crowd = (shortability or {}).get("crowding") or {}
    pct = crowd.get("short_percent_of_float")
    if pct is not None and pct >= rules["crowding_flag_pct"]:
        soft.append(f"{pct:.1f}% of the float is already sold short — crowded trade: "
                    "expect expensive borrow and squeeze risk against your stop")

    # 4. Tighter caps than a long gets.
    short_pos_pct = plan_value_base / portfolio_value * 100
    if short_pos_pct > rules["max_short_position_pct"]:
        hard.append(f"Short would be {short_pos_pct:.1f}% of portfolio "
                    f"(short cap {rules['max_short_position_pct']}%, tighter than the "
                    "long cap on purpose)")

    existing_shorts = [p for p in positions if float(p.get("shares", 0) or 0) < 0]
    scratch = []
    short_value = sum(_position_value_base(p, base_ccy, fx_rates, scratch)
                      for p in existing_shorts)
    total_short_pct = (short_value + plan_value_base) / portfolio_value * 100
    if total_short_pct > rules["max_total_short_exposure_pct"]:
        hard.append(f"Total short exposure would reach {total_short_pct:.1f}% "
                    f"(cap {rules['max_total_short_exposure_pct']}%)")

    if len(existing_shorts) + 1 > rules["max_open_shorts"]:
        hard.append(f"Would exceed max open shorts ({rules['max_open_shorts']}); "
                    f"you already hold {len(existing_shorts)}")


def _to_base(amount, ccy, base_ccy, fx_rates, soft_flags):
    try:
        return fx.convert(amount, ccy, base_ccy, fx_rates)
    except fx.MissingRateError:
        soft_flags.append(f"No FX rate for {ccy}->{base_ccy}; treating amount as {base_ccy} 1:1")
        return float(amount)


def _position_value_base(position, base_ccy, fx_rates, soft_flags):
    """GROSS exposure of a position in the base currency.

    abs() is essential: IBKR reports shorts as negative share counts, and a
    signed sum would let a short cancel out a long, understating risk and
    letting the gate approve far more gross exposure than the caps intend.
    Uses the current mark when available, falling back to cost basis.
    """
    price = position.get("mark_price") or position.get("entry_price", 0)
    value = abs(position.get("shares", 0)) * abs(price)
    return _to_base(value, position.get("currency", base_ccy), base_ccy, fx_rates, soft_flags)


def exposure_summary(positions, portfolio_value, plan_value_base, base_ccy, fx_rates):
    scratch = []
    current = sum(_position_value_base(p, base_ccy, fx_rates, scratch) for p in positions)
    added = plan_value_base or 0
    return {
        "base_currency": base_ccy,
        "current_value": round(current, 2),
        "current_pct": round(current / portfolio_value * 100, 2),
        "total_after_pct": round((current + added) / portfolio_value * 100, 2),
    }


def return_correlation(ticker_a, ticker_b, price_history_fn, days=60):
    if not ticker_b or ticker_a == ticker_b:
        return 1.0 if ticker_a == ticker_b else None
    try:
        a, b = price_history_fn(ticker_a), price_history_fn(ticker_b)
        if a is None or b is None:
            return None
        ra = a["Close"].pct_change().dropna().tail(days)
        rb = b["Close"].pct_change().dropna().tail(days)
        joined = ra.to_frame("a").join(rb.to_frame("b"), how="inner").dropna()
        if len(joined) < 20:
            return None
        return float(joined["a"].corr(joined["b"]))
    except Exception:
        return None
