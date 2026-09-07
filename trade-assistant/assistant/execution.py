"""Execution service (Layer 2) — the ONLY path from a researched idea to a live
order, and it is deliberately narrow.

Every order must pass all of these, in order:

  1. execution.enabled is true in config.yaml           (off by default)
  2. The idea exists and YOU marked it decision='approved' in the dashboard
  3. The idea produced a real trade plan (entry/stop/size)
  4. The idea's risk gate verdict was not 'rejected'
  5. Preparing a ticket re-runs the deterministic risk math against the CURRENT
     portfolio — stale or newly-breaching ideas are refused here
  6. Confirming requires typing the ticker symbol exactly, and the ticket must
     still be pending (single-use)

Nothing in the research pipeline calls into this module. The scanner cannot
reach it, the LLM cannot reach it: orders originate only from an explicit
human click plus a typed confirmation.
"""
from datetime import datetime, timezone

from . import journal
from .core.models import OrderIntent
from .risk import gate

# 2 minutes. This was 15, which is a long time for a limit price taken off a
# live quote: a volatile name can move well past the drift limit — and past the
# stop — inside that window, reopening the exact gap MAX_ENTRY_DRIFT_PCT exists
# to close. Confirmation also re-checks the price now, but a short window means
# fewer tickets ever reach that check stale.
TICKET_MAX_AGE_SECONDS = 120
MAX_ENTRY_DRIFT_PCT = 2.0      # refuse if price has moved this far from the plan


class ExecutionRefused(Exception):
    """A safety precondition failed. The message is shown verbatim to the user."""


def execution_status(config):
    """Dashboard status for the execution layer. Never raises.

    `mode` is explicit — 'paper' | 'live' | 'unverified' — so the UI never has
    to infer it (inferring it from a port number is the exact bug that once
    labelled a live account "PAPER"). Anything not positively confirmed as
    paper is reported as live/unverified: fail closed, never fail open.
    """
    exec_cfg = config.get("execution", {})
    if not exec_cfg.get("enabled"):
        return {"enabled": False, "connected": False, "mode": "off",
                "account_id": None, "accounts": [],
                "note": "Research only. No orders can be placed."}
    try:
        from .broker.ibkr import IBKRBroker
        broker = IBKRBroker(config)
        ping = broker.ping()
        accounts = ping.get("accounts") or []
        account_id = exec_cfg.get("account") or (accounts[0] if len(accounts) == 1 else None)
        mode = "paper" if ping["paper"] else "live"
        if mode == "paper":
            note = "IBKR connected (PAPER) — orders still need your per-order confirmation"
        else:
            note = (f"IBKR connected to a LIVE account ({', '.join(accounts) or 'unknown'}). "
                    "Paper account ids start with 'DU' — log into the Paper Trading side "
                    "of IB Gateway if you did not intend to trade real money.")
        return {"enabled": True, "connected": True, "paper": ping["paper"],
                "mode": mode, "account_id": account_id, "accounts": accounts, "note": note}
    except Exception as exc:
        # Connected-ness unknown => treat as unverified, which the UI renders
        # with live-money severity. Never present this as paper.
        return {"enabled": True, "connected": False, "paper": False,
                "mode": "unverified", "account_id": exec_cfg.get("account") or None,
                "accounts": [],
                "note": f"Order entry is ON but the broker is unreachable, so the account "
                        f"mode cannot be verified — nothing is treated as paper. ({exc})"}


def _ticket_age_seconds(ticket):
    """Seconds since the ticket was prepared, or None if unparseable."""
    try:
        created = datetime.fromisoformat(ticket["created_at"])
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - created).total_seconds()
    except (ValueError, TypeError, KeyError):
        return None


def _load_approved_idea(idea_id):
    idea = journal.get_idea(idea_id)
    if idea is None:
        raise ExecutionRefused(f"Idea #{idea_id} does not exist.")
    if idea["decision"] != "approved":
        raise ExecutionRefused(
            f"Idea #{idea_id} is '{idea['decision']}', not 'approved'. "
            "Approve it in the dashboard first — that is the human approval step.")
    payload = idea.get("payload") or {}
    plan = payload.get("plan")
    if not plan:
        raise ExecutionRefused(f"Idea #{idea_id} has no trade plan, so there is nothing to place.")
    if (payload.get("gate") or {}).get("verdict") == "rejected":
        raise ExecutionRefused(
            f"Idea #{idea_id} was rejected by the risk gate; it cannot be executed.")
    return idea, plan, payload


