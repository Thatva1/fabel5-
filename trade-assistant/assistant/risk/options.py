"""Option pricing, Greeks, and implied volatility. Deterministic maths only.

WHY THIS EXISTS BEFORE ANY OPTION DATA DOES

The account has no OPRA subscription, so no option price reaches this stack.
That is a reason to build the maths first, not last. Everything here is a pure
function of numbers a caller supplies, so it is completely testable today
against published values, and when a quote feed does arrive the only new thing
will be the feed.

The alternative — waiting for data, then writing the pricer against live
quotes — is how you end up unable to tell a wrong model from a wrong feed.

WHAT AN OPTION POSITION NEEDS THAT A SHARE POSITION DOES NOT

A share's risk is its price times its size. An option's is not, and this is the
single most expensive misunderstanding available in this project:

  * A £5,000 premium outlay can carry £80,000 of directional exposure. Sizing
    an option book on premium is sizing it blind, which is why `exposure` below
    returns DELTA-adjusted notional and why nothing here reports premium as
    though it were risk.

  * The position changes character as the market moves. Delta is not constant;
    gamma says how fast it is not constant. A hedge that was neutral this
    morning is directional by lunchtime.

  * It decays. Theta is a cost paid every day for being right slowly, and it is
    the reason an option expresses a view about TIMING as much as direction.

  * Its value depends on a number nobody observes. Implied volatility is backed
    out of the price, not measured, so two people can disagree about whether an
    option is expensive while agreeing on every other input.

EUROPEAN AND AMERICAN

Black-Scholes prices a EUROPEAN option — exercisable only at expiry. That is
exactly right for index options (SPX, and the 0DTE trade that is the only
genuinely intraday option strategy worth this project's attention) and merely
close for US single-stock options, which are American.

The difference is not uniform and is worth stating rather than glossing:

  * An American CALL on a non-dividend-paying stock is worth exactly the same
    as the European one. Early exercise is never optimal — you would be
    throwing away the remaining time value. So Black-Scholes is exact here.
  * An American PUT is worth MORE, because exercising early to collect the
    strike and earn interest on it can be optimal. Black-Scholes UNDERSTATES
    it, by more when rates are high, the put is deep in the money, or expiry is
    far away.
  * A call on a stock that pays a dividend before expiry can also be worth
    exercising early.

So `binomial_price` is here too, and the two are offered side by side rather
than one silently standing in for the other. `early_exercise_premium` reports
the gap so a caller can see when it matters instead of assuming it does not.

WHAT THIS IS NOT

Not a volatility surface, not a term structure, not a model of skew. Every
function takes ONE volatility for ONE option. Fitting a surface needs a chain
of live quotes, and inventing one from a single number would produce prices
that look authoritative and are not.
"""
import math

# Below this many years to expiry an option is settled rather than priced: the
# maths divides by sqrt(T) and the answer stops meaning anything long before T
# reaches zero. Roughly nine minutes of a trading year.
MIN_YEARS = 1e-5
MIN_VOL = 1e-6

CALL = "call"
PUT = "put"


