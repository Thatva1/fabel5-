"""One algorithm out of twelve strategies, from measured evidence.

WHAT THIS REPLACES, AND WHY THAT NEEDED REPLACING

The book currently chooses what to hold in two stages, and `session._rank_
candidates` says plainly what is wrong with both:

  * WITHIN an instrument, a hand-ordered `strategy_priority` list decides which
    strategy claims it. The order is a judgement written down once, not a
    measurement, and nothing re-checks it.
  * ACROSS instruments, reward:risk decides — except every strategy targets a
    fixed multiple of its own stop, so reward:risk is 3.0, 3.0 and 2.5 by
    construction and discriminates almost nothing. What actually orders the
    book is the order instruments arrive from the screen, which is liquidity
    order. The docstring admits it: "the strategy mix in the book is not a
    considered judgement about which signal is better today."

And the whole design throws away its best information. When four strategies
independently flag the same instrument, the current code picks one and
discards the fact that four agreed. Agreement is the single most useful thing a
library of twelve rules produces, and it was being deleted.

THE COMBINATION

Grinold and Kahn's, and it is the standard answer to exactly this question:
given several signals of differing quality that are correlated with each other,
what is the strength of their combination?

    score = |w · d| / sqrt(wᵀ C w)

  w  each strategy's MEASURED out-of-sample mean return (reports/KELLY-EDGES
     .json — the half of history the strategy was NOT selected against)
  d  its direction on this instrument, +1 long or -1 short
  C  the strategies' return-stream correlation matrix
     (reports/STRATEGY-CORRELATION.csv)

The denominator is what makes it honest. Two momentum strategies agreeing are
correlated at 0.78, so their combination is barely stronger than one of them:
the numerator doubles and the denominator nearly doubles with it. A momentum
strategy and a reversal strategy agreeing are correlated at 0.27, and their
combination is genuinely stronger than either. With every correlation at 1 the
score collapses to the single best signal, which is the correct limit.

Disagreement is handled by the same arithmetic rather than by a special case.
A long vote and a short vote of equal weight cancel in the numerator, so an
instrument its own library cannot agree about scores near zero and does not get
a slot — which is the right outcome and is not something the priority list
could express at all.

WHAT IT IS NOT

Not a machine-learning model, and deliberately not. Every weight here is a
measured out-of-sample return and every correlation is a measured one; nothing
is fitted to make the answer come out well. A model with free parameters would
need its own out-of-sample discipline before it could be trusted more than
this, and this can be checked by hand.

It also cannot be better than its inputs. An edge file measured against a
strategy library that has since been rewritten is stale, and the loader says so
rather than silently ranking on it.
"""
import csv
import math
import os
import time

from ..core.config import PROJECT_ROOT

EDGES_PATH = os.path.join(PROJECT_ROOT, "reports", "KELLY-EDGES.json")
CORRELATION_PATH = os.path.join(PROJECT_ROOT, "reports", "STRATEGY-CORRELATION.csv")

# Correlation assumed between two strategies the matrix does not cover. High on
# purpose: an unmeasured pair is far more likely to be two variations on the
# same idea than a genuine diversifier, and assuming independence would hand a
# brand-new strategy the largest possible agreement bonus on no evidence.
UNKNOWN_CORRELATION = 0.7


def load_edges(path=None):
    """Measured out-of-sample mean return per strategy, with its age.

    Returns ({strategy: mean_return}, age_days). Empty when the study has not
    been run — in which case the caller must fall back rather than invent one.
    """
    import json

    path = path or EDGES_PATH
    try:
        with open(path) as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return {}, None
    means = {k: float(v) for k, v in (payload.get("means") or {}).items()
             if v is not None}
    try:
        age_days = round((time.time() - os.path.getmtime(path)) / 86400, 1)
    except OSError:
        age_days = None
    return means, age_days


def load_correlations(path=None):
    """The strategies' return-stream correlation matrix.

    Of the RETURN STREAMS, not of the instruments held — two momentum
    strategies can hold entirely different tickers and still be the same bet,
    because what they have in common is when they lose.
    """
    path = path or CORRELATION_PATH
    matrix = {}
    try:
        with open(path) as handle:
            for row in csv.DictReader(handle):
                name = row.get("strategy")
                if not name:
                    continue
                matrix[name] = {k: float(v) for k, v in row.items()
                                if k != "strategy" and v not in (None, "")}
    except (OSError, ValueError):
        return {}
    return matrix


def correlation(matrix, a, b, default=UNKNOWN_CORRELATION):
    if a == b:
        return 1.0
    row = (matrix or {}).get(a) or {}
    value = row.get(b)
    if value is None:
        value = ((matrix or {}).get(b) or {}).get(a)
    return default if value is None else float(value)


