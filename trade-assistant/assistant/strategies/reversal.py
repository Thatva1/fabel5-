"""Section E of the library — things that go the other way.

Every strategy in the rest of this library buys what has been going up. These
buy what has been going down, and they are here for exactly that reason: a book
made only of momentum is one bet expressed several ways, and the reversal
family loses money at different times.

The three horizons are genuinely different effects, not one effect retuned:

  * ONE MONTH reverses. This is the mirror image of the skip in the 12-1
    momentum signal — the same month that contaminates a momentum ranking is
    the month this strategy trades. Very high turnover, and costs decide whether
    it survives.
  * THREE TO FIVE YEARS reverses. De Bondt-Thaler overreaction, a completely
    separate mechanism operating on a horizon where momentum has long since
    decayed.
  * BAND EXTREMES revert intraday-to-weekly. The weakest of the three by
    evidence — the library tiers it T3 — and it is the rule set this project
    already tested and found wanting on mega-caps. It is kept because the
    library's own note says it was pointed at the wrong universe, and a 1,500-
    name universe with real small and mid caps is the first honest test of that
    claim.

The library is explicit that reversal is strongest in smaller, higher-volatility,
lower-quality names, which is the opposite of where the momentum strategies want
to be. That is not a contradiction to resolve; it is the diversification.
"""
from ..research import indicators
from . import factors
from .base import LONG, Strategy


class ShortTermReversalStrategy(Strategy):
    """Jegadeesh (1990); Lehmann (1990) — the one-month loser bounce."""

    name = "short_reversal"
    label = "Short-term reversal"
    description = ("Buys the past month's worst performers, betting the move "
                   "overshot. High turnover — costs decide whether it survives.")
    regimes = ("SIDEWAYS", "VOLATILITY_SQUEEZE", "TRENDING_UP")

    defaults = {
        "lookback_bars": 21,          # one month
        "bottom_pct": 10.0,           # the worst decile
        "min_universe": 20,
        "rebalance": "monthly",
        "vol_lookback_bars": 60,
        "max_vol_pct": 90.0,          # reversal LIVES in volatile names
        "require_above_trend": False,  # deliberately not a trend strategy
        "stop_atr": 2.0,
        "reward_multiple": 2.0,
    }

    def detect(self, ctx):
        p = ctx.params
        price, atr = ctx.snapshot.get("price"), ctx.snapshot.get("atr")
        if not indicators.is_finite(price):
            return []
        if not factors.is_rebalance_bar(ctx.df, p["rebalance"]):
            return []

        universe = ctx.cross_section
        if universe is None or len(universe) < int(p["min_universe"]):
            return []
        as_of = factors.as_of_of(ctx.df)
        eligible = universe.calm_enough(as_of, p["max_vol_pct"],
                                        lookback_bars=p["vol_lookback_bars"])
        stats = universe.momentum(ctx.ticker, as_of, lookback_bars=p["lookback_bars"],
                                  skip_bars=0, eligible=eligible)
        if stats is None:
            return []
        # Rank 1 is the STRONGEST, so the losers are at the far end.
        from_bottom = (stats["count"] - stats["rank"] + 1) / stats["count"] * 100
        if from_bottom > float(p["bottom_pct"]):
            return []

        reasons = list(ctx.regime.get("reasons", []))
        reasons.append(
            f"Among the worst {from_bottom:.0f}% of {stats['count']} instruments "
            f"over the last month ({factors.pct(stats['value'])})")
        reasons.append(
            "This is the same month that a 12-1 momentum signal deliberately "
            "skips: short-horizon moves overshoot and snap back, which is why "
            "momentum excludes it and this strategy trades it")

        levels = factors.atr_levels(price, atr, LONG, p["stop_atr"], p["reward_multiple"])
        if levels is None:
            return []
        idea = self.build(ctx, LONG, entry=price, stop=levels[0], target=levels[1],
                          reasons=reasons,
                          headline=(f"One-month loser — worst {from_bottom:.0f}% of "
                                    f"{stats['count']}, betting on the snap back"),
                          meta={"month_return_pct": round(stats["value"] * 100, 2),
                                "percentile_from_bottom": round(from_bottom, 1),
                                "universe_size": stats["count"]})
        return [idea] if idea else []


