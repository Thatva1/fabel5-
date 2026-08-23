"""Option pricing, Greeks and implied volatility.

Pure maths, so it is checkable against published numbers rather than against
itself — which matters here more than usual, because there is no option data on
this account to sanity-check against. A pricer written to agree with a feed
that does not exist would agree with nothing.

The failures worth guarding are the ones that produce a confident wrong number:
a solver that diverges silently where vega vanishes, a European price standing
in for an American one, and premium being mistaken for risk.
"""
import math

import pytest

from assistant.risk import options


# --- against published values ------------------------------------------------
#
# Hull, "Options, Futures and Other Derivatives": S=100, K=100, T=1, r=5%,
# sigma=20%, no dividend. Call 10.4506, put 5.5735.

def test_the_textbook_call_and_put():
    assert options.price("call", 100, 100, 1.0, 0.05, 0.20) == pytest.approx(10.4506, abs=1e-4)
    assert options.price("put", 100, 100, 1.0, 0.05, 0.20) == pytest.approx(5.5735, abs=1e-4)


def test_put_call_parity_holds_everywhere_it_should():
    """C - P = S*e^-qT - K*e^-rT. An identity, not an approximation: if this
    drifts, one of the two formulas is wrong and no amount of eyeballing a
    single price would reveal which."""
    for spot in (50, 90, 100, 110, 250):
        for years in (0.05, 0.5, 2.0):
            for vol in (0.1, 0.35, 0.9):
                for dividend in (0.0, 0.03):
                    call = options.price("call", spot, 100, years, 0.05, vol, dividend)
                    put = options.price("put", spot, 100, years, 0.05, vol, dividend)
                    expected = (spot * math.exp(-dividend * years)
                                - 100 * math.exp(-0.05 * years))
                    assert call - put == pytest.approx(expected, abs=1e-9)


def test_a_call_is_worth_more_as_the_underlying_rises():
    prices = [options.price("call", s, 100, 0.5, 0.05, 0.2) for s in (80, 95, 100, 120)]
    assert prices == sorted(prices)


def test_more_volatility_is_worth_more_to_both_sides():
    """The one thing a long option owner is actually buying."""
    for kind in ("call", "put"):
        values = [options.price(kind, 100, 100, 0.5, 0.05, v)
                  for v in (0.05, 0.2, 0.5, 1.0)]
        assert values == sorted(values)


# --- the limits, which are where a formula divides by zero -------------------

def test_at_expiry_an_option_is_its_intrinsic_value():
    """Not a special case bolted on — the limit the formula converges to. The
    alternative is dividing by sqrt(0)."""
    assert options.price("call", 120, 100, 0.0, 0.05, 0.3) == pytest.approx(20.0)
    assert options.price("call", 90, 100, 0.0, 0.05, 0.3) == pytest.approx(0.0)
    assert options.price("put", 90, 100, 0.0, 0.05, 0.3) == pytest.approx(10.0)
    assert options.price("put", 120, 100, 0.0, 0.05, 0.3) == pytest.approx(0.0)


def test_zero_volatility_is_a_forward_not_a_crash():
    value = options.price("call", 100, 100, 1.0, 0.05, 0.0)
    assert value == pytest.approx(100 - 100 * math.exp(-0.05), abs=1e-9)


def test_an_option_is_never_worth_less_than_nothing():
    for kind in ("call", "put"):
        for spot in (1, 50, 100, 500):
            assert options.price(kind, spot, 100, 0.25, 0.05, 0.2) >= 0.0


def test_a_nonsense_contract_is_refused_rather_than_priced():
    with pytest.raises(ValueError):
        options.price("straddle", 100, 100, 1.0, 0.05, 0.2)
    with pytest.raises(ValueError):
        options.price("call", 0, 100, 1.0, 0.05, 0.2)
    with pytest.raises(ValueError):
        options.price("call", 100, -5, 1.0, 0.05, 0.2)


# --- Greeks ------------------------------------------------------------------

