"""Pipeline orchestrator.

  Scanner (measure) -> Regime classifier -> Strategy router (decide what to flag)
    -> Context Reader -> Thesis (explain) -> Trade Plan -> Risk Gate -> Journal

Layer 1 guarantee: nothing here places, modifies, or suggests placing an order.
Every idea lands in the journal awaiting a human decision. Portfolio state is
read through the Broker seam (PaperBroker in v1) so Layer 2 can swap in later
without touching this file.

The division of labour that matters: strategies decide WHAT to flag and at which
levels; the LLM only explains WHY in plain English. An idea now exists because a
complete strategy rule set fired, not because a single indicator twitched.
"""
from . import journal
from .broker.factory import get_broker
from .core import fx
from .core.config import load_config
from .providers.base import ProviderUnavailable
from .providers.router import DataRouter
from .research import scanner
from .research.context import build_context, snapshot_facts
from .research.thesis import build_thesis
from .risk import borrow, gate, sizing
from .strategies import router as strategy_router
from .strategies.cross_section import CrossSection

_router = None


def get_router(config):
    """One shared router per process so caches and rate limiters are shared."""
    global _router
    if _router is None:
        _router = DataRouter(config)
    return _router


def _benchmark_closes(router, config):
    """Close series for the benchmark, or None.

    Used three ways: relative strength in the scanner, beta in the low-beta
    tilt, and the market-trend overlay on cross-sectional momentum. Failure here
    is never fatal to the scan itself — but the two strategies that need it will
    correctly produce nothing rather than guess, so a missing benchmark shows up
    as a quiet scan, not as a wrong one.
    """
    symbol = (config.get("scanner", {}) or {}).get("benchmark")
    if not symbol:
        return None
    try:
        df = router.get_prices(symbol)
        return df["Close"] if df is not None else None
    except Exception:
        return None


