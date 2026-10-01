"""Strategies that live and die inside one session.

WHY THIS IS SEPARATE FROM THE DAILY LIBRARY

Not because intraday is "the same rules, faster". It is not, and running the
daily library on five-minute bars would not produce more signals of the same
kind — it would produce different signals that no evidence supports. A trailing
year of returns computed over five-minute bars is a trailing three days.

The daily library's horizons are the horizons its research was conducted at.
These have their own, and the two libraries share only the idea contract:
levels in, a validated StrategyIdea out, no money computed anywhere.

WHAT AN INTRADAY IDEA CARRIES THAT A DAILY ONE DOES NOT

  * `max_hold_minutes` — the horizon the rule expects to work over. A rule that
    wants an hour must not be started with twenty minutes left, and one that
    has run its hour without resolving is a rule that was wrong, not one that
    needs longer. The daily engine expresses this in bars; minutes are the only
    unit that means the same thing at every bar size.

  * A reward:risk of ROUGHLY ONE, not three. This is the whole reason the
    intraday engine exists. Three-to-one on ATR-wide daily stops is achievable
    and takes months to pay, which is why a daily book cannot show realised
    profit day to day. An intraday rule banks a smaller multiple far more
    often, and the arithmetic that makes that work is hit rate, not reward.

WHAT DECIDES WHETHER ANY OF THIS IS TRADABLE

Costs, and nothing else. Almost every intraday edge that exists gross is
smaller than the spread plus commission it must pay. `research/intraday.py`
measures the breakeven cost — the round trip at which a rule stops earning —
and that number, not the return, is what says whether a rule belongs here.

CAUSALITY

Every strategy is handed `ctx.bars`, the session's bars UP TO AND INCLUDING the
current one, and physically cannot see later ones. This is the same structural
guarantee the daily backtest rests on, and it matters more here: an intraday
rule with one bar of look-ahead does not overstate its return slightly, it
turns a losing rule into a winner every time.
"""
import math
from dataclasses import dataclass, field
from typing import Optional

from .base import LONG, SHORT, Strategy, StrategyIdea, _dedupe, _validate_levels


@dataclass
class IntradayContext:
    """Everything an intraday strategy is allowed to see.

    `bars` is the causal slice: the session's bars up to and including now.
    `prev_close` is the previous session's closing price, carried because the
    two strongest rules in this library are defined against it — Gao et al.'s
    intraday momentum measures the first half hour FROM the previous close, and
    a gap is by definition the distance from it. Without it neither can be
    expressed at all, only approximated with the session's own open, which is a
    different and much weaker signal.
    """
    ticker: str
    bars: object                     # DataFrame: this session, up to now
    prev_close: Optional[float] = None
    bar_minutes: int = 5
    minutes_to_close: Optional[float] = None
    params: dict = field(default_factory=dict)
    config: dict = field(default_factory=dict)
    # Cumulative session arrays, computed ONCE for the whole session by a
    # caller that walks it bar by bar, and sliced here.
    #
    # Not an optimisation detail — an algorithmic one. `session_vwap` rebuilt
    # the entire cumulative average from the first bar on EVERY bar, which
    # makes one session O(n^2) in its own length and dominated the profile at
    # 1.6 of 5.8 seconds. A backtest is not slow here because it is written in
    # Python; it is slow because it does the same sum seventy-eight times.
    #
    # Optional, so a caller holding a single snapshot (the live book, the
    # dashboard) needs to know nothing about this and gets the same answers.
    precomputed: Optional[dict] = None

    @property
    def price(self):
        return float(self.bars["Close"].iloc[-1])

    @property
    def bar_count(self):
        return len(self.bars)

    def bars_for(self, minutes):
        """How many bars span this many minutes at this resolution.

        Windows are stated in MINUTES throughout. Stating them in bars made
        them mean different things at different resolutions — an opening range
        of six bars is the first half hour at five-minute bars and the first
        six hours at hourly, and a rule that cannot fire looks exactly like a
        rule that fired and lost.
        """
        return max(1, round(minutes / max(1, self.bar_minutes)))

    def session_vwap(self):
        """Volume-weighted average price for the session so far."""
        pre = self.precomputed
        if pre is not None:
            index = len(self.bars) - 1
            total = pre["cum_volume"][index]
            if not total:
                return None
            return float(pre["cum_pv"][index] / total)

        bars = self.bars
        typical = (bars["High"] + bars["Low"] + bars["Close"]) / 3
        volume = bars["Volume"].replace(0, 1)
        total = float(volume.sum())
        if not total:
            return None
        return float((typical * volume).sum() / total)

    def session_atr(self, minutes=30):
        """Average true range over the last `minutes` of this session.

        The intraday volatility unit. Session-local on purpose: a stop sized
        off yesterday's daily range is many times too wide for a rule that
        expects to be resolved within the hour, and a stop that is never
        reached is not a stop.
        """
        span = self.bars_for(minutes)
        pre = self.precomputed
        if pre is not None:
            end = len(self.bars)
            start = max(0, end - span)
            if end - start < 2:
                return None
            total = pre["cum_range"][end - 1]
            if start:
                total -= pre["cum_range"][start - 1]
            value = float(total / (end - start))
            return value if value > 0 and math.isfinite(value) else None

        window = self.bars.tail(span)
        if len(window) < 2:
            return None
        ranges = (window["High"] - window["Low"]).astype(float)
        value = float(ranges.mean())
        return value if value > 0 and math.isfinite(value) else None