def test_delta_matches_a_numerical_derivative():
    """The definition, checked against the implementation. Every Greek here is
    hand-differentiated and a sign error in one term is invisible by eye."""
    for kind in ("call", "put"):
        for spot in (85, 100, 115):
            h = 1e-5
            up = options.price(kind, spot + h, 100, 0.5, 0.05, 0.25)
            down = options.price(kind, spot - h, 100, 0.5, 0.05, 0.25)
            numeric = (up - down) / (2 * h)
            assert options.greeks(kind, spot, 100, 0.5, 0.05, 0.25)["delta"] \
                == pytest.approx(numeric, abs=1e-5)


def test_gamma_matches_a_numerical_second_derivative():
    h = 1e-3
    spot, args = 100, (100, 0.5, 0.05, 0.25)
    second = (options.price("call", spot + h, *args)
              - 2 * options.price("call", spot, *args)
              + options.price("call", spot - h, *args)) / (h * h)
    assert options.greeks("call", spot, *args)["gamma"] == pytest.approx(second, abs=1e-4)


def test_vega_is_quoted_per_volatility_POINT_not_per_unit():
    """A vega quoted per 1.00 of vol is a hundred times the number anyone
    means, and it is the sort of error that only shows up in a position size."""
    h = 1e-6
    up = options.price("call", 100, 100, 0.5, 0.05, 0.25 + h)
    down = options.price("call", 100, 100, 0.5, 0.05, 0.25 - h)
    per_unit = (up - down) / (2 * h)
    assert options.greeks("call", 100, 100, 0.5, 0.05, 0.25)["vega"] \
        == pytest.approx(per_unit / 100.0, abs=1e-6)


def test_theta_is_quoted_per_CALENDAR_DAY():
    h = 1e-6
    later = options.price("call", 100, 100, 0.5 - h, 0.05, 0.25)
    now = options.price("call", 100, 100, 0.5 + h, 0.05, 0.25)
    per_year = (later - now) / (2 * h)
    assert options.greeks("call", 100, 100, 0.5, 0.05, 0.25)["theta"] \
        == pytest.approx(per_year / 365.0, abs=1e-6)


def test_gamma_and_vega_are_the_same_for_a_call_and_its_put():
    """They differ only by a discounted strike, which has no second derivative
    in spot and no sensitivity to volatility. A test that catches a whole class
    of copy-paste error between the two branches."""
    call = options.greeks("call", 100, 100, 0.5, 0.05, 0.25)
    put = options.greeks("put", 100, 100, 0.5, 0.05, 0.25)
    assert call["gamma"] == pytest.approx(put["gamma"], abs=1e-12)
    assert call["vega"] == pytest.approx(put["vega"], abs=1e-12)


def test_delta_has_the_right_sign_and_bounds():
    for spot in (60, 100, 140):
        call = options.greeks("call", spot, 100, 0.5, 0.05, 0.25)["delta"]
        put = options.greeks("put", spot, 100, 0.5, 0.05, 0.25)["delta"]
        assert 0.0 <= call <= 1.0
        assert -1.0 <= put <= 0.0


def test_at_expiry_the_second_order_greeks_have_collapsed():
    g = options.greeks("call", 120, 100, 0.0, 0.05, 0.3)
    assert g["delta"] == 1.0 and g["gamma"] == 0.0 and g["vega"] == 0.0


# --- implied volatility ------------------------------------------------------

def test_implied_vol_round_trips_wherever_the_price_identifies_it():
    """Only where vega is large enough for the price to carry the information.
    Where it is not, the solver refuses — see the next test."""
    recovered = 0
    for kind in ("call", "put"):
        for vol in (0.08, 0.2, 0.55, 1.2):
            for spot in (80, 100, 130):
                mark = options.price(kind, spot, 100, 0.4, 0.05, vol)
                solved = options.implied_vol(kind, mark, spot, 100, 0.4, 0.05)
                if solved is None:
                    continue
                assert solved == pytest.approx(vol, abs=1e-5), (kind, vol, spot)
                recovered += 1
    assert recovered >= 18, "most of these should be solvable"


