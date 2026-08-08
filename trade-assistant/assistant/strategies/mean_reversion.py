"""Mean-Reversion — fade stretched moves back toward the 20-day average.

This is the contrarian engine, and it is where a bearish view becomes an
actionable short rather than just a reason to stay out.

It deliberately does NOT run in a strong trend. Shorting an overbought stock in
a powerful uptrend is how accounts die: "overbought" can stay overbought for
months. The regime router only lets this run when there is no trend worth
following, which is exactly when an extreme tends to snap back.

Three things must line up, not one:
  1. an RSI extreme,
  2. price genuinely stretched outside the Bollinger band,
  3. evidence the move is STALLING — the push has stopped pushing.
"""
from ..research import indicators
from .base import LONG, SHORT, Strategy


class MeanReversionStrategy(Strategy):
    name = "mean_reversion"
    label = "Mean reversion"
    description = ("Fades overbought and oversold extremes back toward the 20-day "
                   "average, only when no strong trend is running.")
    regimes = ("SIDEWAYS",)

    defaults = {
        "rsi_overbought": 70.0,
        "rsi_oversold": 30.0,
        "require_band_break": True,     # price must be outside the Bollinger band
        "min_stretch_atr": 1.0,         # ...and this far from the 20-day average
        "stall_lookback": 3,            # bars used to judge "the push has stopped"
        "stop_buffer_atr": 0.5,         # stop sits this far beyond the extreme
        "max_stop_atr": 2.0,
        "min_reward_risk": 1.2,         # the snap-back must be worth the risk
        "target_fraction": 1.0,         # 1.0 = full way back to the 20-day average
    }

    def detect(self, ctx):
        ideas = []
        for direction in (SHORT, LONG):
            idea = self._fade(ctx, direction)
            if idea:
                ideas.append(idea)
        return ideas

    def _fade(self, ctx, direction):
        p = ctx.params
        s = ctx.snapshot
        price, atr, rsi = s.get("price"), s.get("atr"), s.get("rsi")
        mean = s.get("bb_middle") or s.get("sma20")
        if not all(indicators.is_finite(v) for v in (price, atr, rsi, mean)) or atr <= 0:
            return None

        short_side = direction == SHORT
        reasons = list(ctx.regime.get("reasons", []))

        # 1. RSI extreme.
        if short_side and rsi < p["rsi_overbought"]:
            return None
        if not short_side and rsi > p["rsi_oversold"]:
            return None
        reasons.append(f"RSI {rsi:.0f} is {'overbought' if short_side else 'oversold'} "
                       f"(threshold {p['rsi_overbought' if short_side else 'rsi_oversold']:.0f})")

        # 2. Genuinely stretched — outside the band AND far from the mean.
        band = s.get("bb_upper") if short_side else s.get("bb_lower")
        if p["require_band_break"]:
            if not indicators.is_finite(band):
                return None
            outside = price > band if short_side else price < band
            if not outside:
                return None
            reasons.append(f"Price {price:.2f} is outside the "
                           f"{'upper' if short_side else 'lower'} Bollinger band ({band:.2f})")

        stretch_atr = abs(price - mean) / atr
        if stretch_atr < p["min_stretch_atr"]:
            return None
        reasons.append(f"Stretched {stretch_atr:.1f} ATR from the 20-day average "
                       f"({mean:.2f}) — minimum {p['min_stretch_atr']}")

        # 3. Stalling. Without this the strategy catches falling knives: an
        #    extreme that is still accelerating is not yet a reversion.
        if not self._stalling(ctx, short_side, p["stall_lookback"]):
            return None
        reasons.append("The move has stalled — momentum rolled over from its recent extreme")

        # Levels: stop beyond the extreme of the move, target back at the mean.
        extreme = s.get("swing_high_20d") if short_side else s.get("swing_low_20d")
        if not indicators.is_finite(extreme):
            extreme = price
        if short_side:
            structural = max(extreme, price) + p["stop_buffer_atr"] * atr
            stop = min(structural, price + p["max_stop_atr"] * atr)
            target = price - (price - mean) * p["target_fraction"]
        else:
            structural = min(extreme, price) - p["stop_buffer_atr"] * atr
            stop = max(structural, price - p["max_stop_atr"] * atr)
            target = price + (mean - price) * p["target_fraction"]

        risk = abs(stop - price)
        if risk <= 0:
            return None
        reward_risk = abs(target - price) / risk
        if reward_risk < p["min_reward_risk"]:
            return None

        return self.build(
            ctx, direction, entry=price, stop=stop, target=target, reasons=reasons,
            headline=(f"{'Overbought' if short_side else 'Oversold'} and stalling — "
                      f"fade back to the 20-day average"),
            meta={"mean_target": round(float(mean), 2),
                  "stretch_atr": round(stretch_atr, 2)})

    @staticmethod
    def _stalling(ctx, short_side, lookback):
        """True when the push has lost its edge: RSI has turned back from its
        recent extreme, or the latest bar closed against the move."""
        closes = ctx.df["Close"].dropna()
        if len(closes) < lookback + 2:
            return False
        rsi_series = indicators.rsi(closes).dropna()
        if len(rsi_series) < lookback + 1:
            return False
        window = rsi_series.tail(lookback + 1)
        now = float(window.iloc[-1])
        turned = now < float(window.max()) if short_side else now > float(window.min())
        last, prev = float(closes.iloc[-1]), float(closes.iloc[-2])
        closed_against = last < prev if short_side else last > prev
        return bool(turned or closed_against)
