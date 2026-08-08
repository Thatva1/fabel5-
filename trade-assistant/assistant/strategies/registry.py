"""Strategy registry — the one place that knows which strategies exist.

Adding a strategy is two lines: import it, add it to BUILTIN. Nothing else in
the codebase needs to change, which is the entire point of the interface.

Enabling, disabling and re-tuning happen in config.yaml, never in code:

    strategies:
      mean_reversion:
        enabled: true
        rsi_overbought: 75        # override any key from the strategy's defaults
        regimes: [SIDEWAYS]       # override which regimes it may run in
"""
from .mean_reversion import MeanReversionStrategy
from .momentum import MomentumStrategy
from .range_trading import RangeTradingStrategy
from .squeeze import SqueezeStrategy

BUILTIN = (
    MomentumStrategy,
    MeanReversionStrategy,
    RangeTradingStrategy,
    SqueezeStrategy,
)


def all_strategies():
    """Fresh instances of every registered strategy, enabled or not."""
    return [cls() for cls in BUILTIN]


def enabled_strategies(config):
    return [s for s in all_strategies() if s.enabled(config)]


def regimes_for(strategy, config):
    """Which regimes this strategy may run in — config can override the default."""
    block = ((config or {}).get("strategies") or {}).get(strategy.name) or {}
    configured = block.get("regimes")
    if configured:
        return tuple(str(r).upper() for r in configured)
    return tuple(strategy.regimes)


def directions_for(strategy, config):
    """Which sides this strategy may take — config can restrict it.

        strategies:
          range_trading:
            directions: [long]      # stop shorting the range

    Useful because shorting carries costs and risks longs do not (borrow fees,
    stamp duty on the closing purchase, unlimited theoretical loss), so a rule
    set can be profitable long and lose money short.
    """
    block = ((config or {}).get("strategies") or {}).get(strategy.name) or {}
    configured = block.get("directions")
    if configured:
        return tuple(str(d).lower() for d in configured)
    return ("long", "short")


def strategies_for_regime(regime, config):
    return [s for s in enabled_strategies(config) if regime in regimes_for(s, config)]


def describe(config=None):
    """Catalogue for the dashboard: what exists, what it does, is it on."""
    return [
        {
            "name": s.name,
            "label": s.label,
            "description": s.description,
            "regimes": list(regimes_for(s, config)),
            "enabled": s.enabled(config),
            "settings": s.params_for(config),
        }
        for s in all_strategies()
    ]
