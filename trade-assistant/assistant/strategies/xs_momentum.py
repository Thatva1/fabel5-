"""Cross-sectional momentum rotation — buy the leaders, monthly.

Papers: Jegadeesh & Titman (1993); Asness, Moskowitz & Pedersen, *Value and
Momentum Everywhere* (2013); Daniel & Moskowitz, *Momentum Crashes* (2016) for
the overlay.

The rule, in one line: each month, rank every instrument in the universe by its
return over the last twelve months skipping the most recent one, and hold the
top handful. That is the whole signal. There is no chart pattern, no volume
confirmation, no entry trigger — the name is either near the top of the
universe on rebalance day or it is not.

Two things make this different in kind from everything this library held before,
and both are the point:

**It is a RELATIVE judgement.** "Up 40% over the year" means nothing on its own;
what matters is whether that is the best in the universe or the worst. So this
strategy is the first here that cannot work from one ticker's data. It needs the
cross-section, and when it does not have one it produces nothing rather than
quietly falling back to an absolute threshold, which would be a different
strategy wearing this one's name.

**It skips the most recent month.** Short-horizon returns reverse; twelve-month
returns continue. Rank on the last twelve months INCLUDING the last one and the
two effects fight, which is most of the difference between the published result
and the version people build from memory.

The overlay is the other half. Momentum's fatal flaw is not that it stops
working, it is that it crashes: it loses several years of gains in a few weeks
coming off a market bottom, when the beaten-up names it is short rip upward.
Daniel & Moskowitz show that cutting exposure when the market is below trend or
volatility is spiking removes most of that. This uses the project's existing
market filter for exactly that job, so the strategy's crash protection and the
portfolio's risk-off logic are the same measurement rather than two that drift.
"""
from ..research import indicators, market_regime
from . import factors
from .base import LONG, Strategy