class LongTermReversalStrategy(Strategy):
    """De Bondt & Thaler (1985) — the three-to-five year overreaction."""

    name = "long_reversal"
    label = "Long-term reversal"
    description = ("Buys instruments that have underperformed for years, betting "
                   "the market overreacted. A different mechanism to the "
                   "one-month bounce, on a horizon where momentum has decayed.")
    regimes = ("SIDEWAYS", "VOLATILITY_SQUEEZE", "TRENDING_UP")

    defaults = {
        # Three years, skipping the most recent year so this does not simply
        # fight the momentum strategies over the same twelve months.
        "lookback_bars": 756,
        "skip_bars": 252,
        "bottom_pct": 10.0,
        "min_universe": 20,
        "rebalance": "monthly",
        "vol_lookback_bars": 60,
        "max_vol_pct": 80.0,
        "stop_atr": 2.5,
        "reward_multiple": 3.0,
    }

    def detect(self, ctx):
        p = ctx.params
        price, atr = ctx.snapshot.get("price"), ctx.snapshot.get("atr")
        if not indicators.is_finite(price):
            return []
        if not factors.is_rebalance_bar(ctx.df, p["rebalance"]):
            return []

        universe = ctx.cross_section
        if universe is None or len(universe) < int(p["min_universe"]):
            return []
        as_of = factors.as_of_of(ctx.df)
        eligible = universe.calm_enough(as_of, p["max_vol_pct"],
                                        lookback_bars=p["vol_lookback_bars"])
        stats = universe.momentum(ctx.ticker, as_of, lookback_bars=p["lookback_bars"],
                                  skip_bars=p["skip_bars"], eligible=eligible)
        if stats is None:
            return []
        from_bottom = (stats["count"] - stats["rank"] + 1) / stats["count"] * 100
        if from_bottom > float(p["bottom_pct"]):
            return []

        reasons = list(ctx.regime.get("reasons", []))
        reasons.append(
            f"Among the worst {from_bottom:.0f}% of {stats['count']} instruments "
            f"over three years ending a year ago ({factors.pct(stats['value'])})")
        reasons.append(
            "The most recent year is excluded so this does not fight the "
            "momentum strategies over the same twelve months — the overreaction "
            "being traded here is older than that")

        levels = factors.atr_levels(price, atr, LONG, p["stop_atr"], p["reward_multiple"])
        if levels is None:
            return []
        idea = self.build(ctx, LONG, entry=price, stop=levels[0], target=levels[1],
                          reasons=reasons,
                          headline=f"Multi-year laggard — worst {from_bottom:.0f}% over three years",
                          meta={"three_year_return_pct": round(stats["value"] * 100, 2),
                                "percentile_from_bottom": round(from_bottom, 1)})
        return [idea] if idea else []


class BandReversionStrategy(Strategy):
    """The project's own original rule set, re-pointed at the right universe.

    The 10-year backtest that killed this ran it on mega-caps in the biggest
    bull run in decades — fading the strongest trending assets on earth. The
    library's verdict was that the rules were fine and the universe was wrong.
    A 1,500-name universe with real small and mid caps is the first honest test
    of that claim, so it is kept, long-only, and tiered T3 by its own author.
    """

    name = "band_reversion"
    label = "Band reversion"
    description = ("Buys oversold extremes stretched below the lower Bollinger "
                   "band once the fall has stalled. Weakest evidence in the "
                   "library; kept to test it on the universe it suits.")
    regimes = ("SIDEWAYS", "VOLATILITY_SQUEEZE")

    defaults = {
        "rsi_oversold": 30.0,
        "min_stretch_atr": 1.0,
        "stall_lookback": 3,
        "stop_buffer_atr": 0.5,
        "max_stop_atr": 2.5,
        "min_reward_risk": 1.2,
        "target_fraction": 1.0,      # all the way back to the 20-day average
        "rebalance": "any",          # genuinely a short-horizon rule
    }

    def detect(self, ctx):
        p = ctx.params
        s = ctx.snapshot
        price, atr, rsi = s.get("price"), s.get("atr"), s.get("rsi")
        mean = s.get("bb_middle") or s.get("sma20")
        band = s.get("bb_lower")
        if not all(indicators.is_finite(v) for v in (price, atr, rsi, mean, band)):
            return []
        if atr <= 0 or rsi > float(p["rsi_oversold"]) or price >= band:
            return []

        stretch = abs(price - mean) / atr
        if stretch < float(p["min_stretch_atr"]):
            return []
        if not self._stalling(ctx, int(p["stall_lookback"])):
            return []

        reasons = list(ctx.regime.get("reasons", []))
        reasons.append(f"RSI {rsi:.0f} is oversold and price {price:.2f} sits below "
                       f"the lower band ({band:.2f})")
        reasons.append(f"Stretched {stretch:.1f} ATR from the 20-day average ({mean:.2f})")
        reasons.append("The fall has stalled — without this the rule catches "
                       "falling knives, which is most of how it lost money before")

        extreme = s.get("swing_low_20d")
        if not indicators.is_finite(extreme):
            extreme = price
        stop = max(min(extreme, price) - p["stop_buffer_atr"] * atr,
                   price - p["max_stop_atr"] * atr)
        target = price + (mean - price) * float(p["target_fraction"])
        risk = abs(price - stop)
        if risk <= 0 or abs(target - price) / risk < float(p["min_reward_risk"]):
            return []

        idea = self.build(ctx, LONG, entry=price, stop=stop, target=target,
                          reasons=reasons,
                          headline="Oversold and stalling — fade back to the 20-day average",
                          meta={"stretch_atr": round(stretch, 2),
                                "mean_target": round(float(mean), 2)})
        return [idea] if idea else []

    @staticmethod
    def _stalling(ctx, lookback):
        closes = ctx.df["Close"].dropna()
        if len(closes) < lookback + 2:
            return False
        rsi_series = indicators.rsi(closes).dropna()
        if len(rsi_series) < lookback + 1:
            return False
        window = rsi_series.tail(lookback + 1)
        turned = float(window.iloc[-1]) > float(window.min())
        closed_up = float(closes.iloc[-1]) > float(closes.iloc[-2])
        return bool(turned or closed_up)