def precompute(frame):
    """Cumulative sums a whole session's worth of contexts can share.

    Built once by a caller that walks a session bar by bar. Every value is a
    PREFIX sum, so a context holding the first `i` bars reads index `i-1` and
    can never see past its own slice — the causality guarantee survives the
    optimisation, which is the only reason it is allowed.
    """
    import numpy as np

    high = frame["High"].to_numpy(dtype=float)
    low = frame["Low"].to_numpy(dtype=float)
    close = frame["Close"].to_numpy(dtype=float)
    volume = frame["Volume"].to_numpy(dtype=float)
    volume = np.where(volume == 0, 1.0, volume)
    typical = (high + low + close) / 3.0
    return {
        "cum_pv": np.cumsum(typical * volume),
        "cum_volume": np.cumsum(volume),
        "cum_range": np.cumsum(high - low),
    }


class IntradayStrategy(Strategy):
    """Base for a rule that opens and closes inside one session.

    Reuses the daily library's level validation deliberately: a long whose stop
    sits above its entry is the same arithmetic mistake at any horizon, and it
    is the mistake that produces a negative risk-per-share and a position size
    of infinity.
    """

    # Minutes of session a rule needs before it will open anything. Checked by
    # the engine as well, but declared here because it is a property of the
    # RULE — the engine only knows what the clock says.
    max_hold_minutes = 120
    min_minutes_remaining = 20

    # Intraday rules are configured under their own block, so a name can be
    # reused across the two libraries without one silently reading the other's
    # settings.
    def params_for(self, config):
        block = (((config or {}).get("intraday") or {}).get("strategies") or {}) \
            .get(self.name) or {}
        overrides = {k: v for k, v in block.items() if k != "enabled"}
        return {**self.defaults, **overrides}

    def enabled(self, config):
        block = (((config or {}).get("intraday") or {}).get("strategies") or {}) \
            .get(self.name) or {}
        return bool(block.get("enabled", True))

    def detect(self, ctx: IntradayContext):
        raise NotImplementedError

    def build(self, ctx, direction, entry, stop, target, reasons,
              headline="", meta=None):
        """Assemble a validated intraday idea, or None if the levels are wrong."""
        levels = _validate_levels(direction, entry, stop, target)
        if levels is None:
            return None
        entry, stop, target, risk_per_share = levels

        meta = dict(meta or {})
        meta.setdefault("max_hold_minutes", self.max_hold_minutes)
        meta.setdefault("horizon", "intraday")
        meta.setdefault("bar_minutes", ctx.bar_minutes)

        return StrategyIdea(
            ticker=ctx.ticker,
            strategy=self.name,
            strategy_label=self.label,
            # Intraday rules are not gated on the daily regime. Tagging them
            # with one would invite the router to filter them by it, and the
            # daily regime says nothing about the next ninety minutes.
            regime="INTRADAY",
            direction=direction,
            entry=entry,
            stop=stop,
            target=target,
            risk_per_share=risk_per_share,
            reward_risk=round(abs(target - entry) / risk_per_share, 2),
            headline=headline or f"{self.label}: {direction}",
            reasons=_dedupe(reasons),
            meta=meta,
        )

    def has_room(self, ctx):
        """Is there enough session left for this rule to be given its chance?

        A rule started with less time than it needs is not the rule any more —
        it is a bet that the flat-by-close liquidation lands in the right place.
        """
        remaining = ctx.minutes_to_close
        return remaining is None or remaining >= self.min_minutes_remaining


