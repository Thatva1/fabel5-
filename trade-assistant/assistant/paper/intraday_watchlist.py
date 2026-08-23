"""Which instruments the intraday book actually watches, and why not all of them.

THE CONSTRAINT, STATED HONESTLY

The tradable universe is 1,568 instruments. IB serves intraday bars at roughly
1.5 seconds each, so one pass over the whole universe takes about forty
minutes. A five-minute strategy that re-reads its universe every forty minutes
is not a five-minute strategy — by the time the last name is fetched, the first
one's signal is eight bars stale.

So the working set has to be smaller than the universe, and the only question
worth arguing about is how it is chosen. Choosing by market cap gives you the
mega-caps, which is where an intraday edge is smallest. Choosing by yesterday's
mover gives you whatever gapped on news, which is the population this library
is least equipped to trade. Choosing at random gives you the spread profile of
the universe, and most of the universe cannot pay its own spread.

WHAT ACTUALLY DECIDES IT

One ratio: how far an instrument travels in a session, divided by what it costs
to get in and out.

    range_to_cost = average daily range % ÷ round-trip spread %

An instrument that moves 0.4% on an average day and costs 0.40% to trade offers
a ratio of 1. Every trade in it is a coin flip for the spread, and no rule —
however good — changes that arithmetic. An instrument that moves 3% against a
0.05% spread offers 60, and a rule needs only a small slice of that range to
clear its costs. The ratio is the budget every intraday strategy spends out of,
and it is knowable in advance, which almost nothing else in this system is.

TWO STAGES, BECAUSE THE CHEAP MEASUREMENT IS NOT THE ACCURATE ONE

Stage one runs here, on the daily universe history already sitting on disk —
the same cache the daily rebalance reads — so it costs ZERO IB requests and can
be rebuilt before the open without touching the pacing budget the live loop
needs. It narrows 1,568 instruments to a candidate pool of a few hundred.

Stage two happens in the runner, on real intraday bars for the pool only.

The split exists because of a measurement problem worth being blunt about.
Corwin-Schultz applied to DAILY high-low prices does not produce a usable
absolute spread for this purpose. Measured across this universe it returns a
median of 63 basis points, and the true spread on a liquid US mid-cap is a few
basis points. The estimator is not broken — it is being asked the wrong
question. A whole DAY's high-low range is dominated by genuine price movement
rather than by the quote, its two-day volatility adjustment cannot separate the
two once overnight gaps are involved, and the residual lands in the spread term.

So the daily estimate is used HERE AS A RELATIVE RANKER ONLY: an instrument
scoring 40 really is tighter than one scoring 200, and that ordering is all
stage one needs. Every ABSOLUTE gate at this stage is applied to a quantity the
daily bars genuinely know — price, dollar volume, and the size of the daily
range itself. The absolute spread is re-measured on five-minute bars in stage
two, where a bar's high-low IS mostly the quote and the estimator is answering
the question it was built for.

Tuning the daily estimate's threshold until the output looked reasonable would
have been the easy path and it would have produced a working set selected by a
number that means nothing.

WHAT THIS IS NOT

Not a prediction. Nothing here says these instruments will move today; it says
that when they do move, the movement is large relative to the toll. A liquidity
filter is the one part of a trading system that can be honest without being
predictive, and that is exactly why it should carry as much of the weight as it
can.
"""
import math
import os
import time

from ..backtest import spread as spread_model
from ..core.config import DATA_DIR

CACHE_PATH = os.path.join(DATA_DIR, "intraday_watchlist.json")

DEFAULTS = {
    # How many instruments the live loop finally polls. Sized against the bar
    # rather than chosen: at ~1.5s an instrument a five-minute bar affords about
    # 200, and the loop must finish well inside its own bar or it is trading on
    # history. 60 leaves room for the marks on held positions and for a slow day
    # on IB's side.
    "size": 60,
    # The candidate pool stage one hands to stage two. Bigger than `size`
    # because the intraday re-measurement will reject some of it, and small
    # enough that fetching intraday bars for the pool costs about four minutes
    # rather than forty.
    "pool": 150,
    "lookback_days": 60,

    # --- absolute gates, on quantities daily bars actually know --------------
    "min_price": 5.0,               # a penny spread on a $2 share is 50bp
    "min_dollar_volume": 20_000_000,
    # An instrument has to MOVE. This is the gate that keeps short-duration
    # treasury ETFs out: BIL and SHV score beautifully on travel-per-cost
    # because both numbers are tiny, and a 0.01% daily range cannot pay a
    # commission however favourable the ratio looks.
    "min_range_pct": 0.8,

    # --- relative gate, on a number that only orders correctly ---------------
    # Keep the tightest share of what survives the absolute gates. A percentile
    # rather than a basis-point cap, because the daily spread estimate has no
    # trustworthy absolute scale — see the module docstring.
    "keep_tightest_pct": 40.0,

    # --- stage two, applied by the runner on intraday bars ------------------
    "max_spread_bps": 15.0,         # a real cap, on a real measurement
    "min_range_to_cost": 8.0,       # a session must offer 8 round trips of travel

    "rebuild_after_hours": 20,
}


