"""Market-wide risk-on / risk-off — is the whole market rising or falling?

Distinct from `regime.py`, which classifies ONE ticker's own conditions. A stock
can look like a perfect oversold bounce while the entire index is falling off a
cliff, and buying that dip is how 2018 and 2020 lost money. This is the layer
that answers "should I be buying anything at all right now?"

Two design decisions worth understanding:

**Per region, not global.** A US uptrend says nothing about whether Tokyo is
rising. Gating a Japanese trade on the S&P is a category error, so each market
is measured against its own index.

**Hysteresis.** A plain "above the 200-day average" test flips state every time
the index brushes the line, and each flip costs a round of entries and exits.
That whipsaw is what turned 2022 — a choppy year that repeatedly crossed the
trend — into a loss. Re-entry therefore requires clearing the average by a
margin, not merely touching it.
"""
from . import indicators

RISK_ON = "RISK_ON"
RISK_OFF = "RISK_OFF"
UNKNOWN = "UNKNOWN"

DEFAULTS = {
    "enabled": True,
    "ma_period": 200,
    # Re-entry needs the index this far ABOVE its average; risk-off triggers as
    # soon as it falls below. Deliberately asymmetric: slow to buy back in, fast
    # to step aside. Whipsaw costs money, being late to a rally costs less.
    "reentry_buffer_pct": 2.0,
    # What to do when the market is falling. "skip" takes nothing; "half_size"
    # keeps a foot in — measured as better on both return and drawdown.
    "risk_off_action": "half_size",
    "risk_off_size_multiplier": 0.5,
    # Fast crashes can outrun a 200-day average entirely. When realised
    # volatility spikes past this percentile of its own recent range, treat it
    # as risk-off regardless of where the average sits.
    "volatility_percentile_max": 95.0,
    "volatility_lookback": 120,
    # Ticker suffix -> index. '' is the US (no suffix in Yahoo symbols).
    "indices": {
        "": "^GSPC", ".L": "^FTSE", ".DE": "^GDAXI", ".PA": "^FCHI",
        ".AS": "^AEX", ".SW": "^SSMI", ".MI": "FTSEMIB.MI", ".T": "^N225",
        ".HK": "^HSI", ".AX": "^AXJO", ".TO": "^GSPTSE",
    },
}


def config_for(config):
    return {**DEFAULTS, **((config or {}).get("market_filter") or {})}


def index_for(ticker, config=None):
    """Which index governs this instrument, or None if the market is unmapped.

    An unrecognised suffix must NOT fall back to the US index. Gating a
    Brazilian stock on the S&P 500 would be worse than not gating it at all —
    it would look like a working filter while measuring the wrong market.
    """
    cfg = config_for(config)
    indices = cfg["indices"]
    name = str(ticker or "").upper()
    if "." not in name:
        return indices.get("")            # Yahoo gives US listings no suffix
    suffix = ""
    for candidate in indices:
        if candidate and name.endswith(candidate.upper()) and len(candidate) > len(suffix):
            suffix = candidate
    return indices.get(suffix) if suffix else None


def classify_market(index_closes, config=None, previous_state=None):
    """RISK_ON / RISK_OFF for one index, as of the last bar it contains.

    `previous_state` drives the hysteresis: coming back from RISK_OFF requires
    clearing the moving average by `reentry_buffer_pct`, while a single close
    below it is enough to go RISK_OFF. Pass it through from the prior bar or the
    buffer does nothing.
    """
    cfg = config_for(config)
    if not cfg["enabled"]:
        return _result(RISK_ON, ["Market filter is switched off in config."], None, None)

    if index_closes is None or len(index_closes) < cfg["ma_period"]:
        have = 0 if index_closes is None else len(index_closes)
        return _result(UNKNOWN, [f"Only {have} bars of index history; need "
                                 f"{cfg['ma_period']} for the trend average."], None, None)

    ma_series = index_closes.rolling(cfg["ma_period"]).mean()
    price = indicators.latest(index_closes)
    ma = indicators.latest(ma_series)
    if not indicators.is_finite(price) or not indicators.is_finite(ma) or ma <= 0:
        return _result(UNKNOWN, ["Index trend average unavailable."], None, None)

    above_pct = (price / ma - 1) * 100
    reasons = []

    # Volatility override — a fast crash can be below-average in price terms
    # only after the damage is done.
    vol_rank = _volatility_rank(index_closes, cfg)
    if vol_rank is not None and vol_rank >= cfg["volatility_percentile_max"]:
        reasons.append(f"Index volatility is in the top {100 - vol_rank:.0f}% of its "
                       f"recent range — treating as risk-off regardless of trend")
        return _result(RISK_OFF, reasons, above_pct, vol_rank)

    if previous_state == RISK_OFF:
        # Coming back requires clearing the average by the buffer.
        if above_pct >= cfg["reentry_buffer_pct"]:
            reasons.append(f"Index is {above_pct:.1f}% above its {cfg['ma_period']}-day "
                           f"average, clearing the {cfg['reentry_buffer_pct']}% re-entry buffer")
            return _result(RISK_ON, reasons, above_pct, vol_rank)
        reasons.append(f"Index is {above_pct:+.1f}% vs its {cfg['ma_period']}-day average — "
                       f"not yet {cfg['reentry_buffer_pct']}% clear, staying risk-off "
                       "to avoid whipsawing back in")
        return _result(RISK_OFF, reasons, above_pct, vol_rank)

    if price < ma:
        reasons.append(f"Index is {above_pct:.1f}% BELOW its {cfg['ma_period']}-day "
                       "average — the market itself is falling")
        return _result(RISK_OFF, reasons, above_pct, vol_rank)

    reasons.append(f"Index is {above_pct:+.1f}% above its {cfg['ma_period']}-day average")
    return _result(RISK_ON, reasons, above_pct, vol_rank)


def size_multiplier(state, config=None):
    """How much of normal size to take given the market state.

    0.0 stands aside entirely; 0.5 halves it. Half-sizing measured better than
    skipping on both return AND drawdown — standing fully aside gives up the
    recovery, which historically starts while the average still says risk-off.
    """
    cfg = config_for(config)
    if state != RISK_OFF:
        return 1.0
    if cfg["risk_off_action"] == "skip":
        return 0.0
    return float(cfg["risk_off_size_multiplier"])


def _volatility_rank(closes, cfg):
    returns = closes.pct_change().dropna()
    if len(returns) < 30:
        return None
    realised = returns.rolling(20).std()
    return indicators.percentile_rank(realised, lookback=int(cfg["volatility_lookback"]))


def _result(state, reasons, above_pct, vol_rank):
    return {
        "state": state,
        "reasons": reasons,
        "pct_vs_average": None if above_pct is None else round(above_pct, 2),
        "volatility_percentile": vol_rank,
    }