# --- the rules ---------------------------------------------------------------

class OpeningRangeBreak(IntradayStrategy):
    """Break of the first half hour's range — Zarattini & Aziz (2023).

    The crudest instrument that would detect an intraday trend at all, and the
    one with the most independent replication behind it. The opening range is a
    real structure rather than a fitted one: it is where overnight information
    finishes being priced, and a break of it is the market disagreeing with the
    level it just spent thirty minutes agreeing on.

    The stop is the OTHER side of the range, not an ATR multiple. That is the
    point of the rule — the range is the thing being bet against, so the trade
    is wrong exactly when price goes back inside it and out the far side.
    """

    name = "opening_range_break"
    label = "Opening range break"
    description = ("Buys a break above the first half hour's high, sells a break "
                   "below its low. Wrong when price crosses the whole range back.")
    max_hold_minutes = 180
    min_minutes_remaining = 45

    defaults = {
        "range_minutes": 30,
        "reward_multiple": 1.0,     # not 3.0 — see the module docstring
        # A range wider than this is a session already in chaos; a break of it
        # has no room left to pay for the risk it takes.
        "max_range_pct": 3.0,
        "min_range_pct": 0.15,      # too tight to be a real agreement
    }

    def detect(self, ctx):
        p = ctx.params
        span = ctx.bars_for(p["range_minutes"])
        if ctx.bar_count <= span or not self.has_room(ctx):
            return None

        opening = ctx.bars.iloc[:span]
        high, low = float(opening["High"].max()), float(opening["Low"].min())
        width = high - low
        if width <= 0:
            return None

        mid = (high + low) / 2
        width_pct = width / mid * 100
        if not (p["min_range_pct"] <= width_pct <= p["max_range_pct"]):
            return None

        price = ctx.price
        reward = float(p["reward_multiple"])
        if price > high:
            risk = price - low
            return self.build(
                ctx, LONG, entry=price, stop=low, target=price + risk * reward,
                headline=(f"Broke above the opening half hour's high of {high:.2f}"),
                reasons=[f"First {p['range_minutes']} minutes ranged {low:.2f}–{high:.2f}"
                         f" ({width_pct:.2f}% wide)",
                         f"Price {price:.2f} is above that high",
                         "Wrong if price crosses back through the whole range"],
                meta={"range_high": round(high, 4), "range_low": round(low, 4),
                      "range_pct": round(width_pct, 3)})
        if price < low:
            risk = high - price
            return self.build(
                ctx, SHORT, entry=price, stop=high, target=price - risk * reward,
                headline=(f"Broke below the opening half hour's low of {low:.2f}"),
                reasons=[f"First {p['range_minutes']} minutes ranged {low:.2f}–{high:.2f}"
                         f" ({width_pct:.2f}% wide)",
                         f"Price {price:.2f} is below that low",
                         "Wrong if price crosses back through the whole range"],
                meta={"range_high": round(high, 4), "range_low": round(low, 4),
                      "range_pct": round(width_pct, 3)})
        return None


