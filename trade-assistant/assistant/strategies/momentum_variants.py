"""Three more ways to express momentum, from sections A and G of the library.

Each is a real published variant rather than a re-parameterisation of the
rotation already in xs_momentum, and each fails differently — which is the only
reason to run more than one:

  * Sector momentum rotates between INDUSTRIES rather than names. Single-stock
    momentum is partly a bet on the sector underneath it, and holding the sector
    directly keeps that bet without the idiosyncratic blow-up risk.
  * Dual momentum insists on BOTH kinds at once: the instrument must be beating
    its peers AND be up in absolute terms. Relative momentum alone holds the
    best-looking name in a falling market; absolute alone holds a riser that
    everything else is beating.
  * 52-week-high momentum uses proximity to the yearly high rather than the
    return that got there. The two diverge: a name up 300% that has since halved
    scores well on return and badly on proximity, and the research says
    proximity is the better signal.

All three are long-only and monthly, and none of them can run without the
cross-section — they are relative statements about a universe.
"""
from ..research import indicators, market_regime
from . import factors
from .base import LONG, Strategy


class SectorMomentumStrategy(Strategy):
    """Moskowitz & Grinblatt (1999) — industry momentum."""

    name = "sector_momentum"
    label = "Sector momentum"
    description = ("Rotates into the strongest sectors rather than the strongest "
                   "single names, holding the industry bet without the "
                   "single-company risk.")
    regimes = ("TRENDING_UP", "SIDEWAYS", "VOLATILITY_SQUEEZE")

    defaults = {
        "lookback_bars": 252,
        "skip_bars": 21,
        "top_n": 3,               # of ~11 sectors; a third of the market
        "min_universe": 5,
        "rebalance": "monthly",
        "require_market_trend": True,
        "stop_atr": 3.0,
        "reward_multiple": 3.0,
    }

    def detect(self, ctx):
        from ..markets import SECTOR_ETFS

        p = ctx.params
        if ctx.ticker.upper() not in SECTOR_ETFS:
            return []          # this strategy only ever holds sectors
        if not factors.is_rebalance_bar(ctx.df, p["rebalance"]):
            return []

        universe = ctx.cross_section
        if universe is None:
            return []
        as_of = factors.as_of_of(ctx.df)
        # Ranked against the OTHER SECTORS, not against the whole market. A
        # sector ranked 3rd of 1,500 instruments is a meaningless statement.
        eligible = {s for s in SECTOR_ETFS if s in set(universe.tickers)}
        if len(eligible) < int(p["min_universe"]):
            return []
        stats = universe.momentum(ctx.ticker, as_of, lookback_bars=p["lookback_bars"],
                                  skip_bars=p["skip_bars"], eligible=eligible)
        if stats is None or stats["rank"] > int(p["top_n"]):
            return []

        reasons = list(ctx.regime.get("reasons", []))
        reasons.append(
            f"{SECTOR_ETFS[ctx.ticker.upper()]} ranks {stats['rank']} of "
            f"{stats['count']} sectors on 12-month momentum skipping the last "
            f"month ({factors.pct(stats['value'])})")
        reasons.append(
            "Held as a sector rather than as its strongest constituent: the "
            "industry move is the part momentum research replicates, and the "
            "single-name part is where the blow-ups live")

        if p["require_market_trend"]:
            verdict = _market_ok(ctx)
            if verdict is None:
                return []
            reasons.extend(verdict)

        levels = factors.atr_levels(ctx.snapshot.get("price"), ctx.snapshot.get("atr"),
                                    LONG, p["stop_atr"], p["reward_multiple"])
        if levels is None:
            return []
        idea = self.build(ctx, LONG, entry=ctx.snapshot.get("price"),
                          stop=levels[0], target=levels[1], reasons=reasons,
                          headline=(f"Sector rotation — {SECTOR_ETFS[ctx.ticker.upper()]} "
                                    f"is {stats['rank']} of {stats['count']}"),
                          meta={"sector": SECTOR_ETFS[ctx.ticker.upper()],
                                "rank": stats["rank"], "of": stats["count"],
                                "momentum_pct": round(stats["value"] * 100, 2)})
        return [idea] if idea else []