def _norm_cdf(x):
    """Standard normal CDF, via the error function in the standard library."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x):
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _d1_d2(spot, strike, years, rate, vol, dividend=0.0):
    total_vol = vol * math.sqrt(years)
    d1 = ((math.log(spot / strike) + (rate - dividend + 0.5 * vol * vol) * years)
          / total_vol)
    return d1, d1 - total_vol


def _intrinsic(kind, spot, strike):
    return max(0.0, spot - strike) if kind == CALL else max(0.0, strike - spot)


def _validate(kind, spot, strike):
    if kind not in (CALL, PUT):
        raise ValueError(f"kind must be {CALL!r} or {PUT!r}, got {kind!r}")
    if spot <= 0 or strike <= 0:
        raise ValueError("spot and strike must both be positive")


def price(kind, spot, strike, years, rate, vol, dividend=0.0):
    """Black-Scholes value of one EUROPEAN option, per share.

    Multiply by the contract multiplier (100 for US equity options) to get what
    one contract costs. That multiplication is deliberately NOT done here —
    it is the single most common place a factor of a hundred goes missing, and
    it belongs with the contract, not with the maths.
    """
    _validate(kind, spot, strike)
    # At or past expiry, and at zero volatility, the option IS its intrinsic
    # value. Returning that rather than dividing by zero is not a special case
    # bolted on; it is the limit the formula converges to.
    if years <= MIN_YEARS or vol <= MIN_VOL:
        return _intrinsic(kind, spot * math.exp(-dividend * max(years, 0.0)),
                          strike * math.exp(-rate * max(years, 0.0)))

    d1, d2 = _d1_d2(spot, strike, years, rate, vol, dividend)
    discounted_strike = strike * math.exp(-rate * years)
    carried_spot = spot * math.exp(-dividend * years)
    if kind == CALL:
        return carried_spot * _norm_cdf(d1) - discounted_strike * _norm_cdf(d2)
    return discounted_strike * _norm_cdf(-d2) - carried_spot * _norm_cdf(-d1)


def greeks(kind, spot, strike, years, rate, vol, dividend=0.0):
    """Delta, gamma, vega, theta and rho for one EUROPEAN option, per share.

    Units are chosen to be the ones a person actually quotes, and each is
    stated because getting them wrong is silent:

      delta  per 1.00 of underlying move      (dimensionless, -1..1)
      gamma  delta change per 1.00 of move
      vega   value change per 1 VOLATILITY POINT (0.01 of vol), not per 1.00
      theta  value change per CALENDAR DAY, not per year
      rho    value change per 1 percentage point of rate
    """
    _validate(kind, spot, strike)
    if years <= MIN_YEARS or vol <= MIN_VOL:
        # At expiry delta is the indicator of being in the money and every
        # second-order sensitivity has collapsed.
        in_money = (spot > strike) if kind == CALL else (spot < strike)
        return {"delta": (1.0 if kind == CALL else -1.0) if in_money else 0.0,
                "gamma": 0.0, "vega": 0.0, "theta": 0.0, "rho": 0.0}

    d1, d2 = _d1_d2(spot, strike, years, rate, vol, dividend)
    carry = math.exp(-dividend * years)
    discount = math.exp(-rate * years)
    pdf = _norm_pdf(d1)
    root_years = math.sqrt(years)

    gamma = carry * pdf / (spot * vol * root_years)
    vega = spot * carry * pdf * root_years / 100.0

    if kind == CALL:
        delta = carry * _norm_cdf(d1)
        theta_year = (-spot * carry * pdf * vol / (2 * root_years)
                      - rate * strike * discount * _norm_cdf(d2)
                      + dividend * spot * carry * _norm_cdf(d1))
        rho = strike * years * discount * _norm_cdf(d2) / 100.0
    else:
        delta = -carry * _norm_cdf(-d1)
        theta_year = (-spot * carry * pdf * vol / (2 * root_years)
                      + rate * strike * discount * _norm_cdf(-d2)
                      - dividend * spot * carry * _norm_cdf(-d1))
        rho = -strike * years * discount * _norm_cdf(-d2) / 100.0

    return {"delta": delta, "gamma": gamma, "vega": vega,
            "theta": theta_year / 365.0, "rho": rho}


# Below this vega, an option's price carries no information about its
# volatility. Measured in price units per volatility POINT, so 0.005 means a
# one-point move in implied vol shifts the price by half a cent — at which
# point the vol is uncertain by several points and any single number reported
# for it is false precision.
MIN_IDENTIFYING_VEGA = 0.005


def implied_vol(kind, market_price, spot, strike, years, rate, dividend=0.0,
                tolerance=1e-8, max_iterations=100,
                min_vega=MIN_IDENTIFYING_VEGA):
    """The volatility that reproduces an observed price. None when there is none.

    Newton's method with a BISECTION fallback, and the fallback is the point.
    Newton converges in three or four steps near the money and fails outright
    where vega approaches zero — deep in or out of the money, or close to
    expiry — which is exactly where 0DTE options live. A solver that works on
    the easy cases and silently diverges on the hard ones is worse than a
    slower one that always answers.

    Returns None rather than a number in two distinct cases, and the second is
    the subtle one.

    First, when the price is not attainable at ANY volatility — below intrinsic
    or above the underlying. Those are stale quotes or crossed markets, and
    inventing a volatility for them is how a bad tick becomes a signal.

    Second, and more often, when the price does not IDENTIFY a volatility. A
    call at 130 against a 100 strike with five months left is worth 31.98
    whether its volatility is 8% or 12%: vega is zero to six decimal places, so
    the price contains no information about vol and the solver can return
    anything that reprices correctly. It did exactly that — 0.08049 for an
    input of 0.08000 — and the number was right to eight decimal places in
    price and meaningless as a volatility.

    That distinction matters because an implied vol goes into a surface and is
    then treated as data. One fabricated from a deep in-the-money quote is
    indistinguishable from a measured one, and it will be the point that makes
    the skew look interesting.
    """
    _validate(kind, spot, strike)
    if market_price is None or market_price < 0 or years <= MIN_YEARS:
        return None

    # No volatility can price below intrinsic or above the bounding value, so
    # a quote outside those is not an option price.
    floor = price(kind, spot, strike, years, rate, MIN_VOL, dividend)
    ceiling = price(kind, spot, strike, years, rate, 5.0, dividend)
    if market_price < floor - tolerance or market_price > ceiling + tolerance:
        return None

    low, high = MIN_VOL, 5.0
    guess = 0.25
    for _ in range(max_iterations):
        modelled = price(kind, spot, strike, years, rate, guess, dividend)
        difference = modelled - market_price
        if abs(difference) < tolerance:
            return _identified(kind, spot, strike, years, rate, guess,
                               dividend, min_vega)
        # Keep the bracket honest whatever Newton does next.
        if difference > 0:
            high = guess
        else:
            low = guess

        vega = greeks(kind, spot, strike, years, rate, guess, dividend)["vega"] * 100.0
        if vega > 1e-8:
            step = guess - difference / vega
            if low < step < high:
                guess = step
                continue
        guess = 0.5 * (low + high)      # Newton refused or wandered; bisect

    if abs(price(kind, spot, strike, years, rate, guess, dividend)
           - market_price) >= 1e-4:
        return None
    return _identified(kind, spot, strike, years, rate, guess, dividend, min_vega)


def _identified(kind, spot, strike, years, rate, vol, dividend, min_vega):
    """The solution, or None when the price could not have pinned it down."""
    if min_vega and greeks(kind, spot, strike, years, rate, vol,
                           dividend)["vega"] < min_vega:
        return None
    return vol


def binomial_price(kind, spot, strike, years, rate, vol, dividend=0.0,
                   steps=200, american=True):
    """Cox-Ross-Rubinstein lattice, which can price EARLY EXERCISE.

    Here because US single-stock options are American and Black-Scholes is not.
    With `american=False` it converges to the Black-Scholes number, which is
    the test that says the lattice is built correctly.

    200 steps is convergence to well under a penny for ordinary inputs and
    costs microseconds. More steps buy precision that the bid-ask spread erases
    several times over.
    """
    _validate(kind, spot, strike)
    if years <= MIN_YEARS or vol <= MIN_VOL:
        return _intrinsic(kind, spot, strike)

    dt = years / steps
    up = math.exp(vol * math.sqrt(dt))
    down = 1.0 / up
    growth = math.exp((rate - dividend) * dt)
    # A lattice whose probability escapes [0,1] is not a probability. It
    # happens when the step is long relative to the volatility, and continuing
    # produces confident nonsense.
    probability = (growth - down) / (up - down)
    if not 0.0 <= probability <= 1.0:
        return price(kind, spot, strike, years, rate, vol, dividend)
    discount = math.exp(-rate * dt)

    values = [_intrinsic(kind, spot * (up ** (steps - 2 * i)), strike)
              for i in range(steps + 1)]
    for step in range(steps - 1, -1, -1):
        for i in range(step + 1):
            values[i] = discount * (probability * values[i]
                                    + (1 - probability) * values[i + 1])
            if american:
                node_spot = spot * (up ** (step - 2 * i))
                values[i] = max(values[i], _intrinsic(kind, node_spot, strike))
    return values[0]


def early_exercise_premium(kind, spot, strike, years, rate, vol, dividend=0.0,
                           steps=200):
    """What the right to exercise early is worth. Zero for most calls.

    Reported rather than absorbed so a caller can see WHEN the European number
    is safe to use. On a non-dividend-paying call this comes back at
    essentially zero, which is the theory confirming itself; on a deep
    in-the-money put with rates at 5% it is real money.
    """
    american = binomial_price(kind, spot, strike, years, rate, vol, dividend,
                              steps=steps, american=True)
    european = price(kind, spot, strike, years, rate, vol, dividend)
    return max(0.0, american - european)


def exposure(kind, spot, strike, years, rate, vol, contracts, *, dividend=0.0,
             multiplier=100):
    """What an option POSITION actually risks. The number sizing must use.

    Premium is what it costs; it is not what it risks, and the two differ by an
    order of magnitude in the direction that hurts. A £5,000 outlay in
    at-the-money calls carries something like £80,000 of directional exposure —
    size a book on the £5,000 and the first 6% move against you takes the lot.

    Returns premium, delta-adjusted notional, and the per-day and per-vol-point
    sensitivities, all scaled by contracts and multiplier so the caller never
    has to remember the hundred.
    """
    per_share = price(kind, spot, strike, years, rate, vol, dividend)
    g = greeks(kind, spot, strike, years, rate, vol, dividend)
    scale = contracts * multiplier
    return {
        "premium": per_share * scale,
        # Signed: a long put is negative delta, and a book that sums these
        # gets its true directional position rather than a gross total.
        "delta_notional": g["delta"] * spot * scale,
        "delta": g["delta"] * scale,
        "gamma": g["gamma"] * scale,
        "vega": g["vega"] * scale,
        "theta_per_day": g["theta"] * scale,
        # The honest worst case for a LONG position, and undefined for a short
        # one — a naked short call can lose without limit, which is why the
        # risk gate must never see None here and treat it as zero.
        "max_loss": per_share * scale if contracts > 0 else None,
        "contracts": contracts,
        "multiplier": multiplier,
    }


def years_to_expiry(days, *, calendar_days=365.0):
    """Days to expiry as a fraction of a year.

    Calendar days, not trading days, because that is the convention every
    quoted implied volatility is expressed in. Mixing the two is a quiet 25%
    error in every vega and theta.
    """
    return max(0.0, float(days)) / calendar_days