class VWAPReversion(IntradayStrategy):
    """Fade a stretch away from the session VWAP, targeting the VWAP itself.

    The mean-reversion counterpart to the breakout above, and between them they
    cover both directions an intraday edge could take — so a session where
    NEITHER fires is informative rather than a gap in coverage.

    VWAP is the right anchor rather than a moving average because it is the
    price the session's actual volume transacted at, which is what institutional
    execution is measured against and therefore what it is pulled back toward.
    """

    name = "vwap_reversion"
    label = "VWAP reversion"
    description = ("Fades a stretch away from the session's volume-weighted "
                   "average price, aiming to be paid as it returns.")
    max_hold_minutes = 90
    min_minutes_remaining = 30

    defaults = {
        "stretch_pct": 0.4,         # how far from VWAP counts as stretched
        "stop_atr": 1.5,            # beyond the extreme, in session ATR
        "atr_minutes": 30,
        "warmup_minutes": 45,       # VWAP means nothing on four bars
    }

    def detect(self, ctx):
        p = ctx.params
        if ctx.bar_count < ctx.bars_for(p["warmup_minutes"]) or not self.has_room(ctx):
            return None

        vwap = ctx.session_vwap()
        atr = ctx.session_atr(p["atr_minutes"])
        if not vwap or not atr:
            return None

        price = ctx.price
        stretch = (price / vwap - 1) * 100
        if abs(stretch) < p["stretch_pct"]:
            return None

        cushion = atr * float(p["stop_atr"])
        # The target is the VWAP, so reward:risk is whatever the stretch and the
        # session's own volatility make it — not a number chosen in advance.
        if stretch <= -p["stretch_pct"]:
            return self.build(
                ctx, LONG, entry=price, stop=price - cushion, target=vwap,
                headline=f"{abs(stretch):.2f}% below the session VWAP of {vwap:.2f}",
                reasons=[f"Session VWAP is {vwap:.2f}; price is {price:.2f}",
                         f"That is {abs(stretch):.2f}% below, past the "
                         f"{p['stretch_pct']}% threshold",
                         f"Stop {p['stop_atr']}x the {p['atr_minutes']}-minute range below"],
                meta={"vwap": round(vwap, 4), "stretch_pct": round(stretch, 3),
                      "session_atr": round(atr, 4)})
        return self.build(
            ctx, SHORT, entry=price, stop=price + cushion, target=vwap,
            headline=f"{stretch:.2f}% above the session VWAP of {vwap:.2f}",
            reasons=[f"Session VWAP is {vwap:.2f}; price is {price:.2f}",
                     f"That is {stretch:.2f}% above, past the "
                     f"{p['stretch_pct']}% threshold",
                     f"Stop {p['stop_atr']}x the {p['atr_minutes']}-minute range above"],
            meta={"vwap": round(vwap, 4), "stretch_pct": round(stretch, 3),
                  "session_atr": round(atr, 4)})


