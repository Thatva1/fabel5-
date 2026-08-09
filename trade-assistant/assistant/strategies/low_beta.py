"""Low-beta / low-volatility tilt — the defensive factor, long leg only.

Papers: Frazzini & Pedersen, *Betting Against Beta* (2014); Baker, Bradley &
Wurgler (2011) on the low-volatility anomaly.

The finding is one of the most uncomfortable in finance: low-beta and low-
volatility shares have historically delivered BETTER risk-adjusted returns than
high-beta ones, which is the opposite of what the textbook says should happen.
The standard explanation is leverage aversion — investors who want more return
but cannot or will not borrow bid up volatile shares instead, so the calm ones
stay cheap.

**Long leg only, on purpose.** The published factor is long low-beta, short
high-beta, levered so both legs have beta 1. The short leg is dropped here: it
needs leverage this system does not use, borrow costs it does not model, and
shorting has lost money in every backtest this project has run. A long-only
low-beta tilt is a weaker version of the paper and an honest one.

**Why it is worth running next to momentum.** It is the diversifier. Momentum
buys what has gone up, which in practice means the high-beta end of the market;
this buys the other end. They lose money at different times, which is the only
free lunch on offer.

One filter is added beyond the papers: the name must still be above its own
long-term average. Beta measures how much a share moves WITH the index, not
which way it is going, and a stock that has quietly halved on low beta scores
beautifully on the raw factor. Cheap insurance against buying a calm decline.
"""
from ..research import indicators
from . import factors
from .base import LONG, Strategy


class LowBetaStrategy(Strategy):
    name = "low_beta"
    label = "Low beta"
    description = ("Tilts toward names that move less than their index and are calm "
                   "relative to the rest of the universe, while still holding their "
                   "own long-term uptrend. Long only.")
    # Not TRENDING_DOWN: this classifier describes the NAME, not the market, and
    # a low-beta share in its own downtrend is precisely the calm decline the
    # trend filter below exists to refuse. The defensive value of this factor
    # shows up as holding names that keep their trend while the market loses its.
    regimes = ("TRENDING_UP", "SIDEWAYS", "VOLATILITY_SQUEEZE")

    defaults = {
        "beta_lookback_bars": 252,
        "max_beta": 0.85,
        "vol_lookback_bars": 60,
        # Cross-sectional when a universe is available: the calmest 40% of names.
        "max_vol_rank_pct": 40.0,
        # Absolute fallback when it is not. Both are stated in the reasons so it
        # is always clear which test the idea actually passed.
        "max_vol_pct": 30.0,
        "require_above_trend": True,
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

        closes = ctx.df["Close"]
        as_of = factors.as_of_of(ctx.df)

        # 1. Beta. The whole factor is this number, so no benchmark means no idea
        #    — there is nothing to be low-beta relative to.
        benchmark = factors.benchmark_upto(ctx.benchmark_closes, as_of)
        if benchmark is None or len(benchmark) == 0:
            return []
        beta = indicators.beta(closes, benchmark, lookback_bars=p["beta_lookback_bars"])
        if beta is None or beta > float(p["max_beta"]):
            return []

        reasons = list(ctx.regime.get("reasons", []))
        years = int(p["beta_lookback_bars"]) / 252
        reasons.append(
            f"Beta {beta:.2f} against its benchmark over {years:.0f} year(s) — it moves "
            f"about {beta * 100:.0f}% as much as the index, inside the "
            f"{p['max_beta']:.2f} ceiling")
        reasons.append(
            "Low-beta names have historically earned better risk-adjusted returns than "
            "high-beta ones (Frazzini-Pedersen 2014); only the long leg of that trade "
            "is taken here, because the short leg needs leverage this system does not use")

        # 2. Calm relative to its peers where possible, in absolute terms otherwise.
        vol = indicators.realized_vol(closes, lookback_bars=p["vol_lookback_bars"])
        if vol is None:
            return []
        rank = None
        if ctx.cross_section is not None and len(ctx.cross_section) >= 2:
            rank = ctx.cross_section.volatility(ctx.ticker, as_of,
                                                lookback_bars=p["vol_lookback_bars"])
        if rank is not None:
            if rank["percentile"] > float(p["max_vol_rank_pct"]):
                return []
            reasons.append(
                f"Realised volatility {vol:.0f}% annualised ranks {rank['rank']} calmest "
                f"of {rank['count']} in the universe — inside the quietest "
                f"{p['max_vol_rank_pct']:.0f}%")
        else:
            if vol > float(p["max_vol_pct"]):
                return []
            reasons.append(
                f"Realised volatility {vol:.0f}% annualised is under the "
                f"{p['max_vol_pct']:.0f}% absolute ceiling — no universe was available "
                "to rank it against its peers, so the absolute test was used")

        # 3. Calm is not the same as rising.
        if p["require_above_trend"]:
            above = indicators.above_moving_average(closes, p["trend_ma_bars"])
            if above is not True:
                return []
            reasons.append(
                f"Still above its own {int(p['trend_ma_bars'])}-day average — beta says "
                "how much a share moves with the index, not which way it is going, and a "
                "stock that has quietly halved scores well on beta alone")

        levels = factors.atr_levels(price, atr, LONG, p["stop_atr"], p["reward_multiple"])
        if levels is None:
            return []
        stop, target = levels

        idea = self.build(
            ctx, LONG, entry=price, stop=stop, target=target, reasons=reasons,
            headline=f"Defensive tilt — beta {beta:.2f}, still in its own uptrend",
            meta={
                "beta": beta,
                "realized_vol_pct": vol,
                "volatility_rank": None if rank is None else rank["rank"],
                "volatility_percentile": None if rank is None else rank["percentile"],
                "universe_size": None if rank is None else rank["count"],
                "rebalance": p["rebalance"],
            })
        return [idea] if idea else []