def settings(config):
    block = (((config or {}).get("intraday") or {}).get("watchlist") or {})
    return {**DEFAULTS, **block}


def _metrics(frame, lookback_days):
    """Range, cost and turnover for one instrument. None when unmeasurable."""
    if frame is None or len(frame) < 20:
        return None
    window = frame.tail(int(lookback_days))
    closes = window["Close"].astype(float)
    price = float(closes.iloc[-1])
    if not (price > 0 and math.isfinite(price)):
        return None

    ranges = ((window["High"].astype(float) - window["Low"].astype(float))
              / closes * 100)
    range_pct = float(ranges.mean())
    if not (range_pct > 0 and math.isfinite(range_pct)):
        return None

    spread_bps = spread_model.estimate_spread_bps(window)
    if spread_bps is None:
        return None
    # An estimate of zero means the estimator could not see the spread, not
    # that trading is free. Floored at a penny-wide quote on this price.
    spread_bps = max(spread_bps, 100 * 0.01 / price)

    dollar_volume = float((closes * window["Volume"].astype(float)).mean())
    return {
        "price": round(price, 4),
        "range_pct": round(range_pct, 3),
        "spread_bps": round(spread_bps, 2),
        # The whole point: daily travel measured in round trips.
        "range_to_cost": round(range_pct / (spread_bps / 100), 2),
        "dollar_volume": round(dollar_volume, 0),
    }


def rank(frames, config=None):
    """Stage one: every instrument scored, best first, with why each was dropped.

    Rejections are counted by REASON rather than summed. A pool that came back
    short because nothing moves is a different problem from one that came back
    short because the cache was stale, and a single "1,508 rejected" cannot
    tell you which.
    """
    cfg = settings(config)
    survivors, rejected = [], {"unmeasurable": 0, "price": 0, "volume": 0,
                               "range": 0, "spread_rank": 0}

    for ticker, frame in (frames or {}).items():
        row = _metrics(frame, cfg["lookback_days"])
        if row is None:
            rejected["unmeasurable"] += 1
            continue
        if row["price"] < cfg["min_price"]:
            rejected["price"] += 1
            continue
        if row["dollar_volume"] < cfg["min_dollar_volume"]:
            rejected["volume"] += 1
            continue
        if row["range_pct"] < cfg["min_range_pct"]:
            rejected["range"] += 1
            continue
        survivors.append({"ticker": ticker, **row})

    # The relative gate, applied last so the percentile is taken over
    # instruments that already move and already trade.
    survivors.sort(key=lambda row: row["spread_bps"])
    keep = max(1, int(len(survivors) * float(cfg["keep_tightest_pct"]) / 100))
    rejected["spread_rank"] = len(survivors) - keep
    kept = survivors[:keep]

    kept.sort(key=lambda row: -row["range_to_cost"])
    return kept, rejected


def build(frames, config=None):
    """Stage one's output: the candidate pool, for the runner to re-measure.

    Deliberately NOT called a watchlist. Nothing here has had its spread
    measured at a resolution that means anything, so this is a shortlist to
    spend intraday requests on, not a set of instruments to trade.
    """
    cfg = settings(config)
    ranked, rejected = rank(frames, config)
    chosen = ranked[:int(cfg["pool"])]
    return {
        "built_at": time.time(),
        "stage": "daily-prefilter",
        "size": len(chosen),
        "considered": len(frames or {}),
        "qualified": len(ranked),
        "rejected": rejected,
        "settings": {k: cfg[k] for k in
                     ("size", "pool", "min_range_pct", "keep_tightest_pct",
                      "min_price", "min_dollar_volume", "lookback_days")},
        "symbols": [row["ticker"] for row in chosen],
        "detail": chosen,
        "note": ("Spreads here are Corwin-Schultz on DAILY bars, which orders "
                 "instruments correctly but has no trustworthy absolute scale. "
                 "The runner re-measures on intraday bars before trading any "
                 "of these."),
    }