class IntradayMomentum(IntradayStrategy):
    """Gao, Han, Li & Zhou (2018) — the first half hour predicts the last.

    The best-evidenced rule in this library and the only one whose horizon is
    fixed by the research rather than chosen: the signal is the first half
    hour's return measured FROM THE PREVIOUS CLOSE, and the position is taken in
    the last half hour and held to the bell. Anything else is a different rule
    wearing this one's citation.

    It therefore ignores `min_minutes_remaining` — it is SUPPOSED to be opened
    near the close. The flat-by-close liquidation is what exits it, which is the
    published rule exactly.
    """

    name = "intraday_momentum"
    label = "First-half-hour momentum"
    description = ("Takes the last half hour in the direction the first half "
                   "hour moved. Held to the bell.")
    max_hold_minutes = 45
    min_minutes_remaining = 0

    defaults = {
        "signal_minutes": 30,       # the first half hour
        "entry_minutes_before_close": 35,
        "min_move_pct": 0.1,        # below this the signal is noise
        "stop_atr": 2.0,
        "atr_minutes": 30,
        "reward_multiple": 1.0,
    }

    def detect(self, ctx):
        p = ctx.params
        if ctx.prev_close is None or not ctx.prev_close:
            return None
        span = ctx.bars_for(p["signal_minutes"])
        if ctx.bar_count < span:
            return None

        # Only in the entry window, and the window is the point of the rule.
        remaining = ctx.minutes_to_close
        if remaining is None or remaining > p["entry_minutes_before_close"]:
            return None

        first = float(ctx.bars["Close"].iloc[span - 1])
        move = (first / float(ctx.prev_close) - 1) * 100
        if abs(move) < p["min_move_pct"]:
            return None

        atr = ctx.session_atr(p["atr_minutes"])
        if not atr:
            return None

        price = ctx.price
        cushion = atr * float(p["stop_atr"])
        reward = float(p["reward_multiple"])
        direction = LONG if move > 0 else SHORT
        reasons = [
            f"Previous close {float(ctx.prev_close):.2f}; first "
            f"{p['signal_minutes']} minutes ended at {first:.2f}",
            f"That is {move:+.2f}%, past the {p['min_move_pct']}% threshold",
            f"{remaining:.0f} minutes to the bell — held to it",
        ]
        meta = {"prev_close": round(float(ctx.prev_close), 4),
                "first_half_hour_pct": round(move, 3),
                "session_atr": round(atr, 4)}

        if direction == LONG:
            return self.build(
                ctx, LONG, entry=price, stop=price - cushion,
                target=price + cushion * reward,
                headline=f"First half hour was {move:+.2f}% — long into the close",
                reasons=reasons, meta=meta)
        return self.build(
            ctx, SHORT, entry=price, stop=price + cushion,
            target=price - cushion * reward,
            headline=f"First half hour was {move:+.2f}% — short into the close",
            reasons=reasons, meta=meta)


class GapFade(IntradayStrategy):
    """Fade a large opening gap back toward the previous close.

    The weakest rule here by evidence and it is kept honest about that. A gap
    has a CAUSE, and this has no way to read it: an earnings gap and a
    sympathy gap look identical in price and behave nothing alike. The size
    limits below are the only defence — beyond a certain distance a gap is news
    rather than an overreaction, and fading news is not a strategy.
    """

    name = "gap_fade"
    label = "Gap fade"
    description = ("Fades a moderate opening gap back toward the previous "
                   "close. No news filter — the size limits are the filter.")
    max_hold_minutes = 120
    min_minutes_remaining = 45

    defaults = {
        "min_gap_pct": 0.5,
        # Beyond this a gap is information, not an overreaction. The rule has
        # no way to tell the two apart, so it declines the large ones.
        "max_gap_pct": 4.0,
        "settle_minutes": 15,       # let the opening auction clear first
        "stop_atr": 2.0,
        "atr_minutes": 30,
        "fill_fraction": 0.5,       # target half the gap, not all of it
    }

    def detect(self, ctx):
        p = ctx.params
        if ctx.prev_close is None or not ctx.prev_close:
            return None
        settle = ctx.bars_for(p["settle_minutes"])
        if ctx.bar_count < settle or not self.has_room(ctx):
            return None
        # Only in the settling window: this is an OPENING gap rule, and a fade
        # entered at lunchtime is a different trade with the same name.
        if ctx.bar_count > ctx.bars_for(p["settle_minutes"] * 3):
            return None

        prev_close = float(ctx.prev_close)
        session_open = float(ctx.bars["Open"].iloc[0])
        gap = (session_open / prev_close - 1) * 100
        if not (p["min_gap_pct"] <= abs(gap) <= p["max_gap_pct"]):
            return None

        atr = ctx.session_atr(p["atr_minutes"])
        if not atr:
            return None

        price = ctx.price
        cushion = atr * float(p["stop_atr"])
        travel = abs(session_open - prev_close) * float(p["fill_fraction"])
        reasons = [
            f"Opened at {session_open:.2f} against a previous close of {prev_close:.2f}",
            f"A gap of {gap:+.2f}%, inside the {p['min_gap_pct']}–{p['max_gap_pct']}% band",
            f"Targeting {p['fill_fraction']:.0%} of the gap, not all of it",
        ]
        meta = {"prev_close": round(prev_close, 4), "gap_pct": round(gap, 3),
                "session_open": round(session_open, 4), "session_atr": round(atr, 4)}

        if gap > 0:      # gapped UP — fade it down
            return self.build(
                ctx, SHORT, entry=price, stop=price + cushion,
                target=price - travel,
                headline=f"Gapped up {gap:+.2f}% — faded back toward the close",
                reasons=reasons, meta=meta)
        return self.build(
            ctx, LONG, entry=price, stop=price - cushion, target=price + travel,
            headline=f"Gapped down {gap:+.2f}% — faded back toward the close",
            reasons=reasons, meta=meta)


