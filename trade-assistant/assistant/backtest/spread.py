"""What each instrument actually costs to cross, estimated from its own bars.

WHY A FLAT COST NUMBER BREAKS THE MOMENT THE UNIVERSE WIDENS

Run an intraday reversion rule on eight mega-caps and a flat 8bp round trip is
roughly honest — SPY and AAPL really do trade a penny wide. Run the same rule
on fifteen hundred names and that number becomes the single largest error in
the study, because it is wrong in the direction that manufactures an edge:

  * The apparent reversion is LARGEST in the least liquid names. That is not a
    discovery, it is the bid-ask bounce. A price that prints at the bid, then
    at the offer, then at the bid has "reverted" twice and moved not at all.
  * Those are exactly the names whose real spread is 30, 80, 200 basis points.
  * So a flat 8bp charge pays the mega-cap's cost on the small-cap's illusion,
    and the wider the universe the better the fake result looks.

A universe-wide intraday backtest with a flat spread is therefore not a more
thorough version of the narrow one. It is a machine for generating edges that
do not exist, and it gets more convincing the more instruments you feed it.

THE ESTIMATOR

Corwin & Schultz (2012), the high-low spread estimator. It rests on one idea:
over a two-day window the high-to-low range contains both the true volatility
AND one spread, while the sum of the two single-day ranges contains the same
volatility and TWO spreads. Differencing them isolates the spread without ever
seeing a quote — which matters here, because this account has no quote data at
all and buying it to find out whether an edge exists is the wrong order.

Applied to intraday bars the "day" is a bar, so what comes back is the spread
faced by someone trading at that bar size. That is the right question.

WHAT IT IS NOT, AND WHICH WAY IT IS WRONG

Not exact, and the direction of the error is worth stating plainly because an
earlier draft of this note got it backwards.

Two biases pull against each other. Truncating individual negative estimates to
zero — the authors' own recommendation — pushes the average UP. The volatility
adjustment at the heart of the estimator pushes it DOWN, because part of a
genuinely wide spread is attributed to the price having moved. Measured here on
synthetic series with a known bounce, the second wins at the wide end: a true
60bp round trip comes back as roughly 44bp, and a true 3bp comes back as
essentially zero.

So this UNDERSTATES wide spreads, which is the dangerous direction — it charges
an illiquid name less than it really costs and flatters any rule that trades
one. Two consequences follow, and neither is optional:

  * `max_spread_bps` must be set conservatively. A measured 40 is plausibly a
    real 55, so a cap chosen as though the number were exact admits instruments
    that cannot be traded at anything like the assumed cost.
  * A result that depends on the widest names in the surviving universe should
    be disbelieved. The engine prints the tightest-half against the widest-half
    return for exactly this reason.

It also measures the QUOTED spread, not market impact. A position large enough
to move the book pays more than this, and nothing here models that either.
"""
import math

# Corwin-Schultz constant: 3 - 2*sqrt(2).
_K = 3 - 2 * math.sqrt(2)


def _log_ratio(high, low):
    if not (high > 0 and low > 0):
        return None
    return math.log(high / low)


def estimate_spread_bps(frame, *, max_bars=None):
    """Effective round-trip spread in basis points, or None if unmeasurable.

    Returns the spread as a fraction of price expressed in bp: 25.0 means a
    quarter of a percent between bid and offer, so crossing it once costs
    12.5bp and a round trip costs 25.
    """
    if frame is None or len(frame) < 3:
        return None
    if max_bars:
        frame = frame.tail(int(max_bars))

    highs = frame["High"].astype(float).tolist()
    lows = frame["Low"].astype(float).tolist()

    estimates = []
    for i in range(len(highs) - 1):
        h1, l1, h2, l2 = highs[i], lows[i], highs[i + 1], lows[i + 1]
        single = _log_ratio(h1, l1), _log_ratio(h2, l2)
        if any(x is None for x in single):
            continue
        # The two-bar window: the extremes across both bars together.
        pair = _log_ratio(max(h1, h2), min(l1, l2))
        if pair is None:
            continue

        beta = single[0] ** 2 + single[1] ** 2
        gamma = pair ** 2
        alpha = (math.sqrt(2 * beta) - math.sqrt(beta)) / _K - math.sqrt(gamma / _K)
        spread = 2 * (math.exp(alpha) - 1) / (1 + math.exp(alpha))
        # The authors' own treatment: a negative estimate is noise around a
        # small true spread, not evidence of a negative one.
        estimates.append(max(0.0, spread))

    if not estimates:
        return None
    mean = sum(estimates) / len(estimates)
    if not math.isfinite(mean):
        return None
    return round(mean * 10_000, 2)


def cost_table(frames, *, floor_bps=1.0, cap_bps=None, max_bars=None):
    """Per-instrument round-trip spread for a whole universe.

    Returns {"spreads": {ticker: bps}, "unmeasured": [...], "excluded": [...]}.

    `floor_bps` stops a suspiciously clean series being charged nothing — an
    estimate of zero means the estimator could not see the spread, not that
    there isn't one. `cap_bps` drops instruments too wide to trade at all;
    keeping them contributes noise dressed as signal, and they are reported by
    name rather than silently dropped so the universe can be audited.
    """
    spreads, unmeasured, excluded = {}, [], []
    for ticker, frame in (frames or {}).items():
        value = estimate_spread_bps(frame, max_bars=max_bars)
        if value is None:
            unmeasured.append(ticker)
            continue
        value = max(float(floor_bps), value)
        if cap_bps is not None and value > float(cap_bps):
            excluded.append({"ticker": ticker, "spread_bps": value})
            continue
        spreads[ticker] = value
    excluded.sort(key=lambda row: -row["spread_bps"])
    return {"spreads": spreads, "unmeasured": sorted(unmeasured),
            "excluded": excluded}


def describe(table):
    """A one-line read on how liquid the surviving universe actually is."""
    values = sorted(table.get("spreads", {}).values())
    if not values:
        return {"instruments": 0}

    def percentile(p):
        if len(values) == 1:
            return values[0]
        position = p / 100 * (len(values) - 1)
        low = int(position)
        high = min(low + 1, len(values) - 1)
        return round(values[low] + (values[high] - values[low]) * (position - low), 2)

    return {
        "instruments": len(values),
        "median_bps": percentile(50),
        "p10_bps": percentile(10),
        "p90_bps": percentile(90),
        "widest_bps": values[-1],
        "tightest_bps": values[0],
        "excluded": len(table.get("excluded") or []),
        "unmeasured": len(table.get("unmeasured") or []),
    }
