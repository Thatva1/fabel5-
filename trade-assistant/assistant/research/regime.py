"""Market Regime Classifier — deterministic, no LLM, no I/O.

Answers one question about a ticker: what KIND of market is this right now?
The router uses the answer to pick which strategies are allowed to run, because
momentum and mean-reversion rules contradict each other on purpose — run both
blindly and they cancel out.

    TRENDING_UP         strong directional move higher
    TRENDING_DOWN       strong directional move lower
    SIDEWAYS            no trend worth following; price is rotating
    VOLATILITY_SQUEEZE  bands compressed to a multi-week low; a move is coming
                        but its direction is not yet knowable
    UNKNOWN             not enough history to say (no strategy may run)

Every threshold comes from config.yaml -> regime. Nothing is hard-coded.
"""
from . import indicators

TRENDING_UP = "TRENDING_UP"
TRENDING_DOWN = "TRENDING_DOWN"
SIDEWAYS = "SIDEWAYS"
VOLATILITY_SQUEEZE = "VOLATILITY_SQUEEZE"
UNKNOWN = "UNKNOWN"

ALL_REGIMES = (TRENDING_UP, TRENDING_DOWN, SIDEWAYS, VOLATILITY_SQUEEZE)

LABELS = {
    TRENDING_UP: "Trending up",
    TRENDING_DOWN: "Trending down",
    SIDEWAYS: "Sideways / range-bound",
    VOLATILITY_SQUEEZE: "Volatility squeeze",
    UNKNOWN: "Not enough history",
}

DEFAULTS = {
    "adx_trend_min": 25.0,        # below this there is no trend worth following
    "adx_chop_max": 20.0,         # below this it is definitively choppy
    "squeeze_width_rank_max": 15.0,   # BB width in the bottom 15% of its own range
    "squeeze_lookback_days": 120,
    "ma_slope_min_pct": 0.0,      # 200-day MA must be rising for TRENDING_UP
    "squeeze_overrides_trend": True,
}


def config_for(config):
    """Merge the user's config.yaml regime block over the defaults."""
    return {**DEFAULTS, **((config or {}).get("regime") or {})}


def classify(snapshot, config=None):
    """Label the current conditions for one ticker.

    Reads only the snapshot produced by scanner.scan_ticker, so it is trivially
    testable: hand it a dict of numbers, get a regime back.

    Returns {regime, label, reasons[], trend_bias, inputs{}} where trend_bias is
    the prevailing direction even when the regime is not a trend — the squeeze
    strategy uses it to say which way a break would be a continuation.
    """
    cfg = config_for(config)
    inputs = _inputs(snapshot)
    reasons = []

    missing = [k for k in ("price", "sma200", "adx") if not indicators.is_finite(inputs.get(k))]
    if missing:
        return _result(UNKNOWN, [
            "Not enough price history to classify the regime "
            f"(missing {', '.join(missing)}). 200-day average needs about a year of data."
        ], None, inputs)

    price, sma200, adx = inputs["price"], inputs["sma200"], inputs["adx"]
    slope = inputs.get("sma200_slope_pct")
    width_rank = inputs.get("bb_width_rank")

    above_200 = price > sma200
    slope_min = cfg["ma_slope_min_pct"]
    rising = indicators.is_finite(slope) and slope > slope_min
    falling = indicators.is_finite(slope) and slope < -slope_min
    trending = adx >= cfg["adx_trend_min"]

    # trend_bias is the direction the tape leans, independent of whether the
    # trend is strong enough to trade. Needed by the squeeze handoff.
    trend_bias = "up" if above_200 and not falling else "down" if not above_200 and not rising else None

    # 1. Squeeze first. A multi-week low in band width means the next real move
    #    is a breakout, and guessing its direction is exactly what the brief
    #    says not to do — so it gets its own regime even inside a trend.
    if (indicators.is_finite(width_rank)
            and width_rank <= cfg["squeeze_width_rank_max"]
            and (cfg["squeeze_overrides_trend"] or not trending)):
        reasons.append(
            f"Bollinger band width is in the bottom {width_rank:.0f}% of the last "
            f"{cfg['squeeze_lookback_days']} days — volatility is compressed")
        reasons.append(f"ADX {adx:.0f}: "
                       + ("a trend is running underneath the squeeze"
                          if trending else "no established trend"))
        if trend_bias:
            reasons.append(f"Prevailing bias is {trend_bias} — a break that way "
                           "would be a continuation, the other way a reversal")
        return _result(VOLATILITY_SQUEEZE, reasons, trend_bias, inputs)

    # 2. A real trend needs BOTH strength (ADX) and agreement between price and
    #    the direction of its own 200-day average. Price above a rolling-over
    #    200-day MA is a late-stage move, not a trend to buy.
    if trending and above_200 and rising:
        reasons.append(f"ADX {adx:.0f} is above {cfg['adx_trend_min']:.0f} — the trend has strength")
        reasons.append(f"Price {price:.2f} is above its 200-day average {sma200:.2f}")
        reasons.append(f"200-day average is rising ({slope:+.2f}% over the last month)")
        return _result(TRENDING_UP, reasons, "up", inputs)

    if trending and not above_200 and falling:
        reasons.append(f"ADX {adx:.0f} is above {cfg['adx_trend_min']:.0f} — the downtrend has strength")
        reasons.append(f"Price {price:.2f} is below its 200-day average {sma200:.2f}")
        reasons.append(f"200-day average is falling ({slope:+.2f}% over the last month)")
        return _result(TRENDING_DOWN, reasons, "down", inputs)

    # 3. Everything else is sideways — including "strong ADX but price and its
    #    200-day average disagree", which is a turn, not a trend.
    if trending:
        reasons.append(
            f"ADX {adx:.0f} suggests movement, but price and its 200-day average disagree "
            "on direction — treating it as rotation, not a trend")
    else:
        reasons.append(f"ADX {adx:.0f} is below {cfg['adx_trend_min']:.0f} — no trend worth following")
    if indicators.is_finite(width_rank):
        reasons.append(f"Band width sits at the {width_rank:.0f}th percentile of its recent range")
    return _result(SIDEWAYS, reasons, trend_bias, inputs)


def _inputs(snapshot):
    keys = ("price", "sma20", "sma50", "sma200", "sma200_slope_pct", "adx",
            "plus_di", "minus_di", "atr", "rsi", "bb_upper", "bb_lower",
            "bb_middle", "bb_width_pct", "bb_width_rank")
    return {k: snapshot.get(k) for k in keys}


def _result(regime, reasons, trend_bias, inputs):
    return {
        "regime": regime,
        "label": LABELS[regime],
        "reasons": reasons,
        "trend_bias": trend_bias,
        "inputs": inputs,
    }
