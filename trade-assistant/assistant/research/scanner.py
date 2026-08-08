"""Market Scanner — computes the indicator snapshot for a ticker.

What changed when the strategy library arrived: this module no longer decides
what is worth trading. It measures. The `signals` list it produces is
DESCRIPTIVE context for the dashboard and the thesis engine ("unusual volume",
"RSI overbought"), and `flagged` means "something here is notable", not "take
this trade". Ideas are created only by a strategy in assistant/strategies/,
which requires a full rule set to line up rather than any one signal firing.

Pure computation over price history; no I/O.
"""
import math

from . import indicators

# Re-exported under their original private names so anything that imported them
# before the indicators module existed keeps working.
_rsi = indicators.rsi
_atr = indicators.atr


def _round(value, digits=2):
    if value is None or (isinstance(value, float) and (math.isnan(value) or math.isinf(value))):
        return None
    return round(float(value), digits)


def scan_ticker(ticker, df, scan_cfg, benchmark_closes=None):
    """Returns a snapshot dict with computed indicators, descriptive signals,
    a notable flag, and a rough direction hint.

    benchmark_closes: optional index close series, used for relative strength.
    The momentum strategy prefers names outperforming their index; without it
    that preference is simply skipped rather than guessed at.
    """
    min_days = scan_cfg.get("min_history_days", 60)
    if df is None or len(df) < min_days:
        return {"ticker": ticker, "error": "insufficient history", "flagged": False, "signals": []}

    closes, volumes = df["Close"], df["Volume"]
    last = float(closes.iloc[-1])
    prev = float(closes.iloc[-2])
    change_pct = (last / prev - 1) * 100

    lookback = scan_cfg.get("breakout_lookback_days", 20)
    sma20 = closes.rolling(20).mean().iloc[-1]
    sma50 = closes.rolling(50).mean().iloc[-1]
    rsi = _rsi(closes).iloc[-1]
    atr = _atr(df).iloc[-1]
    avg_vol20 = volumes.rolling(20).mean().iloc[-2]  # exclude today from the average
    vol_ratio = float(volumes.iloc[-1]) / float(avg_vol20) if avg_vol20 else None
    prior_high = float(closes.iloc[-(lookback + 1):-1].max())
    prior_low = float(closes.iloc[-(lookback + 1):-1].min())
    roc5 = (last / float(closes.iloc[-6]) - 1) * 100 if len(closes) > 6 else None

    # --- Regime + strategy inputs -----------------------------------------
    # Computed here so the classifier and every strategy read one shared set of
    # numbers rather than each recomputing (and slowly diverging).
    sma200_series = closes.rolling(200).mean()
    sma200 = indicators.latest(sma200_series)
    sma200_slope_pct = indicators.slope_pct(sma200_series, periods=21)
    adx_frame = indicators.adx(df, period=scan_cfg.get("adx_period", 14))
    bands = indicators.bollinger(closes, period=scan_cfg.get("bollinger_period", 20),
                                 num_std=scan_cfg.get("bollinger_std", 2.0))
    bb_width_rank = indicators.percentile_rank(
        bands["width_pct"], lookback=scan_cfg.get("squeeze_lookback_days", 120))
    swing_high, swing_low = indicators.recent_extremes(df, lookback)
    rel_strength = indicators.relative_strength(
        closes, benchmark_closes, days=scan_cfg.get("relative_strength_days", 63))

    signals, bull, bear = [], 0, 0

    if vol_ratio and vol_ratio >= scan_cfg.get("volume_spike_ratio", 1.6):
        signals.append(f"Unusual volume: {vol_ratio:.1f}x the 20-day average")
        bull += 1 if change_pct > 0 else 0
        bear += 1 if change_pct < 0 else 0

    if last > prior_high:
        signals.append(f"Breakout above {lookback}-day high ({prior_high:.2f})")
        bull += 2
    elif last < prior_low:
        signals.append(f"Breakdown below {lookback}-day low ({prior_low:.2f})")
        bear += 2

    if not math.isnan(rsi):
        if rsi >= scan_cfg.get("rsi_overbought", 70):
            signals.append(f"RSI overbought at {rsi:.0f} — extended, watch for exhaustion")
            bear += 1
        elif rsi <= scan_cfg.get("rsi_oversold", 30):
            signals.append(f"RSI oversold at {rsi:.0f} — washed out, watch for reversal")
            bull += 1

    if not math.isnan(sma20) and not math.isnan(sma50):
        prev_sma20 = closes.rolling(20).mean().iloc[-2]
        prev_sma50 = closes.rolling(50).mean().iloc[-2]
        if prev_sma20 <= prev_sma50 and sma20 > sma50:
            signals.append("Momentum shift: 20-day MA crossed above 50-day MA")
            bull += 2
        elif prev_sma20 >= prev_sma50 and sma20 < sma50:
            signals.append("Momentum shift: 20-day MA crossed below 50-day MA")
            bear += 2

    if roc5 is not None and abs(roc5) >= 5:
        direction = "up" if roc5 > 0 else "down"
        signals.append(f"Fast 5-day move: {roc5:+.1f}% ({direction})")
        bull += 1 if roc5 > 0 else 0
        bear += 1 if roc5 < 0 else 0

    gap_pct = scan_cfg.get("gap_pct", 2.0)
    open_today = float(df["Open"].iloc[-1])
    gap = (open_today / prev - 1) * 100
    if abs(gap) >= gap_pct:
        signals.append(f"Gap {'up' if gap > 0 else 'down'} {gap:+.1f}% at the open")
        bull += 1 if gap > 0 else 0
        bear += 1 if gap < 0 else 0

    direction = "bullish" if bull > bear else "bearish" if bear > bull else "mixed"
    return {
        "ticker": ticker,
        "price": _round(last),
        "change_pct": _round(change_pct),
        "volume_ratio": _round(vol_ratio),
        "rsi": _round(rsi, 1),
        "atr": _round(atr),
        "sma20": _round(sma20),
        "sma50": _round(sma50),
        "sma200": _round(sma200),
        "sma200_slope_pct": sma200_slope_pct,
        "adx": _round(indicators.latest(adx_frame["adx"]), 1),
        "plus_di": _round(indicators.latest(adx_frame["plus_di"]), 1),
        "minus_di": _round(indicators.latest(adx_frame["minus_di"]), 1),
        "bb_upper": _round(indicators.latest(bands["upper"])),
        "bb_lower": _round(indicators.latest(bands["lower"])),
        "bb_middle": _round(indicators.latest(bands["middle"])),
        "bb_width_pct": _round(indicators.latest(bands["width_pct"])),
        "bb_width_rank": bb_width_rank,
        "swing_high_20d": _round(swing_high),
        "swing_low_20d": _round(swing_low),
        "relative_strength_pct": rel_strength,
        "avg_volume_20d": _round(avg_vol20, 0),
        "prior_high_20d": _round(prior_high),
        "prior_low_20d": _round(prior_low),
        "signals": signals,
        "flagged": len(signals) > 0,
        "direction_hint": direction,
    }
