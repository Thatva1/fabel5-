"""Regime router — decides WHICH strategies are allowed to look at a ticker.

Momentum says "buy strength". Mean-reversion says "sell strength". Both are
correct, in different markets. Run them together and they cancel; run the wrong
one and it bleeds. So the regime is classified first, and only the strategies
registered for that regime are given the data.

The router does no analysis of its own and makes no risk decisions. It returns
tagged ideas that flow into the existing pipeline unchanged:
Context Reader -> Thesis -> Trade Plan -> Risk Gate -> Human Approval.
"""
from ..research import regime as regime_mod
from . import registry
from .base import StrategyContext


def route(ticker, df, snapshot, config):
    """Classify the regime and run the matching strategies for one ticker.

    Returns {regime, strategies_run, ideas, notes} where ideas are StrategyIdea
    objects. A strategy that raises is contained: its failure is reported in
    notes and the other strategies still run.
    """
    result = {"ticker": ticker, "regime": None, "strategies_run": [],
              "ideas": [], "notes": []}

    if snapshot.get("error"):
        result["notes"].append(f"Skipped: {snapshot['error']}")
        return result

    market_regime = regime_mod.classify(snapshot, config)
    result["regime"] = market_regime

    if market_regime["regime"] == regime_mod.UNKNOWN:
        result["notes"].append(market_regime["reasons"][0])
        return result

    candidates = registry.strategies_for_regime(market_regime["regime"], config)
    if not candidates:
        result["notes"].append(
            f"No enabled strategy runs in a {market_regime['label'].lower()} market")
        return result

    for strategy in candidates:
        result["strategies_run"].append(strategy.name)
        ctx = StrategyContext(
            ticker=ticker,
            df=df,
            snapshot=snapshot,
            regime=market_regime,
            params=strategy.params_for(config),
            config=config,
        )
        try:
            ideas = strategy.detect(ctx) or []
        except Exception as exc:
            # One broken strategy must never take down a whole scan.
            result["notes"].append(f"{strategy.label} failed: {type(exc).__name__}: {exc}")
            continue

        # Direction filter, applied centrally so it works for every strategy —
        # including ones added later — rather than each having to implement it.
        # A watch item has no direction and is never filtered out.
        allowed = registry.directions_for(strategy, config)
        result["ideas"].extend(
            i for i in ideas
            if i is not None and (i.direction is None or i.direction in allowed))

    if not result["ideas"]:
        result["notes"].append(
            f"{market_regime['label']} — "
            f"{', '.join(s.label for s in candidates)} ran, no setup met its full rule set")
    return result