def test_a_price_that_does_not_identify_a_volatility_is_refused():
    """A 130 call against a 100 strike with five months left is worth 31.98
    whether its vol is 8% or 12% — vega is zero to six decimals. The solver
    returned 0.08049 for an input of 0.08000: correct to eight decimal places
    in PRICE, and meaningless as a volatility.

    This matters because an implied vol goes into a surface and is then treated
    as data. A fabricated one is indistinguishable from a measured one, and it
    will be the point that makes the skew look interesting."""
    mark = options.price("call", 130, 100, 0.4, 0.05, 0.08)
    assert options.greeks("call", 130, 100, 0.4, 0.05, 0.08)["vega"] < 1e-5
    assert options.implied_vol("call", mark, 130, 100, 0.4, 0.05) is None

    # A caller who genuinely wants the number anyway can ask for it.
    forced = options.implied_vol("call", mark, 130, 100, 0.4, 0.05, min_vega=0)
    assert forced is not None
    assert options.price("call", 130, 100, 0.4, 0.05, forced) \
        == pytest.approx(mark, abs=1e-4)


def test_the_same_option_becomes_solvable_once_it_has_real_vega():
    """The refusal is about the QUOTE, not the strike. Raise the volatility and
    the identical contract identifies itself."""
    mark = options.price("call", 130, 100, 0.4, 0.05, 0.35)
    assert options.implied_vol("call", mark, 130, 100, 0.4, 0.05) \
        == pytest.approx(0.35, abs=1e-5)


@pytest.mark.parametrize("spot,years,vol", [
    (100, 0.002, 0.45),   # hours from expiry: the 0DTE case
    (115, 0.020, 0.45),   # in the money, a week out — vega 0.005, the boundary
    (120, 0.050, 0.45),
    (88, 0.050, 0.45),
    (108, 0.010, 0.80),
])
def test_it_still_solves_where_vega_is_small_but_real(spot, years, vol):
    """Where Newton alone diverges and the bisection fallback has to carry it —
    hours from expiry and away from the money, which is exactly where the 0DTE
    index options this project cares about actually live.

    These are distinct from the DEEP in-the-money cases above: vega here is
    small (0.005 to 0.04) but non-zero, so the price genuinely does identify a
    volatility and a solver that gives up would be losing real information."""
    mark = options.price("call", spot, 100, years, 0.05, vol)
    solved = options.implied_vol("call", mark, spot, 100, years, 0.05)
    assert solved is not None, (spot, years)
    assert solved == pytest.approx(vol, abs=1e-4)


def test_newton_is_not_what_is_doing_the_work_in_those_cases():
    """A guard on the guard. If vega at the solution is below what Newton needs
    to converge, then bisection found it — and removing the fallback would
    break these silently rather than loudly."""
    vega = options.greeks("call", 100, 100, 0.002, 0.05, 0.45)["vega"]
    assert vega < 0.02, "pick a harder case; Newton would manage this one"


def test_a_price_below_intrinsic_has_no_implied_volatility():
    """A crossed or stale quote. Inventing a volatility for it is how a bad
    tick becomes a trading signal."""
    assert options.implied_vol("call", 5.0, 120, 100, 0.5, 0.05) is None


def test_a_price_above_the_underlying_has_no_implied_volatility():
    assert options.implied_vol("call", 150.0, 100, 100, 0.5, 0.05) is None


def test_a_missing_or_negative_quote_is_none_not_a_guess():
    assert options.implied_vol("call", None, 100, 100, 0.5, 0.05) is None
    assert options.implied_vol("call", -1.0, 100, 100, 0.5, 0.05) is None


def test_an_expired_option_has_no_implied_volatility():
    assert options.implied_vol("call", 20.0, 120, 100, 0.0, 0.05) is None


# --- American exercise -------------------------------------------------------

def test_the_lattice_converges_to_black_scholes_when_exercise_is_european():
    """The test that says the lattice is built correctly rather than merely
    producing plausible numbers."""
    for kind in ("call", "put"):
        for spot in (85, 100, 115):
            lattice = options.binomial_price(kind, spot, 100, 1.0, 0.05, 0.25,
                                             steps=500, american=False)
            closed = options.price(kind, spot, 100, 1.0, 0.05, 0.25)
            assert lattice == pytest.approx(closed, abs=0.02), (kind, spot)


def test_an_american_call_on_a_non_dividend_payer_is_worth_no_more():
    """Theory: early exercise throws away time value and is never optimal. If
    this ever fails, the lattice is finding an exercise that should not exist."""
    premium = options.early_exercise_premium("call", 130, 100, 1.0, 0.05, 0.25)
    assert premium == pytest.approx(0.0, abs=0.01)


