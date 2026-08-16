"""Fractional, covariance-aware Kelly sizing.

Kelly answers "what fraction of the bankroll maximises long-run growth", and
the honest version of that answer is uncomfortable: full Kelly is violently
aggressive, and it is only optimal if you know the edge exactly. You never do.
Every guard in this module exists because of the gap between the formula and
what can actually be estimated.

Four of them, in the order they bite:

**The edge comes from out-of-sample data only.** An edge measured on the same
history the rule was selected on is not an estimate, it is a description, and
Kelly sized on it bets hardest exactly where the overfitting is worst. This
module refuses to size from an in-sample estimate — not by convention but by
requiring the caller to say which it is.

**The estimate is shrunk toward zero.** Kelly is roughly linear in the edge and
quadratic in the damage from overestimating it, so a mean return that is half
noise produces a bet that is far more than half wrong. The default uses a
lower-confidence bound rather than the point estimate: bet the edge you can
nearly prove, not the one you measured.

**The result is scaled to a fraction.** Quarter-Kelly gives up about a quarter
of the theoretical growth rate for roughly a sixteenth of the variance in
outcomes. That trade is worth taking with an estimated edge, and full Kelly is
never offered here — the fraction is clamped below 1.

**Hard caps override it.** Gross exposure, per position, and per exposure group
all sit above Kelly, not beside it. If Kelly says 40% and the position cap says
15%, the answer is 15%. A growth-optimal fraction computed from a wrong number
is still a wrong number, and the caps are what stop it being catastrophic.

Nothing here is an LLM output or a fitted model. It is arithmetic, and every
piece of it is unit-tested.
"""
import math

DEFAULTS = {
    # Never full Kelly. 0.25 is the usual practitioner default; 0.5 is
    # aggressive; above that the drawdowns become hard to sit through even when
    # the edge is real.
    "fraction": 0.25,
    "max_fraction": 0.5,
    # Shrinkage. "lcb" uses a lower-confidence bound on the mean, which is the
    # conservative choice and the default. "none" uses the raw estimate and
    # exists so a test can show the difference.
    "shrinkage": "lcb",
    "confidence_z": 1.0,          # 1 standard error below the mean
    # Below this many observations an edge estimate is noise wearing a number.
    "min_observations": 30,
    # Absolute ceiling on any single position, whatever Kelly says.
    "max_position_fraction": 0.15,
}


def settings(config):
    return {**DEFAULTS, **((config or {}).get("kelly") or {})}


class EdgeNotEstimable(ValueError):
    """The edge cannot be estimated well enough to size from.

    Raised rather than returning zero so a caller cannot mistake "no edge" for
    "not enough data to tell" — the two call for different actions.
    """


def continuous_kelly(mean_return, variance):
    """f* = mean / variance, the continuous-outcome Kelly fraction.

    Returns 0.0 for a non-positive edge: Kelly's answer to a losing bet is to
    not take it, and this library is long-only, so a negative fraction has no
    expression here.
    """
    if variance is None or variance <= 0:
        return 0.0
    if mean_return is None or mean_return <= 0:
        return 0.0
    return float(mean_return) / float(variance)


def discrete_kelly(win_rate, win_loss_ratio):
    """f* = W - (1-W)/R, for a bet with two outcomes.

    The form to use when a strategy is described by hit rate and average
    win/loss rather than by a return series.
    """
    if win_rate is None or win_loss_ratio is None or win_loss_ratio <= 0:
        return 0.0
    win_rate = float(win_rate)
    if not 0.0 <= win_rate <= 1.0:
        raise ValueError(f"win_rate must be a probability, got {win_rate}")
    edge = win_rate - (1.0 - win_rate) / float(win_loss_ratio)
    return max(0.0, edge)


def shrink_mean(returns, method="lcb", confidence_z=1.0):
    """A deliberately pessimistic estimate of the mean return.

    The standard error of a mean falls as sqrt(n), so a short history produces
    a wide interval and this pulls the estimate a long way down — which is the
    intended behaviour. A strategy with forty observations should not be sized
    like one with four hundred.
    """
    n = len(returns)
    if n < 2:
        return 0.0
    mean = sum(returns) / n
    if method == "none":
        return mean
    variance = sum((r - mean) ** 2 for r in returns) / (n - 1)
    standard_error = math.sqrt(variance / n)
    # Toward zero from whichever side it sits on; never flipped past zero.
    lower = mean - float(confidence_z) * standard_error
    return max(0.0, lower) if mean > 0 else min(0.0, mean + float(confidence_z) * standard_error)