def analyze_ticker(ticker, config=None, snapshot=None, strategy_idea=None,
                   shared=None):
    """Run the research pipeline for one ticker, optionally for one strategy idea.

    strategy_idea: a StrategyIdea dict from the router. When supplied it drives
    the direction and the levels; when omitted the original thesis-driven
    behaviour is used, so this function still works standalone (the dashboard's
    "analyse this ticker" button and the tests both rely on that).

    shared: optional dict of already-fetched context/thesis, so several ideas on
    the same ticker do not re-fetch news or pay for a second LLM call.
    """
    config = config or load_config()
    router = get_router(config)
    broker, broker_note = get_broker(config)

    if snapshot is None:
        try:
            df = router.get_prices(ticker)
        except ProviderUnavailable as exc:
            return {"ticker": ticker, "error": str(exc)}
        snapshot = scanner.scan_ticker(ticker, df, config.get("scanner", {}),
                                       benchmark_closes=_benchmark_closes(router, config))
    if snapshot.get("error"):
        return {"ticker": ticker, "error": snapshot["error"]}

    if shared and shared.get("context"):
        ctx, thesis = shared["context"], shared["thesis"]
    else:
        ctx = build_context(ticker, router, config)
        snapshot["_fundamentals"] = {"sector": ctx["fundamentals"].get("sector")}
        thesis = build_thesis(snapshot, snapshot_facts(snapshot), ctx, config)
        if shared is not None:
            shared["context"], shared["thesis"] = ctx, thesis
    snapshot["_fundamentals"] = {"sector": ctx["fundamentals"].get("sector")}

    account = broker.get_account()
    positions = broker.get_positions()
    base_ccy = account["base_currency"]
    instrument_ccy = ctx["instrument_currency"]

    currencies = {base_ccy, instrument_ccy} | {p.get("currency", base_ccy) for p in positions}
    fx_rates = router.get_fx_rates(currencies, base_ccy)

    # get_account() returns None when the broker reported no account value at
    # all (see broker/ibkr.py). Research may continue on the config.yaml figure
    # — it costs nothing to look at an idea — but it must say so out loud, and
    # prepare_ticket refuses outright rather than sizing a real order on it.
    portfolio_value = account.get("portfolio_value")
    if portfolio_value is None:
        portfolio_value = config["account"]["portfolio_value"]
        broker_note = (
            "Your broker did not report an account value, so every figure below is "
            f"based on the config.yaml portfolio value of {portfolio_value:,.0f} "
            f"{base_ccy}, not your real balance. No order can be prepared until the "
            "broker reports one.")

    risk_budget_base = (portfolio_value
                        * config["account"].get("risk_per_trade_pct", 1.0) / 100)
    try:
        risk_budget_instrument = fx.convert(risk_budget_base, base_ccy, instrument_ccy, fx_rates)
    except fx.MissingRateError:
        risk_budget_instrument = risk_budget_base  # gate will flag the missing rate

    # Concentration cap converted to the instrument's currency, so the sizer can
    # respect it directly instead of building an oversized plan that the gate
    # would only reject.
    max_position_base = (portfolio_value
                         * config["account"].get("max_position_pct", 15.0) / 100)
    try:
        max_position_instrument = fx.convert(max_position_base, base_ccy,
                                             instrument_ccy, fx_rates)
    except fx.MissingRateError:
        max_position_instrument = max_position_base

    plan = sizing.build_plan(snapshot, thesis, risk_budget_instrument, instrument_ccy,
                             max_position_value=max_position_instrument,
                             strategy_idea=strategy_idea)

    # Borrow availability is only looked up for an actual short — it can cost a
    # broker round-trip, and asking it of a long is pointless.
    shortability = None
    if plan and plan.get("direction") == "short":
        shortability = borrow.check_shortability(
            ticker, config, broker=broker, fundamentals=ctx.get("fundamentals"),
            currency=instrument_ccy)

    # The gate's percentage caps must measure against the SAME portfolio value
    # the position was sized from. Passing config's figure while sizing used the
    # broker's meant that whenever the two differed, every cap was evaluated
    # against the wrong denominator — a real £1m account checked against a
    # £100k config value makes every position look 10x its true weight.
    gate_config = {
        **config,
        "base_currency": base_ccy,
        "account": {**config["account"], "portfolio_value": portfolio_value},
        "positions": positions,
    }
    gate_result = gate.evaluate(snapshot, thesis, plan, gate_config, fx_rates,
                                price_history_fn=router.get_prices,
                                shortability=shortability)
    if broker_note:
        gate_result["soft_flags"].append(broker_note)

    # Max loss in the BASE currency, recorded at creation time. Without this the
    # journal can only sum instrument-currency figures, and "£420 risked on
    # momentum" would silently be adding pounds to euros to yen.
    risk_base = None
    if plan:
        try:
            risk_base = round(fx.convert(plan["risk_amount"], plan.get("currency", base_ccy),
                                         base_ccy, fx_rates), 2)
        except fx.MissingRateError:
            risk_base = None
    idea_id = journal.add_idea(snapshot, thesis, plan, gate_result,
                               strategy_idea=strategy_idea, risk_base=risk_base)

    return {
        "idea_id": idea_id,
        "ticker": ticker,
        "snapshot": snapshot,
        "context": ctx,
        "thesis": thesis,
        "plan": plan,
        "gate": gate_result,
        "strategy_idea": strategy_idea,
        "shortability": shortability,
    }


def _log_watch_item(ticker, snapshot, strategy_idea, regime):
    """A watch item (a squeeze that hasn't broken yet) has no plan and no
    direction, so it never reaches the sizer or the gate. It is still journalled
    so the dashboard can show it and so its regime is measured later."""
    thesis = {"thesis_summary": strategy_idea["headline"], "source": "strategy-rules",
              "net_bias": "neutral", "conviction": 0, "supports_setup": False}
    gate_result = {"verdict": "needs_more_research",
                   "hard_failures": [],
                   "soft_flags": ["Watch item only — no direction yet, so no trade plan "
                                  "and no position size were built."],
                   "exposure": None}
    idea_id = journal.add_idea(snapshot, thesis, None, gate_result,
                               strategy_idea=strategy_idea)
    return {"idea_id": idea_id, "ticker": ticker, "snapshot": snapshot,
            "context": None, "thesis": thesis, "plan": None, "gate": gate_result,
            "strategy_idea": strategy_idea, "regime": regime}


