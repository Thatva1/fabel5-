"""Range Trading — trade the channel, not a direction.

In a sideways market the profitable question is not "which way is this going"
but "where are the edges". This strategy finds a horizontal band price has
actually respected, buys near the floor, sells near the ceiling, and targets the
opposite side.

The validation matters more than the entry rule. Any 60 bars of data have a
highest high and a lowest low; that does not make them a range. A real range
has to be (a) tight enough to be worth trading relative to daily volatility,
(b) not so tight that the stop is inside the noise, and (c) touched repeatedly
at both edges. A level price visited once is a coincidence, not support.
"""
from ..research import indicators
from .base import LONG, SHORT, Strategy


class RangeTradingStrategy(Strategy):
    name = "range_trading"
    label = "Range trading"
    description = ("Buys near the floor and sells near the ceiling of a horizontal "
                   "band that price has repeatedly respected.")
    regimes = ("SIDEWAYS",)

    defaults = {
        "lookback_days": 60,          # window used to find the band
        "min_touches": 2,             # bars near each edge before it counts as a level
        "touch_tolerance_pct": 1.5,   # how close counts as "a touch"
        "min_band_atr": 3.0,          # a band thinner than this is just noise
        "max_band_atr": 12.0,         # a band wider than this is a trend, not a range
        "entry_zone_pct": 25.0,       # enter within this % of the band height of an edge
        "stop_buffer_atr": 0.5,       # stop sits this far outside the band
        "target_buffer_pct": 15.0,    # exit before the far edge, not exactly at it
        "min_reward_risk": 1.5,
    }

    def detect(self, ctx):
        band = self._find_band(ctx)
        if band is None:
            return []
        ideas = []
        for direction in (LONG, SHORT):
            idea = self._edge_trade(ctx, direction, band)
            if idea:
                ideas.append(idea)
        return ideas

    def _find_band(self, ctx):
        """Locate and validate the horizontal band, or return None."""
        p = ctx.params
        atr = ctx.snapshot.get("atr")
        df = ctx.df
        if not indicators.is_finite(atr) or atr <= 0:
            return None

        lookback = int(p["lookback_days"])
        if df is None or len(df) < lookback:
            return None
        window = df.tail(lookback)
        resistance = float(window["High"].max())
        support = float(window["Low"].min())
        height = resistance - support
        if height <= 0:
            return None

        height_atr = height / atr
        if not (p["min_band_atr"] <= height_atr <= p["max_band_atr"]):
            return None

        tolerance = p["touch_tolerance_pct"]
        upper_touches = indicators.touches_in_band(window["High"], resistance, tolerance, lookback)
        lower_touches = indicators.touches_in_band(window["Low"], support, tolerance, lookback)
        if upper_touches < p["min_touches"] or lower_touches < p["min_touches"]:
            return None

        return {
            "support": round(support, 2),
            "resistance": round(resistance, 2),
            "height": round(height, 2),
            "height_atr": round(height_atr, 2),
            "upper_touches": upper_touches,
            "lower_touches": lower_touches,
            "lookback_days": lookback,
        }

    def _edge_trade(self, ctx, direction, band):
        p = ctx.params
        price, atr = ctx.snapshot.get("price"), ctx.snapshot.get("atr")
        if not indicators.is_finite(price):
            return None

        long_side = direction == LONG
        support, resistance, height = band["support"], band["resistance"], band["height"]
        zone = height * p["entry_zone_pct"] / 100

        if long_side and price > support + zone:
            return None
        if not long_side and price < resistance - zone:
            return None

        reasons = list(ctx.regime.get("reasons", []))
        reasons.append(
            f"Range identified over {band['lookback_days']} sessions: {support:.2f} to "
            f"{resistance:.2f} ({band['height_atr']:.1f} ATR tall)")
        reasons.append(
            f"Both edges have held: {band['lower_touches']} touches at the floor, "
            f"{band['upper_touches']} at the ceiling")
        reasons.append(
            f"Price {price:.2f} is in the "
            f"{'lower' if long_side else 'upper'} {p['entry_zone_pct']:.0f}% of the band, "
            f"near {'support' if long_side else 'resistance'}")

        buffer_ = height * p["target_buffer_pct"] / 100
        if long_side:
            stop = support - p["stop_buffer_atr"] * atr
            target = resistance - buffer_
        else:
            stop = resistance + p["stop_buffer_atr"] * atr
            target = support + buffer_

        risk = abs(price - stop)
        if risk <= 0:
            return None
        if abs(target - price) / risk < p["min_reward_risk"]:
            return None

        reasons.append(
            f"Target is the far side of the range, stopping "
            f"{p['target_buffer_pct']:.0f}% short of it; the stop sits outside the band, "
            "because a clean break means the range is over and the idea is wrong")

        return self.build(
            ctx, direction, entry=price, stop=stop, target=target, reasons=reasons,
            entry_zone=([support, support + zone] if long_side
                        else [resistance - zone, resistance]),
            headline=(f"Buying support at {support:.2f} in a sideways range" if long_side
                      else f"Selling resistance at {resistance:.2f} in a sideways range"),
            meta=band)