# --- unmeasured additions ------------------------------------------------------
#
# Three rules added after the universe sweep in reports/INTRADAY-REVIEW.md. None
# has been through research/intraday.py's breakeven-cost measurement, so each
# ships switched off in config.yaml. The names describe the idea each one
# reaches for; the descriptions say what the code does, which is simpler.

class KalmanFilterStrategy(IntradayStrategy):
    """Long while price holds above a fixed-gain Kalman smoothing of the session.

    With constant noise settings a one-dimensional Kalman filter settles to a
    fixed gain, which makes it an exponential moving average — here roughly a
    ninety-bar one. So this is "price above a slow session average", a trend
    rule, and it is true on about a third of all bars.
    """
    name = "kalman_filter"
    label = "Kalman-smoothed trend"
    description = ("Buys while price has been above a Kalman-smoothed session "
                   "average for two bars. Long only.")
    max_hold_minutes = 120
    min_minutes_remaining = 30
    defaults = {"stop_atr": 1.5, "reward_multiple": 1.5, "atr_minutes": 30,
                "measurement_error": 2.0, "process_variance": 1e-3}

    def detect(self, ctx):
        if ctx.bar_count < 10 or not self.has_room(ctx):
            return None
        p = ctx.params
        prices = ctx.bars["Close"].to_numpy(dtype=float)
        estimate, error = prices[0], 1.0
        filtered = []
        for z in prices:
            error += p["process_variance"]
            gain = error / (error + p["measurement_error"])
            estimate += gain * (z - estimate)
            error *= 1 - gain
            filtered.append(estimate)

        atr = ctx.session_atr(p["atr_minutes"])
        if not atr:
            return None
        price = ctx.price
        if not (filtered[-2] < prices[-2] and filtered[-1] < price):
            return None
        risk = atr * p["stop_atr"]
        return self.build(
            ctx, LONG, entry=price, stop=price - risk,
            target=price + risk * p["reward_multiple"],
            headline="Price is holding above its Kalman-smoothed average",
            reasons=[f"Price {price:.2f} has been above the smoothed session "
                     f"average ({filtered[-1]:.2f}) for two bars"],
            meta={"kalman_estimate": round(filtered[-1], 4)})


class OUProcessStatArbStrategy(IntradayStrategy):
    """Buy a close two standard deviations below the session's average close.

    The idea behind it is an Ornstein-Uhlenbeck process — a price pulled back
    toward a mean — but nothing here fits one: no reversion speed, no
    half-life, no test that the session is mean-reverting at all. It is a
    z-score fade, the same family as VWAPReversion above with a different
    anchor.
    """
    name = "ou_process_reversion"
    label = "Session z-score reversion"
    description = ("Buys when price is two standard deviations below the "
                   "session's average close, targeting that average. Long only.")
    max_hold_minutes = 90
    min_minutes_remaining = 30
    defaults = {"z_score_threshold": 2.0, "stop_atr": 2.0, "atr_minutes": 30}

    def detect(self, ctx):
        if ctx.bar_count < 30 or not self.has_room(ctx):
            return None
        p = ctx.params
        prices = ctx.bars["Close"].to_numpy(dtype=float)
        mean, std = prices.mean(), prices.std()
        if std == 0:
            return None
        z_score = (ctx.price - mean) / std
        atr = ctx.session_atr(p["atr_minutes"])
        if not atr or z_score >= -p["z_score_threshold"]:
            return None
        return self.build(
            ctx, LONG, entry=ctx.price, stop=ctx.price - atr * p["stop_atr"],
            target=mean, headline=f"{abs(z_score):.1f} standard deviations below "
                                  "the session average",
            reasons=[f"Price {ctx.price:.2f} is {abs(z_score):.1f} standard "
                     f"deviations below the session's average close of {mean:.2f}"],
            meta={"z_score": round(float(z_score), 2)})


