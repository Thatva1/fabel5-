"""Sections D and F — the low-volatility anomaly, and the calendar effects.

Low volatility belongs with low beta but is not the same measurement: beta is
sensitivity to the index, volatility is movement of any kind. A name can be
violently volatile on its own news and barely move with the market, scoring well
on one and badly on the other.

The two seasonal rules are here with a warning attached. They are the cheapest
strategies in the library to compute and the easiest to fool yourself with:
turn-of-month and sell-in-May are calendar patterns found by searching a
calendar, which is a space small enough to be searched exhaustively. Ariel
(1987) and Bouman-Jacobsen (2002) are real papers and the library tiers them
T2 and T3 respectively. They are included so they can be MEASURED against the
same bar as everything else, not because they are expected to survive it.
"""
from ..research import indicators
from . import factors
from .base import LONG, Strategy


class LowVolatilityStrategy(Strategy):
    """Baker, Bradley & Wurgler (2011) — the low-volatility anomaly."""

    name = "low_volatility"
    label = "Low volatility"
    description = ("Holds the calmest names in the universe, which have "
                   "historically delivered better risk-adjusted returns than the "
                   "wildest — the opposite of what the textbook predicts.")
    regimes = ("TRENDING_UP", "SIDEWAYS", "VOLATILITY_SQUEEZE")

    defaults = {
        "vol_lookback_bars": 120,     # longer than the risk filters: this IS the signal
        "top_pct": 10.0,              # the calmest decile
        "min_universe": 5,
        "require_above_trend": True,  # calm is not the same as rising
        "trend_ma_bars": 200,
        "rebalance": "monthly",
        "stop_atr": 2.5,
        "reward_multiple": 2.5,
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
        # Calm relative to its OWN segment. A currency pair is calm compared
        # with any equity, so pooled ranking would fill this book with FX and
        # call it a low-volatility equity strategy.
        stats = universe.volatility(ctx.ticker, factors.as_of_of(ctx.df),
                                    lookback_bars=p["vol_lookback_bars"],
                                    eligible=factors.peer_group(ctx))
        if stats is None or stats["percentile"] > float(p["top_pct"]):
            return []

        reasons = list(ctx.regime.get("reasons", []))
        reasons.append(f"Among the calmest {stats['percentile']:.0f}% of "
                       f"{stats['count']} instruments — {stats['value']:.0f}% "
                       "annualised volatility")
        reasons.append(
            "Low volatility has historically earned better risk-adjusted returns "
            "than high, which the textbook says should not happen; the standard "
            "explanation is that investors who cannot borrow bid up volatile "
            "names instead, leaving the calm ones cheap")

        if p["require_above_trend"]:
            above = indicators.above_moving_average(ctx.df["Close"], p["trend_ma_bars"])
            if above is not True:
                return []
            reasons.append(f"Still above its own {int(p['trend_ma_bars'])}-day average — "
                           "a quiet decline is calm and worthless")

        levels = factors.atr_levels(price, atr, LONG, p["stop_atr"], p["reward_multiple"])
        if levels is None:
            return []
        idea = self.build(ctx, LONG, entry=price, stop=levels[0], target=levels[1],
                          reasons=reasons,
                          headline=(f"Calmest {stats['percentile']:.0f}% of the "
                                    f"universe at {stats['value']:.0f}% volatility"),
                          meta={"realized_vol_pct": round(stats["value"], 2),
                                "vol_percentile": stats["percentile"],
                                "universe_size": stats["count"]})
        return [idea] if idea else []


class TurnOfMonthStrategy(Strategy):
    """Ariel (1987) — returns cluster around the month boundary."""

    name = "turn_of_month"
    label = "Turn of month"
    description = ("Holds only across the turn of the month, when returns have "
                   "historically clustered. A calendar effect — cheap to compute "
                   "and easy to fool yourself with.")
    regimes = ("TRENDING_UP", "SIDEWAYS", "VOLATILITY_SQUEEZE")

    defaults = {
        "days_before": 3,        # enter this many trading days before month end
        "hold_bars": 6,          # across the boundary and a few days into the new month
        "min_universe": 5,
        "top_pct": 25.0,         # only the stronger half-ish; do not buy everything
        "lookback_bars": 252,
        "rebalance": "any",      # its own calendar rule replaces the monthly gate
        "stop_atr": 2.0,
        "reward_multiple": 1.5,
    }

    def detect(self, ctx):
        p = ctx.params
        price, atr = ctx.snapshot.get("price"), ctx.snapshot.get("atr")
        if not indicators.is_finite(price):
            return []
        if not self._near_month_end(ctx.df, int(p["days_before"])):
            return []

        universe = ctx.cross_section
        if universe is not None and len(universe) >= int(p["min_universe"]):
            stats = universe.momentum(ctx.ticker, factors.as_of_of(ctx.df),
                                      lookback_bars=p["lookback_bars"], skip_bars=21,
                                      eligible=factors.peer_group(ctx))
            if stats is None or stats["percentile"] > float(p["top_pct"]):
                return []

        reasons = list(ctx.regime.get("reasons", []))
        reasons.append(
            f"Within {int(p['days_before'])} trading days of the month end, the "
            "window in which returns have historically clustered (Ariel 1987)")
        reasons.append(
            "A calendar effect, and the calendar is a small enough space to be "
            "searched exhaustively — this is included to be measured against the "
            "same bar as everything else, not because it is expected to survive it")

        levels = factors.atr_levels(price, atr, LONG, p["stop_atr"], p["reward_multiple"])
        if levels is None:
            return []
        idea = self.build(ctx, LONG, entry=price, stop=levels[0], target=levels[1],
                          reasons=reasons,
                          headline="Turn-of-month window",
                          meta={"hold_bars": int(p["hold_bars"])})
        return [idea] if idea else []

    @staticmethod
    def _near_month_end(df, days_before):
        """True when the latest bar is within `days_before` of the month's last.

        Uses only bars up to today: the test is whether the calendar month
        changes within the next few CALENDAR days, which is knowable now.
        """
        if df is None or len(df) < 2:
            return False
        last = df.index[-1]
        if not hasattr(last, "day"):
            return False
        import calendar as cal
        days_in_month = cal.monthrange(last.year, last.month)[1]
        # Trading days are roughly 5/7 of calendar days.
        return (days_in_month - last.day) <= max(1, round(days_before * 7 / 5))


class HalloweenStrategy(Strategy):
    """Bouman & Jacobsen (2002) — 'sell in May'."""

    name = "halloween"
    label = "Halloween effect"
    description = ("Holds November to April and stands aside May to October. The "
                   "weakest thing in the library by evidence; included so it can "
                   "be measured rather than argued about.")
    regimes = ("TRENDING_UP", "SIDEWAYS", "VOLATILITY_SQUEEZE")

    defaults = {
        "in_months": [11, 12, 1, 2, 3, 4],
        "min_universe": 5,
        "top_pct": 25.0,
        "lookback_bars": 252,
        "rebalance": "monthly",
        "stop_atr": 3.0,
        "reward_multiple": 3.0,
    }

    def detect(self, ctx):
        p = ctx.params
        price, atr = ctx.snapshot.get("price"), ctx.snapshot.get("atr")
        if not indicators.is_finite(price):
            return []
        if not factors.is_rebalance_bar(ctx.df, p["rebalance"]):
            return []
        last = factors.as_of_of(ctx.df)
        if not hasattr(last, "month") or last.month not in set(p["in_months"]):
            return []

        universe = ctx.cross_section
        if universe is not None and len(universe) >= int(p["min_universe"]):
            stats = universe.momentum(ctx.ticker, last,
                                      lookback_bars=p["lookback_bars"], skip_bars=21,
                                      eligible=factors.peer_group(ctx))
            if stats is None or stats["percentile"] > float(p["top_pct"]):
                return []

        reasons = list(ctx.regime.get("reasons", []))
        reasons.append(f"Month {last.month} is inside the November-April window "
                       "the effect claims (Bouman-Jacobsen 2002)")
        reasons.append(
            "Tiered T3 by the library that proposed it: a six-month calendar "
            "split found by searching a calendar. Measured here so it can be "
            "dismissed on evidence rather than on taste")

        levels = factors.atr_levels(price, atr, LONG, p["stop_atr"], p["reward_multiple"])
        if levels is None:
            return []
        idea = self.build(ctx, LONG, entry=price, stop=levels[0], target=levels[1],
                          reasons=reasons, headline="Inside the November-April window",
                          meta={"month": last.month})
        return [idea] if idea else []