def refine(pool, intraday_frames, config=None):
    """Stage two: re-measure on intraday bars and keep what can actually pay.

    This is where the absolute gates finally mean something. A five-minute
    bar's high-low is mostly the quote rather than the day's news, so
    Corwin-Schultz is answering the question it was designed for, and a
    basis-point cap is a real constraint instead of a number chosen to make the
    list the right length.
    """
    cfg = settings(config)
    kept, dropped = [], {"no_bars": 0, "spread": 0, "range_to_cost": 0}
    by_ticker = {row["ticker"]: row for row in (pool or {}).get("detail", [])}

    for ticker in (pool or {}).get("symbols", []):
        frame = (intraday_frames or {}).get(ticker)
        if frame is None or len(frame) < 20:
            dropped["no_bars"] += 1
            continue

        measured = spread_model.estimate_spread_bps(frame)
        if measured is None:
            dropped["no_bars"] += 1
            continue
        measured = max(measured, 0.5)
        if measured > cfg["max_spread_bps"]:
            dropped["spread"] += 1
            continue

        daily = by_ticker.get(ticker) or {}
        range_pct = daily.get("range_pct") or 0.0
        ratio = range_pct / (measured / 100) if measured else 0.0
        if ratio < cfg["min_range_to_cost"]:
            dropped["range_to_cost"] += 1
            continue

        kept.append({**daily, "ticker": ticker,
                     "intraday_spread_bps": round(measured, 2),
                     "range_to_cost": round(ratio, 1)})

    kept.sort(key=lambda row: -row["range_to_cost"])
    chosen = kept[:int(cfg["size"])]
    return {
        "built_at": time.time(),
        "stage": "intraday-measured",
        "size": len(chosen),
        "pool_size": len((pool or {}).get("symbols") or []),
        "qualified": len(kept),
        "dropped": dropped,
        "symbols": [row["ticker"] for row in chosen],
        "detail": chosen,
        "weakest_range_to_cost": chosen[-1]["range_to_cost"] if chosen else None,
    }


def save(payload, path=None):
    import json
    path = path or CACHE_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as handle:
        json.dump(payload, handle, indent=2)
    os.replace(tmp, path)
    return path


def load(path=None, max_age_hours=None):
    """Today's working set, or None when there isn't a usable one.

    Age-limited for the same reason the scan snapshot is: a spread measured six
    weeks ago is not this morning's spread, and a live loop trading yesterday's
    liquidity profile is the failure this whole module exists to avoid.
    """
    import json
    path = path or CACHE_PATH
    try:
        with open(path) as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return None
    limit = DEFAULTS["rebuild_after_hours"] if max_age_hours is None else max_age_hours
    if limit:
        age_h = (time.time() - float(payload.get("built_at") or 0)) / 3600
        if age_h > limit:
            return None
        payload["age_hours"] = round(age_h, 1)
    return payload


def describe(payload):
    """One paragraph a person can read before trusting the day's list."""
    if not payload or not payload.get("symbols"):
        return ("No intraday working set. Nothing qualified, or the daily "
                "history cache is missing.")
    detail = payload["detail"]
    mid = len(detail) // 2
    key = ("intraday_spread_bps" if payload.get("stage") == "intraday-measured"
           else "spread_bps")
    spreads = sorted(row.get(key, 0) for row in detail)
    ratios = sorted(row["range_to_cost"] for row in detail)

    if payload.get("stage") == "intraday-measured":
        return (f"{payload['size']} instruments, measured on intraday bars, from "
                f"a pool of {payload['pool_size']} ({payload['qualified']} "
                f"qualified). Median spread {spreads[mid]:.1f}bp, median daily "
                f"travel {ratios[mid]:.0f} round trips. Weakest member offers "
                f"{payload['weakest_range_to_cost']:.0f}.")
    return (f"Candidate pool: {payload['size']} from {payload['considered']} "
            f"considered ({payload['qualified']} qualified). Spreads are a "
            f"RELATIVE daily estimate — median {spreads[mid]:.1f}bp — and are "
            f"re-measured on intraday bars before anything is traded.")