def combine(votes, edges, matrix, *, default_correlation=UNKNOWN_CORRELATION):
    """Strength of a set of directional votes on one instrument.

    `votes` is {strategy: +1 or -1}. Returns a dict carrying the score, the
    direction it points, and enough of the arithmetic to see why.

    A strategy with no measured edge contributes nothing rather than a guess.
    That is the conservative choice and it is the right one: a rule nobody has
    measured out of sample is a rule nobody knows anything about.
    """
    weighted = {name: edges.get(name) for name in votes}
    usable = {name: w for name, w in weighted.items()
              if w is not None and w > 0}
    unmeasured = sorted(set(votes) - set(usable))

    if not usable:
        return {"score": 0.0, "direction": None, "contributors": [],
                "unmeasured": unmeasured, "effective_signals": 0.0,
                "note": ("No strategy voting here has a measured out-of-sample "
                         "edge, so there is nothing to combine.")}

    names = sorted(usable)
    numerator = sum(usable[n] * votes[n] for n in names)

    variance = 0.0
    for a in names:
        for b in names:
            variance += (usable[a] * usable[b]
                         * correlation(matrix, a, b, default_correlation))
    denominator = math.sqrt(variance) if variance > 0 else 0.0
    score = (numerator / denominator) if denominator else 0.0

    # How many INDEPENDENT signals this agreement is worth. One when everything
    # agreeing is the same bet; n when they are genuinely unrelated.
    gross = sum(usable[n] for n in names)
    effective = (gross / denominator) ** 2 if denominator else 0.0

    return {
        "score": round(score, 5),
        "direction": ("long" if numerator > 0 else
                      "short" if numerator < 0 else None),
        "agreeing": sum(1 for n in names if votes[n] * numerator > 0),
        "dissenting": sum(1 for n in names if votes[n] * numerator < 0),
        "effective_signals": round(effective, 2),
        "contributors": [{"strategy": n, "edge": round(usable[n], 6),
                          "direction": "long" if votes[n] > 0 else "short"}
                         for n in sorted(names, key=lambda x: -usable[x])],
        "unmeasured": unmeasured,
    }


def rank(ideas, *, edges=None, matrix=None, config=None):
    """Every candidate scored, best first, one per instrument.

    Returns (ordered_ideas, detail). Each surviving idea carries its composite
    score in `meta["composite"]`, so the book records WHY it was chosen rather
    than only what was chosen.

    The instrument's levels come from its highest-edge contributing strategy —
    the votes decide direction and conviction, but a stop has to come from a
    rule that actually computed one.
    """
    edges = edges if edges is not None else load_edges()[0]
    matrix = matrix if matrix is not None else load_correlations()
    default = float(((config or {}).get("composite") or {})
                    .get("unknown_correlation", UNKNOWN_CORRELATION))
    floor = float(((config or {}).get("composite") or {}).get("min_score", 0.0))

    by_ticker = {}
    for idea in ideas:
        by_ticker.setdefault(idea.ticker, []).append(idea)

    scored, rejected = [], []
    for ticker, group in by_ticker.items():
        votes = {}
        for idea in group:
            votes[idea.strategy] = 1 if idea.direction == "long" else -1
        verdict = combine(votes, edges, matrix, default_correlation=default)

        if not verdict["direction"] or verdict["score"] <= floor:
            rejected.append({"ticker": ticker, **verdict})
            continue

        # Levels from the strongest contributor that actually points the
        # agreed way. A stop borrowed from a dissenting rule would be sized
        # against a trade nobody is taking.
        aligned = [i for i in group if i.direction == verdict["direction"]]
        best = max(aligned, key=lambda i: edges.get(i.strategy, 0.0), default=None)
        if best is None:
            rejected.append({"ticker": ticker, **verdict,
                             "note": "no idea points the agreed way"})
            continue

        best.meta = dict(best.meta or {})
        best.meta["composite"] = verdict
        scored.append((verdict["score"], best))

    scored.sort(key=lambda pair: -pair[0])
    ordered = [idea for _, idea in scored]
    return ordered, {
        "ranked": len(ordered),
        "rejected": rejected,
        "edges_used": len([e for e in edges.values() if e and e > 0]),
        "correlations_used": len(matrix),
        "unknown_correlation": default,
    }


def describe(detail, age_days=None):
    """One line about what the ranking rested on."""
    if not detail.get("edges_used"):
        return ("No measured edges — the composite ranking has nothing to work "
                "from and the caller should fall back to the priority list.")
    stale = ""
    if age_days is not None and age_days > 60:
        stale = (f" The edge file is {age_days:.0f} days old; it may have been "
                 f"measured against a strategy library that has since changed.")
    return (f"{detail['ranked']} instruments ranked on {detail['edges_used']} "
            f"measured out-of-sample edges and a {detail['correlations_used']}"
            f"-strategy correlation matrix. {len(detail['rejected'])} rejected "
            f"for weak or conflicting agreement.{stale}")