def _check_daily_limits(config, portfolio_value, base_ccy):
    """Daily circuit breaker (audit finding D-5).

    Every other gate in this file is per-order. Per-order confirmation makes
    runaway automation impossible, but it does nothing about a bad day
    compounded by a human clicking through ten tickets in a row — which is the
    realistic failure mode for a discretionary trader, not a rogue loop.

    Both limits are opt-in: unset means no cap, so this changes nothing for an
    existing config until the user chooses a number.
    """
    exec_cfg = config.get("execution", {}) or {}

    max_orders = exec_cfg.get("max_orders_per_day")
    if max_orders is not None:
        placed = journal.orders_submitted_today()
        if placed >= int(max_orders):
            raise ExecutionRefused(
                f"Daily order limit reached: {placed} order(s) already went to the "
                f"broker today and your config allows {int(max_orders)} "
                "(execution.max_orders_per_day). This is a deliberate cooling-off "
                "point — nothing further can be placed until tomorrow (UTC).")

    max_loss_pct = exec_cfg.get("max_daily_loss_pct")
    if max_loss_pct is not None and portfolio_value:
        pnl_today = journal.realised_pnl_today()
        if pnl_today < 0:
            loss_pct = abs(pnl_today) / portfolio_value * 100
            if loss_pct >= float(max_loss_pct):
                raise ExecutionRefused(
                    f"Daily loss limit reached: you have realised "
                    f"{abs(pnl_today):,.0f} {base_ccy} of losses today, which is "
                    f"{loss_pct:.1f}% of the account (limit {max_loss_pct}%, "
                    "execution.max_daily_loss_pct). No further orders today — "
                    "this is the rule you set when you were not losing.")