def test_an_american_put_is_worth_more_and_the_gap_is_reported():
    """Deep in the money with rates at 5%: exercising to collect the strike and
    earn interest on it is optimal, so Black-Scholes UNDERSTATES this."""
    premium = options.early_exercise_premium("put", 60, 100, 1.0, 0.05, 0.25)
    assert premium > 0.5
    american = options.binomial_price("put", 60, 100, 1.0, 0.05, 0.25)
    assert american >= options.price("put", 60, 100, 1.0, 0.05, 0.25)
    # And it can never be worth less than exercising right now.
    assert american >= 100 - 60 - 1e-6


def test_a_degenerate_lattice_falls_back_rather_than_inventing_a_probability():
    """A step long relative to the volatility pushes the risk-neutral
    probability outside [0,1]. Continuing produces confident nonsense."""
    value = options.binomial_price("call", 100, 100, 5.0, 0.9, 0.01, steps=3)
    assert value == pytest.approx(
        options.price("call", 100, 100, 5.0, 0.9, 0.01), abs=1e-9)


# --- what a POSITION risks ---------------------------------------------------

def test_premium_is_not_risk_and_the_numbers_say_so():
    """The most expensive misunderstanding available here: a five-thousand
    pound outlay carrying eighty thousand of directional exposure."""
    e = options.exposure("call", 100, 100, 0.5, 0.05, 0.25, contracts=10)
    assert e["premium"] == pytest.approx(
        options.price("call", 100, 100, 0.5, 0.05, 0.25) * 1000)
    # Delta-adjusted notional is several times the premium — about 7x for a
    # six-month at-the-money call, and far more as expiry approaches.
    assert e["delta_notional"] > e["premium"] * 5
    near_expiry = options.exposure("call", 100, 100, 0.02, 0.05, 0.25, contracts=10)
    assert near_expiry["delta_notional"] > near_expiry["premium"] * 25
    assert e["max_loss"] == pytest.approx(e["premium"])


def test_a_long_put_carries_negative_delta_so_a_book_can_be_summed():
    calls = options.exposure("call", 100, 100, 0.5, 0.05, 0.25, contracts=10)
    puts = options.exposure("put", 100, 100, 0.5, 0.05, 0.25, contracts=10)
    assert puts["delta_notional"] < 0 < calls["delta_notional"]
    # A rough collar nets out rather than doubling up.
    assert abs(calls["delta_notional"] + puts["delta_notional"]) \
        < abs(calls["delta_notional"])


def test_a_short_position_reports_no_max_loss_rather_than_zero():
    """A naked short call can lose without limit. Reporting zero would let a
    risk gate wave it through as the safest position in the book."""
    short = options.exposure("call", 100, 100, 0.5, 0.05, 0.25, contracts=-5)
    assert short["max_loss"] is None


def test_the_multiplier_is_applied_once_and_only_once():
    one = options.exposure("call", 100, 100, 0.5, 0.05, 0.25, contracts=1)
    per_share = options.price("call", 100, 100, 0.5, 0.05, 0.25)
    assert one["premium"] == pytest.approx(per_share * 100)
    index = options.exposure("call", 100, 100, 0.5, 0.05, 0.25, contracts=1,
                             multiplier=1)
    assert index["premium"] == pytest.approx(per_share)


def test_theta_is_a_daily_cost_scaled_to_the_position():
    e = options.exposure("call", 100, 100, 0.5, 0.05, 0.25, contracts=10)
    assert e["theta_per_day"] < 0
    per_share = options.greeks("call", 100, 100, 0.5, 0.05, 0.25)["theta"]
    assert e["theta_per_day"] == pytest.approx(per_share * 1000)


def test_years_to_expiry_uses_calendar_days():
    """Every quoted implied volatility is expressed in calendar time. Mixing in
    trading days is a quiet 25% error in every vega and theta."""
    assert options.years_to_expiry(365) == pytest.approx(1.0)
    assert options.years_to_expiry(0) == 0.0
    assert options.years_to_expiry(-5) == 0.0
