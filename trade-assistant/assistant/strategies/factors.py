"""Machinery shared by the factor strategies.

Everything in this library now is a factor strategy: it holds for weeks or
months on a periodic rebalance, and its edge comes from a ranking or a sign, not
from a chart pattern on today's bar. That family shares three problems the old
pattern strategies did not have, and they are solved once, here.

**When is it allowed to act.** A monthly-rebalance strategy that re-evaluates
every day is not the strategy in the paper; it is a daily strategy with a slow
signal, and it will trade far more than the research it claims to implement.

**Where do the levels come from.** Cross-sectional momentum has no stop — the
published rule exits by falling out of the top decile at the next rebalance.
This engine cannot express that: it sizes and gates every trade off a stop
price, and refuses ideas without one. So a wide ATR stop is grafted on as a
risk control. It is a deliberate departure from the papers and it belongs in
one place with this comment attached, not scattered across three files.

**How volatile is too volatile.** Every one of these strategies scales down or
steps aside when a name is wild, which is the same measurement three times.
"""
import math

from ..research import indicators

# A rebalance mode of "any" re-evaluates every bar. It exists to isolate the
# effect of the rebalance frequency in a backtest, not because it is a sensible
# way to run a monthly strategy.
REBALANCE_MODES = ("monthly", "weekly", "any")


def is_rebalance_bar(df, mode="monthly"):
    """Is the latest bar one this strategy is allowed to act on?

    A calendar rebalance needs real dates. When the index carries none — a
    synthetic frame, or a provider that returned bare row numbers — the question
    cannot be answered, and the answer is no. Failing closed costs a missed
    signal; failing open silently converts a monthly strategy into a daily one
    and multiplies its turnover by twenty.
    """
    if mode == "any":
        return True
    if df is None or len(df) < 2:
        return False

    last, previous = df.index[-1], df.index[-2]
    if not hasattr(last, "year") or not hasattr(previous, "year"):
        return False

    if mode == "weekly":
        return last.isocalendar()[:2] != previous.isocalendar()[:2]
    # Monthly: the first bar traded in a new month.
    return (last.year, last.month) != (previous.year, previous.month)


def rebalance_label(mode):
    return {"monthly": "month", "weekly": "week"}.get(mode, "bar")


def atr_levels(price, atr, direction, stop_atr, reward_multiple):
    """(stop, target) a fixed number of ATR from the entry, or None if unusable.

    Wide by design. These strategies hold for weeks; a stop tight enough for a
    breakout trade would be hit by ordinary noise long before the thesis had a
    chance to be right or wrong, and would convert a slow winner into a fast
    loser at a rate that has nothing to do with the factor being tested.
    """
    if not (indicators.is_finite(price) and indicators.is_finite(atr)) or atr <= 0:
        return None
    risk = float(stop_atr) * float(atr)
    if risk <= 0:
        return None
    if direction == "long":
        return price - risk, price + float(reward_multiple) * risk
    return price + risk, price - float(reward_multiple) * risk


def volatility_check(closes, lookback_bars, max_vol_pct):
    """(realised_vol_pct, passed) — annualised vol against a ceiling.

    Returns (None, False) when it cannot be measured. A volatility filter that
    passes everything it failed to measure is not a volatility filter.
    """
    vol = indicators.realized_vol(closes, lookback_bars=lookback_bars)
    if vol is None:
        return None, False
    if max_vol_pct is None:
        return vol, True
    return vol, vol <= float(max_vol_pct)


def inverse_vol_weight(realized_vol_pct, target_vol_pct, cap=2.0):
    """Position weight that targets a constant volatility, capped.

    The time-series momentum literature sizes every instrument to the same
    volatility rather than the same cash, so a quiet bond future and a wild
    growth stock contribute equally instead of the wildest name dominating the
    book. This engine sizes off the stop distance instead, so the number is
    reported in the idea's meta for the portfolio layer to use rather than
    applied here — recorded honestly as unused rather than quietly dropped.
    """
    if not indicators.is_finite(realized_vol_pct) or realized_vol_pct <= 0:
        return None
    return round(min(float(target_vol_pct) / float(realized_vol_pct), float(cap)), 2)


def benchmark_upto(benchmark_closes, as_of):
    """The benchmark series truncated at `as_of`, so an overlay cannot peek.

    In the backtest the caller already slices it; in a live scan it is today's
    full series. Truncating again here is cheap and makes the guarantee local to
    the strategy that depends on it.
    """
    if benchmark_closes is None or len(benchmark_closes) == 0 or as_of is None:
        return benchmark_closes
    try:
        return benchmark_closes.loc[:as_of]
    except (TypeError, ValueError, KeyError):
        return benchmark_closes


def as_of_of(df):
    """The date of the latest bar, which is what every rank lookup is keyed on."""
    if df is None or len(df) == 0:
        return None
    try:
        return df.index[-1]
    except (IndexError, TypeError):
        return None


def peer_group(ctx):
    """The tickers this instrument should actually be ranked against.

    A cross-sectional rank is a comparison, and a comparison between different
    kinds of thing tells you nothing. Pooled with five hundred US shares, the
    strongest futures contract in a decade ranked twenty-third out of forty
    stocks — placing in a top-twelve there needed a 183% year, which no currency
    pair or futures contract has ever produced. The macro instruments were not
    outperformed, they were unreachable: present in the universe, structurally
    incapable of being selected, and easily mistaken for "tested and found
    wanting".

    So a currency pair ranks against currency pairs and a bond future against
    bond futures. Every segment gets to produce trades on its own terms, in one
    book, at the same time. Returns None when the universe carries no class
    information at all, which leaves the caller ranking against everything —
    the old behaviour, and correct for a single-segment universe.
    """
    universe = ctx.cross_section
    if universe is None:
        return None
    from ..markets import asset_class_of

    mine = asset_class_of(ctx.ticker)
    peers = {t for t in universe.tickers if asset_class_of(t) == mine}
    return peers or None


def slots(group_size, top_pct, min_n=2, max_n=15):
    """How many of a peer group to hold.

    A fixed count cannot serve both ends of a mixed book: "top 12" is the top
    2% of five hundred equities and the whole of a twelve-instrument currency
    book. Expressed as a fraction with a floor and a ceiling, a small segment
    still concentrates into its best few and a large one does not sprawl into
    fifty positions.
    """
    if not group_size:
        return 0
    target = round(float(top_pct) / 100 * int(group_size))
    return int(max(int(min_n), min(int(max_n), max(1, target))))


def pct(value):
    """A fraction as a readable percentage string, for the reasons list."""
    return "unknown" if value is None or math.isnan(value) else f"{value * 100:+.1f}%"