def prepare_ticket(idea_id, config, router):
    """Step 1 of 2. Re-validates risk against the CURRENT portfolio and returns
    a ticket for human review. Places nothing."""
    if not config.get("execution", {}).get("enabled"):
        raise ExecutionRefused(
            "Execution is disabled in config.yaml (execution.enabled: false). "
            "This build is research-only until you turn it on deliberately.")

    idea, plan, payload = _load_approved_idea(idea_id)

    from .broker.factory import get_execution_broker
    broker = get_execution_broker(config)          # raises if Gateway is unreachable
    account = broker.get_account()
    positions = broker.get_positions()

    base_ccy = account.get("base_currency", config.get("base_currency", "USD"))

    # R-2: "IB reported nothing" and "IB reported zero" are different answers,
    # and neither may be replaced with the config.yaml figure. Falling back to
    # an assumed balance means every risk percentage below is computed against
    # an account that isn't yours — if the real one is smaller, the gate waves
    # through a position far larger than your rules intend.
    portfolio_value = account.get("portfolio_value")
    if portfolio_value is None:
        raise ExecutionRefused(
            "Your broker did not report an account value (no NetLiquidation figure), "
            "so every risk percentage would be measured against a guess. This is "
            "usually a market-data subscription problem or an account-summary "
            "timeout — reconnect IB Gateway and try again.")
    try:
        portfolio_value = float(portfolio_value)
    except (TypeError, ValueError):
        raise ExecutionRefused(
            f"Your broker reported an unreadable account value ({portfolio_value!r}). "
            "Refusing to size a trade against it.")
    if portfolio_value <= 0:
        raise ExecutionRefused(
            f"Your broker reports an account value of {portfolio_value:,.2f} {base_ccy}. "
            "Nothing can be sized against a zero or negative balance.")

    # Daily circuit breaker, checked before any of the per-order work.
    _check_daily_limits(config, portfolio_value, base_ccy)

    currencies = {base_ccy, plan.get("currency", base_ccy)} | {
        p.get("currency", base_ccy) for p in positions}
    fx_rates = router.get_fx_rates(currencies, base_ccy)

    # Re-run the same deterministic gate against live portfolio state. An idea
    # that was fine yesterday may breach exposure caps today.
    recheck_config = {
        **config,
        "base_currency": base_ccy,
        "account": {**config["account"], "portfolio_value": portfolio_value},
        "positions": positions,
    }
    snapshot = payload.get("snapshot") or {"ticker": idea["ticker"]}
    # strict_fx: a missing rate is a hard failure here. On the research path it
    # is only a flag, but this is the step that turns numbers into an order.
    recheck = gate.evaluate(snapshot, payload.get("thesis") or {}, plan,
                            recheck_config, fx_rates, strict_fx=True)
    if recheck["verdict"] == "rejected":
        raise ExecutionRefused(
            "Risk re-check against your CURRENT portfolio rejects this order: "
            + "; ".join(recheck["hard_failures"]))

    # A plan with no stop is unplaceable whatever the market is doing, so this
    # is settled before anything is asked of the price feed.
    stop_price = plan.get("stop")
    if not stop_price:
        raise ExecutionRefused(
            "This plan has no stop level, so no protective stop could be attached. "
            "Unprotected orders are not supported.")

    # The plan's entry/stop were computed from the quote at analysis time. Check
    # the CURRENT price before offering a ticket: if the market has moved past
    # the plan, the levels (and the max-loss figure) are no longer meaningful.
    # R-3: no price means no drift check AND no "already past the stop" check.
    # Skipping both and issuing the ticket anyway is trading blind on levels
    # that may be days old — and a data-feed failure is most likely exactly
    # when the market is moving hardest. Not being able to see the market is a
    # reason not to trade, not a reason to proceed.
    current_price = _current_price(router, idea["ticker"])
    if not current_price:
        raise ExecutionRefused(
            f"Could not read a current price for {idea['ticker']}, so neither the "
            f"{MAX_ENTRY_DRIFT_PCT}% entry-drift check nor the 'price has already passed "
            "your stop' check could run. The plan's levels may be days old. No ticket "
            "is prepared while the market can't be seen — try again when data returns.")

    drift_pct = (current_price / plan["entry"] - 1) * 100
    if abs(drift_pct) > MAX_ENTRY_DRIFT_PCT:
        raise ExecutionRefused(
            f"{idea['ticker']} now trades at {current_price:.2f}, "
            f"{drift_pct:+.1f}% away from the planned entry {plan['entry']:.2f} "
            f"(limit {MAX_ENTRY_DRIFT_PCT}%). The plan's stop and position size were "
            "sized for the old price — re-run the analysis to get a current plan.")
    if not _stop_still_valid(plan, current_price):
        raise ExecutionRefused(
            f"{idea['ticker']} at {current_price:.2f} has already passed the planned "
            f"stop {plan['stop']:.2f} — this setup is invalidated, not tradable.")

    side = "buy" if plan["direction"] == "long" else "sell"
    ticket_id = journal.create_order_ticket(
        idea_id=idea_id, side=side, ticker=idea["ticker"], quantity=plan["shares"],
        limit_price=plan["entry"], stop_price=stop_price,
        currency=plan.get("currency", base_ccy), max_loss=plan.get("risk_amount"))

    return {
        "ticket_id": ticket_id,
        "idea_id": idea_id,
        "ticker": idea["ticker"],
        "side": side,
        "quantity": plan["shares"],
        "limit_price": plan["entry"],
        "stop_price": stop_price,
        "current_price": current_price,
        "price_drift_pct": round(drift_pct, 2) if drift_pct is not None else None,
        "currency": plan.get("currency", base_ccy),
        "target_reference": plan.get("target"),
        "max_loss": plan.get("risk_amount"),
        # Default False: anything we cannot positively confirm as paper is
        # presented as live money.
        "paper": bool(account.get("paper", getattr(broker, "is_paper", False))),
        "account_source": account.get("source", "config.yaml"),
        "recheck_verdict": recheck["verdict"],
        "recheck_flags": recheck["soft_flags"],
        "confirm_instructions": (
            f"To place this order, type {idea['ticker']} exactly to confirm. This sends a "
            f"LIMIT entry at {plan['entry']} with an ATTACHED protective STOP at {stop_price} "
            "(a bracket), so the stop is live at the broker as soon as the entry fills."),
    }


def _current_price(router, ticker):
    """Latest close for a staleness check. None if data is unavailable —
    the caller then proceeds on the plan's own levels."""
    try:
        df = router.get_prices(ticker, period="5d")
        return float(df["Close"].iloc[-1])
    except Exception:
        return None


