"""Squeeze / Breakout-Pending — flag the coiled spring, don't guess the direction.

When Bollinger band width compresses to a multi-week low, a large move is
statistically likely and its DIRECTION is not knowable. The whole point of this
strategy is refusing to guess: while price is still inside the bands it produces
a WATCH item, never a trade plan.

Once price actually breaks out, it hands the setup to the Momentum strategy in
the direction of the break, running momentum's own filters (volume, RSI, not
chasing). If those filters say no, the break is reported as a watch item with
the reason rather than being forced into a trade.
"""
from ..research import indicators
from .base import Strategy
from .momentum import MomentumStrategy


class SqueezeStrategy(Strategy):
    name = "squeeze"
    label = "Squeeze"
    description = ("Flags volatility compression and waits for the actual break, "
                   "then hands the setup to Momentum in the break direction.")
    regimes = ("VOLATILITY_SQUEEZE",)

    defaults = {
        "min_volume_ratio": 1.3,     # a break needs participation to count
        "handoff_to_momentum": True,
    }

    def detect(self, ctx):
        s = ctx.snapshot
        price = s.get("price")
        upper, lower = s.get("bb_upper"), s.get("bb_lower")
        if not indicators.is_finite(price):
            return []

        width_rank = s.get("bb_width_rank")
        bias = ctx.regime.get("trend_bias")
        base_reasons = list(ctx.regime.get("reasons", []))

        broke_up = indicators.is_finite(upper) and price > upper
        broke_down = indicators.is_finite(lower) and price < lower
        vol_ratio = s.get("volume_ratio")
        volume_ok = (indicators.is_finite(vol_ratio)
                     and vol_ratio >= ctx.params["min_volume_ratio"])

        # Still coiled: report it and wait. No direction, so no plan.
        if not (broke_up or broke_down):
            reasons = base_reasons + [
                "Price is still inside the bands — no break yet, so there is no "
                "direction to trade and no plan to build",
                f"Watch for a close outside {lower:.2f} / {upper:.2f} on above-average volume"
                if indicators.is_finite(upper) and indicators.is_finite(lower)
                else "Watch for a decisive close outside the recent range",
            ]
            if bias:
                reasons.append(f"Prevailing bias is {bias}: a break that way is a "
                               "continuation, the other way a reversal")
            return [self.watch(
                ctx,
                headline="Volatility squeeze — breakout pending, direction unknown",
                reasons=reasons,
                meta={"bb_width_rank": width_rank, "trend_bias": bias,
                      "watch_above": upper, "watch_below": lower})]

        direction_word = "up" if broke_up else "down"
        break_reasons = base_reasons + [
            f"Price {price:.2f} broke {direction_word} out of the squeeze "
            f"({'above ' + format(upper, '.2f') if broke_up else 'below ' + format(lower, '.2f')})"
        ]

        if not volume_ok:
            return [self.watch(
                ctx,
                headline=f"Squeeze broke {direction_word} — but on unconvincing volume",
                reasons=break_reasons + [
                    f"Volume is {vol_ratio:.1f}x the 20-day average, below the "
                    f"{ctx.params['min_volume_ratio']}x this strategy requires "
                    "— breaks without participation often fail back into the range"
                    if indicators.is_finite(vol_ratio) else "Volume data unavailable",
                ],
                meta={"break_direction": direction_word, "bb_width_rank": width_rank})]

        if not ctx.params["handoff_to_momentum"]:
            return [self.watch(
                ctx,
                headline=f"Squeeze broke {direction_word} on strong volume",
                reasons=break_reasons + ["Momentum handoff is switched off in config"],
                meta={"break_direction": direction_word})]

        # The handoff. Momentum runs its own filters against a regime relabelled
        # to the break direction; nothing here loosens those filters.
        handed = self._handoff(ctx, direction_word, break_reasons)
        if handed:
            return handed

        return [self.watch(
            ctx,
            headline=f"Squeeze broke {direction_word}, but the momentum filters did not confirm",
            reasons=break_reasons + [
                "Handed to the Momentum strategy, which rejected it — typically the "
                "move is already extended past the level, RSI is exhausted, or the "
                "moving averages have not stacked up yet",
                "Left as a watch item rather than forced into a trade",
            ],
            meta={"break_direction": direction_word, "handoff": "rejected"})]

    def _handoff(self, ctx, direction_word, break_reasons):
        momentum = MomentumStrategy()
        if not momentum.enabled(ctx.config):
            return None
        handoff_ctx = ctx.__class__(
            ticker=ctx.ticker,
            df=ctx.df,
            snapshot=ctx.snapshot,
            regime={**ctx.regime,
                    "regime": "TRENDING_UP" if direction_word == "up" else "TRENDING_DOWN",
                    "reasons": break_reasons},
            params=momentum.params_for(ctx.config),
            config=ctx.config,
        )
        ideas = momentum.detect(handoff_ctx)
        for idea in ideas:
            # Keep the regime honest — the market is in a squeeze, that is what
            # the journal should measure against — but record where it came from.
            idea.regime = ctx.regime.get("regime")
            idea.meta["handoff_from"] = self.name
            idea.meta["break_direction"] = direction_word
            idea.reasons.append(
                "Setup originated as a volatility squeeze that broke "
                f"{direction_word}; Momentum's filters confirmed it")
        return ideas or None
