"""Momentum / Trend-Following — buy strength in an uptrend, sell it in a downtrend.

This is the disciplined version of the old "breakout above the 20-day high"
scanner rule. Three filters were added because that rule on its own buys the top
of every spike:

  * a trend filter (price above a RISING 200-day average — supplied by the regime),
  * volume confirmation (a breakout nobody participated in is noise),
  * an overextension reject (RSI already above 80, or price already far past the
    breakout level, means the easy move is gone and the stop has to be absurd).

Stop and target are derived from ATR and from the broken level itself.
"""
from ..research import indicators
from .base import LONG, SHORT, Strategy


class MomentumStrategy(Strategy):
    name = "momentum"
    label = "Momentum"
    description = ("Buys breakouts in an established uptrend and sells breakdowns "
                   "in an established downtrend, with volume confirmation.")
    regimes = ("TRENDING_UP", "TRENDING_DOWN")

    defaults = {
        "min_volume_ratio": 1.2,        # today's volume vs the 20-day average
        "rsi_max_long": 80.0,           # above this the long move is overextended
        "rsi_min_short": 20.0,          # below this the short move is overextended
        "rsi_slope_days": 5,            # RSI must be improving over this many bars
        "require_ma_stack": True,       # 20-day average on the trend side of the 50-day
        "ma_cross_within_days": 0,      # >0 = only take a FRESH cross this recent
        "max_extension_atr": 1.0,       # reject chasing more than this far past the level
        "stop_buffer_atr": 0.35,        # stop sits this far beyond the broken level
        "max_stop_atr": 1.5,            # never risk more than this many ATR per share
        "reward_multiple": 2.5,         # target = entry +/- this many times the risk
        "min_relative_strength_pct": None,   # e.g. 0 to demand it beat its index
    }

    def detect(self, ctx):
        regime = ctx.regime.get("regime")
        if regime == "TRENDING_UP":
            idea = self._directional(ctx, LONG)
        elif regime == "TRENDING_DOWN":
            idea = self._directional(ctx, SHORT)
        else:
            return []
        return [idea] if idea else []

    def _directional(self, ctx, direction):
        p = ctx.params
        s = ctx.snapshot
        price, atr = s.get("price"), s.get("atr")
        if not indicators.is_finite(price) or not indicators.is_finite(atr) or atr <= 0:
            return None

        long_side = direction == LONG
        level = s.get("swing_high_20d") if long_side else s.get("swing_low_20d")
        if not indicators.is_finite(level):
            return None

        reasons = list(ctx.regime.get("reasons", []))

        # 1. The breakout itself, measured against completed bars only.
        broke = price > level if long_side else price < level
        if not broke:
            return None
        reasons.append(
            f"Price {price:.2f} broke {'above' if long_side else 'below'} the 20-day "
            f"{'high' if long_side else 'low'} of {level:.2f}")

        # 2. Not chasing. Once price is far past the level the stop has to sit so
        #    wide that the reward:risk stops making sense.
        extension_atr = abs(price - level) / atr
        if extension_atr > p["max_extension_atr"]:
            return None
        reasons.append(f"Still close to the level ({extension_atr:.1f} ATR past it, "
                       f"limit {p['max_extension_atr']})")

        # 3. Volume confirmation.
        vol_ratio = s.get("volume_ratio")
        if not indicators.is_finite(vol_ratio) or vol_ratio < p["min_volume_ratio"]:
            return None
        reasons.append(f"Volume {vol_ratio:.1f}x the 20-day average confirms participation")

        # 4. Moving-average stack, and optionally a genuinely fresh cross.
        if p["require_ma_stack"]:
            sma20, sma50 = s.get("sma20"), s.get("sma50")
            if not (indicators.is_finite(sma20) and indicators.is_finite(sma50)):
                return None
            stacked = sma20 > sma50 if long_side else sma20 < sma50
            if not stacked:
                return None
            reasons.append(f"20-day average {sma20:.2f} is "
                           f"{'above' if long_side else 'below'} the 50-day {sma50:.2f}")

        cross_window = p["ma_cross_within_days"]
        if cross_window and not self._crossed_recently(ctx, long_side, cross_window):
            return None
        if cross_window:
            reasons.append(f"The moving-average cross happened within the last "
                           f"{cross_window} sessions")

        # 5. RSI: improving, but not already exhausted.
        rsi = s.get("rsi")
        if not indicators.is_finite(rsi):
            return None
        if long_side and rsi > p["rsi_max_long"]:
            return None
        if not long_side and rsi < p["rsi_min_short"]:
            return None
        if not self._rsi_improving(ctx, long_side, p["rsi_slope_days"]):
            return None
        reasons.append(f"RSI {rsi:.0f} is {'rising' if long_side else 'falling'} and not yet "
                       f"{'overbought' if long_side else 'oversold'} "
                       f"(limit {p['rsi_max_long'] if long_side else p['rsi_min_short']:.0f})")

        # 6. Relative strength — a preference by default, a requirement if configured.
        rs = s.get("relative_strength_pct")
        floor = p["min_relative_strength_pct"]
        if floor is not None:
            if not indicators.is_finite(rs):
                return None
            passes = rs >= floor if long_side else rs <= -floor
            if not passes:
                return None
        if indicators.is_finite(rs):
            reasons.append(f"Relative strength vs its index: {rs:+.1f}% over 3 months")

        # Levels: stop just beyond the broken level, never wider than max_stop_atr.
        if long_side:
            stop = max(level - p["stop_buffer_atr"] * atr, price - p["max_stop_atr"] * atr)
        else:
            stop = min(level + p["stop_buffer_atr"] * atr, price + p["max_stop_atr"] * atr)
        risk = abs(price - stop)
        if risk <= 0:
            return None
        target = price + p["reward_multiple"] * risk if long_side else price - p["reward_multiple"] * risk

        entry_low, entry_high = (level, price) if long_side else (price, level)
        return self.build(
            ctx, direction, entry=price, stop=stop, target=target, reasons=reasons,
            entry_zone=[min(entry_low, entry_high), max(entry_low, entry_high)],
            headline=(f"Breakout {'above' if long_side else 'below'} the 20-day "
                      f"{'high' if long_side else 'low'} in an established "
                      f"{'uptrend' if long_side else 'downtrend'}"),
            meta={"broken_level": round(float(level), 2),
                  "extension_atr": round(extension_atr, 2)})

    @staticmethod
    def _rsi_improving(ctx, long_side, days):
        """RSI moving the right way over `days` bars. A breakout on deteriorating
        momentum is usually the last gasp of the move, not the start of one."""
        series = indicators.rsi(ctx.df["Close"]).dropna()
        if len(series) <= days:
            return False
        now, then = float(series.iloc[-1]), float(series.iloc[-(days + 1)])
        return now > then if long_side else now < then

    @staticmethod
    def _crossed_recently(ctx, long_side, window):
        closes = ctx.df["Close"]
        fast = closes.rolling(20).mean()
        slow = closes.rolling(50).mean()
        diff = (fast - slow).dropna()
        if len(diff) <= window:
            return False
        recent = diff.tail(window + 1)
        if long_side:
            return bool((recent.iloc[0] <= 0) and (recent.iloc[-1] > 0))
        return bool((recent.iloc[0] >= 0) and (recent.iloc[-1] < 0))
