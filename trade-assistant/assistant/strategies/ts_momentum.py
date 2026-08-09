"""Time-series momentum (trend-following) — does this instrument's OWN past predict it?

Paper: Moskowitz, Ooi & Pedersen, *Time Series Momentum* (2012).

The rule is almost embarrassingly simple: if an instrument's own return over the
last twelve months is positive, be long it; if it is negative, be short it.
Rebalance monthly. Size each position so they all carry the same volatility.
That is the entire classic CTA book, and it holds across futures, indices,
currencies and rates over a century of data.

Why it is here alongside cross-sectional momentum, when both have "momentum" in
the name: they are asking different questions and their answers diverge exactly
when it matters. Cross-sectional momentum is relative and always fully invested
in something — in a bear market it holds whichever names are falling least.
Time-series momentum is absolute: when everything's own twelve-month return
turns negative, it goes flat or short and simply stops participating. That is
the second, uncorrelated engine, and it is the one that is not in the market
during a drawdown.

**Where to point it.** The evidence is strongest on index ETFs and futures, and
weakest on individual shares, where single-name risk swamps the trend signal.
This runs on whatever is in the watchlist because that is how the engine works —
but a result from single mega-cap names is not a test of this paper. Point it at
index instruments if you want to know whether it works.

One filter is added beyond the published rule: price must agree with its own
long-term average, not just its twelve-month return. A name that rose 30% over
the year and has spent the last four months collapsing has a positive signal and
a broken trend. Set `require_ma_agreement: false` for the unmodified rule.
"""
from ..research import indicators
from . import factors
from .base import LONG, SHORT, Strategy


class TimeSeriesMomentumStrategy(Strategy):
    name = "ts_momentum"
    label = "Time-series momentum"
    description = ("Goes long an instrument whose own 12-month return is positive and "
                   "short one whose own is negative, rebalanced monthly — the classic "
                   "trend-following rule, strongest on index instruments.")
    regimes = ("TRENDING_UP", "TRENDING_DOWN", "SIDEWAYS", "VOLATILITY_SQUEEZE")

    defaults = {
        "lookback_bars": 252,          # the instrument's own trailing year
        "skip_bars": 0,                # the published rule does NOT skip a month
        "min_abs_return_pct": 0.0,     # 0 = pure sign, as published
        "rebalance": "monthly",
        # The added confirmation. Off gives the unmodified 2012 rule.
        "require_ma_agreement": True,
        "confirm_ma_bars": 200,
        "vol_lookback_bars": 60,
        "max_vol_pct": 80.0,           # looser than the rotation: trends can be wild
        "target_vol_pct": 15.0,        # reported for the portfolio layer
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

        closes = ctx.df["Close"]
        trailing = indicators.total_return(closes, p["lookback_bars"], p["skip_bars"])
        if trailing is None:
            return []

        threshold = float(p["min_abs_return_pct"]) / 100
        if abs(trailing) <= threshold:
            return []
        direction = LONG if trailing > 0 else SHORT
        long_side = direction == LONG

        months = int(p["lookback_bars"]) / 21
        reasons = list(ctx.regime.get("reasons", []))
        reasons.append(
            f"Its own {months:.0f}-month return is {factors.pct(trailing)} — "
            f"{'positive, so the rule is long' if long_side else 'negative, so the rule is short'}")
        reasons.append(
            "This is an absolute signal, not a relative one: when every instrument's "
            "own trailing year turns negative this strategy stops participating "
            "instead of rotating into whatever is falling least")

        # The added confirmation: a positive year does not survive a broken trend.
        if p["require_ma_agreement"]:
            above = indicators.above_moving_average(closes, p["confirm_ma_bars"])
            if above is None or above is not long_side:
                return []
            reasons.append(
                f"Price is {'above' if long_side else 'below'} its own "
                f"{int(p['confirm_ma_bars'])}-day average, so the trailing year and the "
                "current trend agree rather than pointing opposite ways")

        vol, vol_ok = factors.volatility_check(
            closes, p["vol_lookback_bars"], p["max_vol_pct"])
        if not vol_ok:
            return []
        weight = factors.inverse_vol_weight(vol, p["target_vol_pct"])
        reasons.append(
            f"Realised volatility {vol:.0f}% annualised; the paper's inverse-volatility "
            f"weight for that is {weight}x — recorded for the portfolio layer, not "
            "applied by the per-trade sizer, which sizes off the stop distance instead")

        levels = factors.atr_levels(price, atr, direction, p["stop_atr"], p["reward_multiple"])
        if levels is None:
            return []
        stop, target = levels

        idea = self.build(
            ctx, direction, entry=price, stop=stop, target=target, reasons=reasons,
            headline=(f"Trend-following {'long' if long_side else 'short'} — its own "
                      f"{months:.0f}-month return is {factors.pct(trailing)}"),
            meta={
                "trailing_return_pct": round(trailing * 100, 2),
                "lookback_bars": int(p["lookback_bars"]),
                "realized_vol_pct": vol,
                "inverse_vol_weight": weight,
                "rebalance": p["rebalance"],
            })
        return [idea] if idea else []