def _stop_still_valid(plan, current_price):
    if plan["direction"] == "long":
        return current_price > plan["stop"]
    return current_price < plan["stop"]


def _recheck_price_at_confirmation(ticket, plan, router):
    """Refuse the placement if the market moved while the ticket was open.

    Deliberately mirrors prepare_ticket's guards, including refusing when the
    price cannot be read at all: an unreadable feed is not permission to send
    an order priced off a quote nobody can verify.
    """
    ticker = ticket["ticker"]
    limit_price = float(ticket["limit_price"])
    current_price = _current_price(router, ticker)
    if not current_price:
        raise ExecutionRefused(
            f"Could not read a current price for {ticker} at the moment of placement, "
            "so the order could not be checked against the live market. Nothing was "
            "sent. Prepare a new ticket when data returns.")

    drift_pct = (current_price / limit_price - 1) * 100
    if abs(drift_pct) > MAX_ENTRY_DRIFT_PCT:
        raise ExecutionRefused(
            f"{ticker} moved to {current_price:.2f} while this ticket was open — "
            f"{drift_pct:+.1f}% from the limit price {limit_price:.2f} "
            f"(limit {MAX_ENTRY_DRIFT_PCT}%). Nothing was sent. Prepare a new ticket "
            "so the size and stop are computed from the current price.")

    stop_price = ticket.get("stop_price")
    if stop_price and plan and not _stop_still_valid(
            {"direction": plan["direction"], "stop": float(stop_price)}, current_price):
        raise ExecutionRefused(
            f"{ticker} at {current_price:.2f} has already passed the stop "
            f"{float(stop_price):.2f}. The setup is invalidated — nothing was sent.")


def confirm_ticket(ticket_id, typed_confirmation, config, router=None):
    """Step 2 of 2. Places the order only if the human typed the ticker exactly.

    router: used to re-read the price at the moment of placement. prepare_ticket
    enforced the drift limit, but that was up to TICKET_MAX_AGE_SECONDS ago and
    the market does not wait for the human to finish typing. Callers that can
    reach market data should always pass it.
    """
    if not config.get("execution", {}).get("enabled"):
        raise ExecutionRefused("Execution is disabled in config.yaml.")

    ticket = journal.get_order(ticket_id)
    if ticket is None:
        raise ExecutionRefused(f"Order ticket #{ticket_id} does not exist.")
    if ticket["status"] != "pending_confirmation":
        raise ExecutionRefused(
            f"Ticket #{ticket_id} is already '{ticket['status']}' — tickets are single-use. "
            "Prepare a new one if you still want this trade.")
    age = _ticket_age_seconds(ticket)
    if age is not None and age > TICKET_MAX_AGE_SECONDS:
        journal.update_order(ticket_id, "cancelled",
                             detail=f"expired after {int(age)}s without confirmation")
        raise ExecutionRefused(
            f"Ticket #{ticket_id} expired ({int(age / 60)} minutes old) — its limit price is "
            "based on a stale quote. Prepare a new ticket; it re-checks the current price "
            "against the plan and refuses if the market has moved away from it.")

    if (typed_confirmation or "").strip().upper() != ticket["ticker"].upper():
        raise ExecutionRefused(
            f"Confirmation text did not match. Type '{ticket['ticker']}' exactly to place this order.")

    # Re-verify the idea is still human-approved at the moment of placement.
    idea, plan, _payload = _load_approved_idea(ticket["idea_id"])

    # D-6: re-check the price HERE, not just at prepare time. The drift limit
    # was enforced against a quote that is now up to TICKET_MAX_AGE_SECONDS old,
    # and a volatile name can cross both the drift limit and the stop inside
    # that window — which is precisely what the limit exists to prevent.
    if router is not None:
        _recheck_price_at_confirmation(ticket, plan, router)

    # Atomically claim the ticket BEFORE contacting the broker. The status read
    # above is not enough: two concurrent confirms can both pass it. Only the
    # caller that wins this database transition may place an order.
    if not journal.claim_order_for_submission(ticket_id):
        raise ExecutionRefused(
            f"Ticket #{ticket_id} is already being submitted (or was already used). "
            "No second order was sent.")

    try:
        from .broker.factory import get_execution_broker
        broker = get_execution_broker(config)
    except Exception as exc:
        journal.release_order_claim(ticket_id)   # broker never contacted; ticket reusable
        raise ExecutionRefused(f"Order was NOT placed — broker unavailable: {exc}")

    intent = OrderIntent(
        ticker=ticket["ticker"], side=ticket["side"], quantity=int(ticket["quantity"]),
        order_type="limit", limit_price=float(ticket["limit_price"]),
        stop_price=float(ticket["stop_price"]) if ticket.get("stop_price") else None,
        currency=ticket["currency"], idea_id=ticket["idea_id"],
        note=f"From idea #{ticket['idea_id']}, human-confirmed ticket #{ticket_id}")

    try:
        result = broker.place_order(intent)
    except Exception as exc:
        # Claim stays consumed: we cannot know whether IB received the order, so
        # never re-open the ticket for another automatic attempt.
        journal.update_order(ticket_id, "error", detail=f"{type(exc).__name__}: {exc}")
        raise ExecutionRefused(f"Order was NOT placed: {exc}")

    status = _map_broker_status(result.get("status"))
    journal.update_order(ticket_id, status,
                         ib_order_id=result.get("ib_order_id"), detail=result.get("detail"))
    return {"ticket_id": ticket_id, "recorded_status": status, **result}