class CrossSectionalMomentumStrategy(Strategy):
    name = "xs_momentum"
    label = "Cross-sectional momentum"
    description = ("Ranks the whole universe by its 12-month return skipping the most "
                   "recent month and buys the leaders each month, standing aside when "
                   "the market itself is below trend.")
    regimes = ("TRENDING_UP", "SIDEWAYS", "VOLATILITY_SQUEEZE")

    defaults = {
        # The 12-1 signal. 252 bars is a trading year, 21 bars a trading month.
        "lookback_bars": 252,
        "skip_bars": 21,
        # Held as a FRACTION of the peer group, floored and capped. A fixed
        # count cannot serve both a five-hundred-name equity segment and a
        # twelve-instrument currency book at once.
        "top_pct": 3.0,
        "min_top_n": 2,
        "max_top_n": 15,
        # Low enough that a currency or rates segment qualifies. Ranking within
        # a segment means the group is small by design, not by accident.
        "min_universe": 5,
        "rebalance": "monthly",
        # The anti-crash overlay. Switching this off gets you the raw 1993 rule
        # and its 2009-style drawdowns; it is on by default for that reason.
        "require_market_trend": True,
        # A name whose own volatility has exploded is the one that hurts most in
        # a momentum unwind, whatever the market is doing.
        "vol_lookback_bars": 60,
        "max_vol_pct": 60.0,
        "target_vol_pct": 15.0,       # reported for the portfolio layer, not applied here
        # Risk control grafted on by this engine — see factors.atr_levels.
        "stop_atr": 3.0,
        "reward_multiple": 3.0,
    }

    def detect(self, ctx):
        p = ctx.params
        price, atr = ctx.snapshot.get("price"), ctx.snapshot.get("atr")
        if not indicators.is_finite(price):
            return []

        # 1. Rebalance day. Outside it this strategy has nothing to say, which
        #    is most of why its turnover is a tenth of a daily rule set's.
        if not factors.is_rebalance_bar(ctx.df, p["rebalance"]):
            return []

        # 2. The cross-section itself. No universe, no ranking, no idea.
        universe = ctx.cross_section
        if universe is None or len(universe) < int(p["min_universe"]):
            return []
        as_of = factors.as_of_of(ctx.df)
        # The eligible set FIRST, then the ranking inside it. Ranking the whole
        # market and vetoing the winners afterwards is a different strategy and
        # selects nothing at this width: over 1,500 liquid US names the top
        # twelve were up 700-3,900% with 85-226% volatility, so a 60% ceiling
        # applied after the fact rejected all twelve and the rotation held
        # nothing at all. Rank inside the set you are willing to own.
        eligible = universe.calm_enough(as_of, p["max_vol_pct"],
                                        lookback_bars=p["vol_lookback_bars"])
        # Ranked against its OWN segment. Pooled with equities, no currency pair
        # or futures contract can ever place — see factors.peer_group.
        peers = factors.peer_group(ctx)
        if peers is not None:
            eligible = peers if eligible is None else (eligible & peers)
        if eligible is not None and len(eligible) < int(p["min_universe"]):
            return []
        stats = universe.momentum(ctx.ticker, as_of,
                                  lookback_bars=p["lookback_bars"],
                                  skip_bars=p["skip_bars"],
                                  eligible=eligible)
        if stats is None:
            return []
        top_n = factors.slots(stats["count"], p["top_pct"],
                              p["min_top_n"], p["max_top_n"])
        if stats["rank"] > top_n:
            return []

        reasons = list(ctx.regime.get("reasons", []))
        months = int(p["lookback_bars"]) / 21
        reasons.append(
            f"Ranked {stats['rank']} of {stats['count']} on {months:.0f}-month "
            f"momentum skipping the last month ({factors.pct(stats['value'])}) "
            f"— inside the top {top_n}")
        reasons.append(
            f"Ranked against the {stats['count']} instruments in its own segment "
            "that are calm enough to own — a currency pair is compared with "
            "currency pairs, not with five hundred shares, because pooled with "
            "those it could never place however well it had actually done")
        reasons.append(
            f"The most recent {int(p['skip_bars'])} sessions are excluded from the "
            "signal on purpose: short-horizon moves tend to reverse, and including "
            "them mixes a reversal signal into a momentum one")

        # 3. The crash overlay: market above its own trend, volatility not spiking.
        if p["require_market_trend"]:
            verdict = self._market_state(ctx)
            if verdict is None:
                return []
            reasons.extend(verdict)

        # 4. Own volatility. Measured, not assumed — an unmeasurable one fails.
        vol, vol_ok = factors.volatility_check(
            ctx.df["Close"], p["vol_lookback_bars"], p["max_vol_pct"])
        if not vol_ok:
            return []
        reasons.append(f"Realised volatility {vol:.0f}% annualised is inside the "
                       f"{p['max_vol_pct']:.0f}% ceiling for a leader worth holding")

        levels = factors.atr_levels(price, atr, LONG, p["stop_atr"], p["reward_multiple"])
        if levels is None:
            return []
        stop, target = levels

        idea = self.build(
            ctx, LONG, entry=price, stop=stop, target=target, reasons=reasons,
            headline=(f"Momentum leader — rank {stats['rank']} of {stats['count']} "
                      f"in its segment on the {months:.0f}-1 signal"),
            meta={
                "momentum_rank": stats["rank"],
                "universe_size": stats["count"],
                "slots": top_n,
                "momentum_percentile": stats["percentile"],
                "momentum_return_pct": round(stats["value"] * 100, 2),
                "realized_vol_pct": vol,
                # Not consumed by the per-ticker sizer; the portfolio layer can.
                "inverse_vol_weight": factors.inverse_vol_weight(vol, p["target_vol_pct"]),
                "rebalance": p["rebalance"],
            })
        return [idea] if idea else []

    @staticmethod
    def _market_state(ctx):
        """The Daniel-Moskowitz overlay, or None when it says stand aside.

        Deliberately fails closed. "I could not tell whether the market was
        falling" and "the market was not falling" are the same answer to a
        strategy that ignores the difference, and the whole purpose of this
        overlay is the handful of months where the difference is everything.
        """
        benchmark = factors.benchmark_upto(ctx.benchmark_closes, factors.as_of_of(ctx.df))
        if benchmark is None or len(benchmark) == 0:
            return None
        state = market_regime.classify_market(benchmark, ctx.config)
        if state["state"] != market_regime.RISK_ON:
            return None
        return list(state["reasons"]) + [
            "Momentum's characteristic loss is a crash off a market bottom, not a "
            "slow bleed; this is the filter that steps aside for it"]