def fraction_for(returns, config=None, in_sample=False):
    """The fraction of equity to allocate, from a return series.

    `in_sample=True` is refused outright. Sizing from an in-sample edge is the
    single most reliable way to turn a backtest artefact into a real loss:
    Kelly bets hardest where the measured edge is largest, and on in-sample
    data that is precisely where the overfitting is.
    """
    if in_sample:
        raise EdgeNotEstimable(
            "Refusing to size from an in-sample edge. Kelly bets hardest where "
            "the measured edge is biggest, which on in-sample data is where the "
            "overfitting is worst. Pass out-of-sample returns.")

    cfg = settings(config)
    returns = [float(r) for r in returns if r is not None]
    if len(returns) < int(cfg["min_observations"]):
        raise EdgeNotEstimable(
            f"{len(returns)} observations, need {cfg['min_observations']}. An "
            "edge estimated from fewer is noise with a number attached.")

    mean = shrink_mean(returns, cfg["shrinkage"], cfg["confidence_z"])
    n = len(returns)
    raw_mean = sum(returns) / n
    variance = sum((r - raw_mean) ** 2 for r in returns) / (n - 1)

    full = continuous_kelly(mean, variance)
    scaled = full * min(float(cfg["fraction"]), float(cfg["max_fraction"]))
    return max(0.0, min(scaled, float(cfg["max_position_fraction"])))


def portfolio_fractions(means, covariance, config=None, in_sample=False):
    """Kelly across correlated strategies: f = inverse(covariance) @ means.

    The single-strategy formula treats each bet as independent. Two strategies
    that lose money at the same moments are one bet sized twice, and the
    covariance matrix is what turns that from an assumption into arithmetic.

    `means` and `covariance` must be ordered consistently. Returns a list of
    fractions in the same order, clipped at zero (long-only) and capped.
    """
    if in_sample:
        raise EdgeNotEstimable(
            "Refusing to size from an in-sample edge — see fraction_for.")

    cfg = settings(config)
    size = len(means)
    if size == 0:
        return []
    if len(covariance) != size or any(len(row) != size for row in covariance):
        raise ValueError("covariance must be square and match means")

    inverse = _invert(covariance)
    if inverse is None:
        # A singular matrix means two strategies are linearly dependent — the
        # same bet twice. Falling back to independent sizing would size both in
        # full, which is the error the matrix existed to prevent.
        raise EdgeNotEstimable(
            "Covariance matrix is singular: two of these return streams are "
            "linearly dependent, so they are one strategy entered twice. "
            "Drop one before sizing.")

    raw = [sum(inverse[i][j] * float(means[j]) for j in range(size))
           for i in range(size)]
    scale = min(float(cfg["fraction"]), float(cfg["max_fraction"]))
    cap = float(cfg["max_position_fraction"])
    return [max(0.0, min(f * scale, cap)) for f in raw]


def apply_caps(fraction, *, equity, price, multiplier=1.0, max_position_value=None,
               exposure_room=None, group_room=None):
    """Turn a fraction into a unit count, with every hard cap applied.

    The caps sit ABOVE Kelly rather than beside it. A growth-optimal fraction
    computed from a mis-estimated edge is still mis-estimated, and these are
    what keep that from being ruinous rather than merely disappointing.
    """
    if fraction <= 0 or price <= 0 or equity <= 0:
        return 0
    unit_notional = float(price) * float(multiplier)
    if unit_notional <= 0:
        return 0

    budget = float(fraction) * float(equity)
    for cap in (max_position_value, exposure_room, group_room):
        if cap is not None:
            budget = min(budget, float(cap))
    if budget <= 0:
        return 0
    return int(budget // unit_notional)


def _invert(matrix):
    """Gauss-Jordan inverse. None when the matrix is singular.

    Written out rather than pulled from numpy so the failure mode is explicit:
    a singular covariance matrix is a real finding about the strategies, not an
    exception to swallow.
    """
    size = len(matrix)
    work = [[float(matrix[i][j]) for j in range(size)] +
            [1.0 if i == j else 0.0 for j in range(size)] for i in range(size)]

    for col in range(size):
        pivot = max(range(col, size), key=lambda r: abs(work[r][col]))
        if abs(work[pivot][col]) < 1e-12:
            return None
        work[col], work[pivot] = work[pivot], work[col]
        divisor = work[col][col]
        work[col] = [v / divisor for v in work[col]]
        for row in range(size):
            if row == col:
                continue
            factor = work[row][col]
            if factor:
                work[row] = [v - factor * w for v, w in zip(work[row], work[col])]
    return [row[size:] for row in work]