def run_scan(config=None, analyze_flagged=True, progress_cb=None, should_cancel=None):
    """Scan the watchlist: measure every ticker, classify its regime, run the
    strategies that match, and put the resulting ideas through the pipeline.

    Two passes, not one. The price history for the WHOLE watchlist is fetched
    first, because the strategies now rank instruments against each other and a
    rank computed while half the universe is still unfetched is not a rank. The
    second pass does the per-ticker work. No extra requests are made: the same
    frames are reused rather than re-fetched.

    progress_cb(done, total, ticker, stage) is called as work proceeds so the UI
    can show per-ticker progress rather than an opaque spinner.

    should_cancel() lets the user stop a multi-minute scan. Cancelling during
    the second pass returns what was analysed so far, as before. Cancelling
    during the FETCH returns nothing, deliberately: the universe is incomplete
    at that point, and a rotation ranked against half a watchlist would be a
    plausible-looking answer to a question nobody asked.
    """
    config = config or load_config()
    router = get_router(config)
    scan_cfg = config.get("scanner", {})
    watchlist = config.get("watchlist", [])
    total = len(watchlist)
    watchlist_results, ideas = [], []
    cancelled = False
    benchmark = _benchmark_closes(router, config)

    def report(done, ticker, stage):
        if progress_cb:
            progress_cb(done, total, ticker, stage)

    # Pass 1 — price history for the whole universe.
    frames, fetch_errors = {}, {}
    for index, ticker in enumerate(watchlist):
        if should_cancel and should_cancel():
            cancelled = True
            break
        report(index, ticker, "fetching price data")
        try:
            frames[ticker] = router.get_prices(ticker)
        except Exception as exc:
            fetch_errors[ticker] = str(exc)

    cross_section = CrossSection.from_frames(frames)

    # Pass 2 — measure, classify, run the strategies, research what fires.
    for index, ticker in enumerate([] if cancelled else watchlist):
        if should_cancel and should_cancel():
            cancelled = True
            break
        report(index, ticker, "scanning price data")
        df = frames.get(ticker)
        if ticker in fetch_errors:
            snapshot = {"ticker": ticker, "error": fetch_errors[ticker],
                        "flagged": False, "signals": []}
        else:
            try:
                snapshot = scanner.scan_ticker(ticker, df, scan_cfg,
                                               benchmark_closes=benchmark)
            except Exception as exc:
                snapshot = {"ticker": ticker, "error": str(exc),
                            "flagged": False, "signals": []}
                df = None
        watchlist_results.append(snapshot)

        if not analyze_flagged or snapshot.get("error"):
            report(index + 1, ticker, "done")
            continue

        # Regime first, then only the strategies that belong in it.
        report(index, ticker, "classifying regime")
        routed = strategy_router.route(ticker, df, snapshot, config,
                                       cross_section=cross_section,
                                       benchmark_closes=benchmark)
        snapshot["regime"] = (routed["regime"] or {}).get("regime")
        snapshot["regime_label"] = (routed["regime"] or {}).get("label")
        snapshot["regime_reasons"] = (routed["regime"] or {}).get("reasons", [])
        snapshot["strategies_run"] = routed["strategies_run"]
        snapshot["router_notes"] = routed["notes"]
        snapshot["setup_count"] = len(routed["ideas"])

        # One context fetch and one LLM call per ticker, shared by its ideas.
        shared = {}
        for strategy_idea in routed["ideas"]:
            if should_cancel and should_cancel():
                cancelled = True
                break
            idea_dict = strategy_idea.to_dict()
            if idea_dict.get("status") != "actionable":
                ideas.append(_log_watch_item(ticker, snapshot, idea_dict, routed["regime"]))
                continue
            report(index, ticker, f"researching {strategy_idea.strategy_label} setup")
            try:
                ideas.append(analyze_ticker(ticker, config, snapshot=dict(snapshot),
                                            strategy_idea=idea_dict, shared=shared))
            except Exception as exc:
                ideas.append({"ticker": ticker, "error": f"pipeline failed: {exc}",
                              "strategy_idea": idea_dict})
        report(index + 1, ticker, "done")

    return {"watchlist": watchlist_results, "ideas": ideas,
            "cancelled": cancelled, "scanned": len(watchlist_results), "total": total}