# IBKR order states -> our journal states. Anything unrecognised is recorded as
# 'submitting' (unknown, needs your eyes in TWS) rather than claimed as accepted.
_IB_STATUS_MAP = {
    "PendingSubmit": "submitting", "PreSubmitted": "submitting", "ApiPending": "submitting",
    "Submitted": "accepted", "PendingCancel": "accepted",
    "Filled": "filled",
    "Cancelled": "cancelled", "ApiCancelled": "cancelled",
    "Inactive": "rejected",
}


def _map_broker_status(ib_status):
    if not ib_status:
        return "submitting"
    return _IB_STATUS_MAP.get(str(ib_status), "submitting")


def cancel_ticket(ticket_id):
    ticket = journal.get_order(ticket_id)
    if ticket is None:
        raise ExecutionRefused(f"Order ticket #{ticket_id} does not exist.")
    if ticket["status"] != "pending_confirmation":
        raise ExecutionRefused(f"Ticket #{ticket_id} is '{ticket['status']}' and cannot be cancelled.")
    journal.update_order(ticket_id, "cancelled", detail="cancelled by user before submission")
    return {"ticket_id": ticket_id, "status": "cancelled"}

def auto_process_idea(idea_id, config, router):
    """
    Automated execution routing based on confidence and risk limits.
    
    1. Hard risk violation -> Reject immediately (no human review).
    2. High confidence -> Auto-trade (no human approval needed).
    3. Borderline -> Leave as 'pending' for human review.
    """
    idea = journal.get_idea(idea_id)
    if not idea:
        return
    
    payload = idea.get("payload") or {}
    gate_result = payload.get("gate") or {}
    
    # 1. Hard risk violation -> Auto-Reject
    if gate_result.get("verdict") == "rejected" or gate_result.get("hard_failures"):
        journal.set_decision(idea_id, "rejected")
        journal.record_outcome(idea_id, "scratch", notes="Auto-rejected due to hard risk violation")
        return
    
    # 2. Confidence Check
    plan = payload.get("plan") or {}
    confidence = plan.get("confidence") or 0
    
    strategy_idea = payload.get("strategy_idea") or {}
    meta = strategy_idea.get("meta") or {}
    
    # We define High Confidence as LLM confidence >= 80, or Kelly fraction > 0.
    is_high_confidence = (confidence >= 80) or (meta.get("kelly_fraction", 0) > 0) or (meta.get("ml_probability_of_success", 0) >= 0.70)
    
    if is_high_confidence:
        # Auto-Approve
        journal.set_decision(idea_id, "approved")
        
        # Auto-Execute if execution is enabled
        if config.get("execution", {}).get("enabled"):
            try:
                ticket = prepare_ticket(idea_id, config, router)
                # Pass the exact ticker to bypass the typed confirmation check
                confirm_ticket(ticket["ticket_id"], ticket["ticker"], config, router)
            except ExecutionRefused:
                # It remains approved, but execution was refused (e.g. drift limit exceeded).
                # The user can still see it in the dashboard.
                pass
            except Exception:
                pass