class HMMRegimeFilterStrategy(IntradayStrategy):
    """Buy when short-term volatility jumps relative to the last 100 minutes.

    No hidden Markov model is fitted; the "regime" is a ratio of two standard
    deviations. It also buys whichever way price is moving, so a volatility
    spike on the way DOWN triggers a long exactly as one on the way up does.
    """
    name = "hmm_regime_breakout"
    label = "Volatility expansion"
    description = ("Buys when the last 25 minutes are at least 1.5 times as "
                   "volatile as the last 100, whichever way price is moving.")
    defaults ={"fast_minutes": 25, "slow_minutes": 100, "expansion": 1.5,
                "stop_atr": 1.0, "reward_multiple": 2.0, "atr_minutes": 30}

    def detect(self, ctx):
        p = ctx.params
        fast, slow = ctx.bars_for(p["fast_minutes"]), ctx.bars_for(p["slow_minutes"])
        # has_room was missing, so this opened positions in the final minutes
        # of the session with no time left to be right or wrong.
        if ctx.bar_count < slow or not self.has_room(ctx):
            return None
        closes = ctx.bars["Close"]
        recent, baseline = closes.tail(fast).std(), closes.tail(slow).std()
        if not baseline or not recent > baseline * p["expansion"]:
            return None
        atr = ctx.session_atr(p["atr_minutes"])
        if not atr:
            return None
        price, risk = ctx.price, atr * p["stop_atr"]
        return self.build(
            ctx, LONG, entry=price, stop=price - risk,
            target=price + risk * p["reward_multiple"],
            headline="Short-term volatility has expanded",
            reasons=[f"The last {p['fast_minutes']} minutes are "
                     f"{recent / baseline:.1f}x as volatile as the last "
                     f"{p['slow_minutes']}"],
            meta={"vol_ratio": round(float(recent / baseline), 2)})


REGISTRY = {cls.name: cls for cls in (
    OpeningRangeBreak, VWAPReversion, IntradayMomentum, GapFade,
    KalmanFilterStrategy, OUProcessStatArbStrategy, HMMRegimeFilterStrategy)}

# Ideas with no implementation, and the input each is waiting for. Kept as a
# list rather than as classes whose detect() returns None: an empty class in
# REGISTRY is counted, listed and enabled like a working rule, and nothing on
# the dashboard distinguishes a strategy that found no setup from one that
# cannot find any.
PLANNED = {
    "meta_labeling_filter": "a trained classifier and a labelled trade history",
    "fama_french_intraday": "cross-sectional fundamental data",
    "order_book_imbalance": "Level 2 order-book data (no subscription held)",
    "nlp_finbert_sentiment": "a live news feed and a sentiment model",
    "macro_fade_first_move": "a live macro-release calendar and feed",
    "intraday_pair_trading": "paired instruments in one context, and a short book",
    "moc_imbalance_arb": "exchange closing-auction imbalance feeds",
    "zero_dte_gamma_squeeze": "OPRA options flow (no subscription held)",
    "vwap_institutional_accumulation": "a volume-profile rule, not yet written",
}


def enabled_strategies(config):
    """Every intraday rule switched on, in a stable order."""
    return [cls() for cls in REGISTRY.values() if cls().enabled(config)]


def detect_all(ctx, config=None):
    """Every idea the enabled rules produce for this instrument at this bar.

    Returns a list. Which one gets the slot is a portfolio decision and belongs
    to the engine, not here — a strategy that ranked itself against its rivals
    would be deciding something it cannot see.
    """
    out = []
    for strategy in enabled_strategies(config or ctx.config):
        ctx.params = strategy.params_for(config or ctx.config)
        idea = strategy.detect(ctx)
        if idea is not None:
            out.append(idea)
    return out