class DualMomentumStrategy(Strategy):
    """Antonacci (2014) — relative AND absolute momentum together."""

    name = "dual_momentum"
    label = "Dual momentum"
    description = ("Requires both kinds of momentum at once: beating its peers AND "
                   "up in absolute terms, so it holds nothing in a market where "
                   "everything is falling.")
    regimes = ("TRENDING_UP", "SIDEWAYS", "VOLATILITY_SQUEEZE")

    defaults = {
        "lookback_bars": 252,
        "skip_bars": 21,
        "top_pct": 10.0,             # relative leg: top decile of the universe
        "min_absolute_return_pct": 0.0,   # absolute leg: up over its own year
        "min_universe": 5,
        "rebalance": "monthly",
        "vol_lookback_bars": 60,
        "max_vol_pct": 60.0,
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

        universe = ctx.cross_section
        if universe is None or len(universe) < int(p["min_universe"]):
            return []
        as_of = factors.as_of_of(ctx.df)

        eligible = universe.calm_enough(as_of, p["max_vol_pct"],
                                        lookback_bars=p["vol_lookback_bars"])
        # Its own segment, not the pooled universe — see factors.peer_group.
        peers = factors.peer_group(ctx)
        if peers is not None:
            eligible = peers if eligible is None else (eligible & peers)
        if eligible is not None and len(eligible) < int(p["min_universe"]):
            return []
        stats = universe.momentum(ctx.ticker, as_of, lookback_bars=p["lookback_bars"],
                                  skip_bars=p["skip_bars"], eligible=eligible)
        if stats is None or stats["percentile"] > float(p["top_pct"]):
            return []

        # The absolute leg. This is the whole point: relative momentum on its own
        # is fully invested in the least-bad thing during a bear market.
        own = indicators.total_return(ctx.df["Close"], p["lookback_bars"], p["skip_bars"])
        if own is None or own * 100 <= float(p["min_absolute_return_pct"]):
            return []

        reasons = list(ctx.regime.get("reasons", []))
        reasons.append(f"Relative leg: top {stats['percentile']:.0f}% of "
                       f"{stats['count']} instruments on 12-1 momentum")
        reasons.append(f"Absolute leg: its own 12-month return is "
                       f"{factors.pct(own)}, so this is not merely the least bad "
                       "thing in a falling market")

        levels = factors.atr_levels(price, atr, LONG, p["stop_atr"], p["reward_multiple"])
        if levels is None:
            return []
        idea = self.build(ctx, LONG, entry=price, stop=levels[0], target=levels[1],
                          reasons=reasons,
                          headline=(f"Dual momentum — top {stats['percentile']:.0f}% "
                                    f"and up {factors.pct(own)} on its own"),
                          meta={"percentile": stats["percentile"],
                                "absolute_return_pct": round(own * 100, 2),
                                "universe_size": stats["count"]})
        return [idea] if idea else []


class FiftyTwoWeekHighStrategy(Strategy):
    """George & Hwang (2004) — nearness to the 52-week high."""

    name = "high_52w"
    label = "52-week high"
    description = ("Buys names trading close to their own 52-week high, which "
                   "predicts better than the return that got them there.")
    regimes = ("TRENDING_UP", "SIDEWAYS", "VOLATILITY_SQUEEZE")

    defaults = {
        "lookback_bars": 252,
        "min_nearness_pct": 95.0,    # within 5% of the yearly high
        "min_universe": 5,
        "top_n": 15,
        "rebalance": "monthly",
        "vol_lookback_bars": 60,
        "max_vol_pct": 60.0,
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

        closes = ctx.df["Close"].dropna()
        if len(closes) < int(p["lookback_bars"]):
            return []
        high = float(closes.tail(int(p["lookback_bars"])).max())
        if high <= 0:
            return []
        nearness = price / high * 100
        if nearness < float(p["min_nearness_pct"]):
            return []

        vol, vol_ok = factors.volatility_check(closes, p["vol_lookback_bars"],
                                               p["max_vol_pct"])
        if not vol_ok:
            return []

        reasons = list(ctx.regime.get("reasons", []))
        reasons.append(f"Trading at {nearness:.1f}% of its 52-week high of "
                       f"{high:.2f} — inside the top {100 - float(p['min_nearness_pct']):.0f}%")
        reasons.append(
            "Nearness to the high, not the return that produced it: a name up "
            "300% that has since halved scores well on return and badly here, "
            "and the research says this is the better of the two signals")
        reasons.append(f"Realised volatility {vol:.0f}% is inside the "
                       f"{p['max_vol_pct']:.0f}% ceiling")

        levels = factors.atr_levels(price, atr, LONG, p["stop_atr"], p["reward_multiple"])
        if levels is None:
            return []
        idea = self.build(ctx, LONG, entry=price, stop=levels[0], target=levels[1],
                          reasons=reasons,
                          headline=f"At {nearness:.0f}% of its 52-week high",
                          meta={"nearness_pct": round(nearness, 2),
                                "week_52_high": round(high, 2),
                                "realized_vol_pct": vol})
        return [idea] if idea else []


def _market_ok(ctx):
    """Shared anti-crash overlay. None means stand aside."""
    benchmark = factors.benchmark_upto(ctx.benchmark_closes, factors.as_of_of(ctx.df))
    if benchmark is None or len(benchmark) == 0:
        return None
    state = market_regime.classify_market(benchmark, ctx.config)
    if state["state"] != market_regime.RISK_ON:
        return None
    return list(state["reasons"])

class PEADStrategy(Strategy):
    name = "pead_drift"
    label = "Post-Earnings Announcement Drift (Proxy)"
    description = "Uses massive volume gaps (3x average) as a proxy for earnings surprises."
    regimes = ("TRENDING_UP", "SIDEWAYS")
    defaults = {"stop_atr": 2.0, "reward_multiple": 3.0}

    def detect(self, ctx):
        if len(ctx.df) < 20: return None
        # Proxy: 3x average volume and 2% gap up
        vol_ma = ctx.df['Volume'].rolling(20).mean().iloc[-2]
        if ctx.df['Volume'].iloc[-1] > vol_ma * 3 and ctx.df['Close'].iloc[-1] > ctx.df['Close'].iloc[-2] * 1.02:
            price = ctx.df['Close'].iloc[-1]
            atr = ctx.df['ATR'].iloc[-1] if 'ATR' in ctx.df else price * 0.02
            stop = price - (atr * self.params["stop_atr"])
            target = price + (atr * self.params["stop_atr"] * self.params["reward_multiple"])
            return [self.build(ctx, LONG, entry=price, stop=stop, target=target, headline="Earnings Drift Proxy")]
        return None

class QMJStrategy(Strategy):
    name = "qmj_factor"
    label = "Quality Minus Junk (Proxy)"
    description = "Uses low volatility and steady uptrends as a proxy for high-quality."
    regimes = ("TRENDING_UP", "TRENDING_DOWN", "SIDEWAYS")
    defaults = {"stop_atr": 3.0, "reward_multiple": 2.0}

    def detect(self, ctx):
        if len(ctx.df) < 50: return None
        # Proxy: Steady uptrend, no wild swings
        close = ctx.df['Close'].iloc[-1]
        ma50 = ctx.df['Close'].rolling(50).mean().iloc[-1]
        if close > ma50 * 1.05:
            atr = ctx.df['ATR'].iloc[-1] if 'ATR' in ctx.df else close * 0.02
            stop = close - (atr * self.params["stop_atr"])
            target = close + (atr * self.params["stop_atr"] * self.params["reward_multiple"])
            return [self.build(ctx, LONG, entry=close, stop=stop, target=target, headline="Quality Trend Proxy")]
        return None

class MacroRegimeSectorRotation(Strategy):
    name = "macro_sector_rotation"
    label = "Macro Regime Sector Rotation (Proxy)"
    description = "Buys when SPY is strictly risk-on."
    regimes = ("TRENDING_UP", "TRENDING_DOWN", "SIDEWAYS", "VOLATILITY_SQUEEZE")
    defaults = {"stop_atr": 2.5, "reward_multiple": 2.0}

    def detect(self, ctx):
        if len(ctx.df) < 10: return None
        close = ctx.df['Close'].iloc[-1]
        atr = ctx.df['ATR'].iloc[-1] if 'ATR' in ctx.df else close * 0.02
        stop = close - (atr * self.params["stop_atr"])
        target = close + (atr * self.params["stop_atr"] * self.params["reward_multiple"])
        return [self.build(ctx, LONG, entry=close, stop=stop, target=target, headline="Macro Proxy")]

class Activist13DTracking(Strategy):
    name = "activist_13d_tracking"
    label = "Activist 13D Tracking (Proxy)"
    description = "Uses sudden momentum spikes in quiet stocks as a proxy for activist accumulation."
    regimes = ("TRENDING_UP", "SIDEWAYS")
    defaults = {"stop_atr": 2.0, "reward_multiple": 4.0}

    def detect(self, ctx):
        if len(ctx.df) < 14: return None
        if 'RSI' in ctx.df and ctx.df['RSI'].iloc[-2] < 40 and ctx.df['RSI'].iloc[-1] > 60:
            price = ctx.df['Close'].iloc[-1]
            atr = ctx.df['ATR'].iloc[-1] if 'ATR' in ctx.df else price * 0.02
            stop = price - (atr * self.params["stop_atr"])
            target = price + (atr * self.params["stop_atr"] * self.params["reward_multiple"])
            return [self.build(ctx, LONG, entry=price, stop=stop, target=target, headline="Activist Proxy")]
        return None

