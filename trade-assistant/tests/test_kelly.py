"""Fractional Kelly sizing.

The arithmetic is short; the guards are the point. Every assertion below is
about a way Kelly turns a mis-estimated edge into a real loss, and each one
corresponds to a line in kelly.py that exists to stop it.
"""

import pytest

from assistant.risk import kelly


# --- the formulas ------------------------------------------------------------

def test_continuous_kelly_is_mean_over_variance():
    assert kelly.continuous_kelly(0.02, 0.04) == pytest.approx(0.5)
    assert kelly.continuous_kelly(0.01, 0.01) == pytest.approx(1.0)


def test_kelly_declines_a_losing_bet_rather_than_shorting_it():
    """Kelly's answer to a negative edge is a negative fraction. This library
    is long-only, so the expressible answer is "do not take it"."""
    assert kelly.continuous_kelly(-0.02, 0.04) == 0.0
    assert kelly.continuous_kelly(0.02, 0.0) == 0.0


def test_discrete_kelly_matches_the_textbook():
    # 60% win rate, wins twice the size of losses: 0.6 - 0.4/2 = 0.4
    assert kelly.discrete_kelly(0.6, 2.0) == pytest.approx(0.4)
    # A coin flip paying evens has no edge and no bet.
    assert kelly.discrete_kelly(0.5, 1.0) == pytest.approx(0.0)
    # Losing proposition.
    assert kelly.discrete_kelly(0.4, 1.0) == 0.0


def test_discrete_kelly_rejects_an_impossible_win_rate():
    with pytest.raises(ValueError):
        kelly.discrete_kelly(1.4, 2.0)


# --- the guards --------------------------------------------------------------

def test_sizing_from_an_in_sample_edge_is_refused():
    """The most important line in the module. Kelly bets hardest where the
    measured edge is largest, which on in-sample data is exactly where the
    overfitting is — so an in-sample Kelly bet is largest where it is most
    wrong."""
    returns = [0.01] * 100
    with pytest.raises(kelly.EdgeNotEstimable, match="in-sample"):
        kelly.fraction_for(returns, in_sample=True)


def test_too_few_observations_is_refused_not_rounded_to_zero():
    """"No edge" and "not enough data to tell" call for different actions, so
    they must not return the same value."""
    with pytest.raises(kelly.EdgeNotEstimable, match="observations"):
        kelly.fraction_for([0.01] * 5)


def test_the_edge_estimate_is_shrunk_toward_zero():
    """Kelly is linear in the edge and the damage from overestimating it is
    quadratic, so the point estimate is the wrong input."""
    rng = [0.01, -0.005, 0.02, 0.0, 0.015, -0.01] * 10
    raw = kelly.shrink_mean(rng, method="none")
    shrunk = kelly.shrink_mean(rng, method="lcb", confidence_z=1.0)
    assert 0 <= shrunk < raw, "the conservative estimate must sit below the mean"


def test_a_short_history_is_shrunk_harder_than_a_long_one():
    """Standard error falls as sqrt(n), so forty observations should not be
    sized like four hundred."""
    pattern = [0.01, -0.005, 0.02, 0.0]
    short = kelly.shrink_mean(pattern * 10, method="lcb")
    long = kelly.shrink_mean(pattern * 100, method="lcb")
    assert short < long, "less data must produce a more cautious estimate"


def test_the_fraction_is_never_full_kelly():
    """Full Kelly is only optimal with a perfectly known edge. It is not
    offered here at any configuration."""
    # A wildly profitable series, which full Kelly would size enormously.
    returns = [0.05] * 50 + [0.04] * 50
    fraction = kelly.fraction_for(returns)
    assert 0 < fraction <= kelly.DEFAULTS["max_position_fraction"]

    greedy = kelly.fraction_for(returns, config={"kelly": {"fraction": 10.0}})
    assert greedy <= kelly.DEFAULTS["max_position_fraction"], \
        "even an absurd configured fraction cannot exceed the position cap"


def test_a_bigger_kelly_fraction_bets_proportionally_more():
    """Deliberately noisy: a 3% daily swing for a 0.2% mean, over enough
    observations to survive shrinkage. A tighter series pins both fractions at
    the cap and the scaling becomes untestable."""
    rng = [0.032, -0.028] * 500
    quarter = kelly.fraction_for(rng, config={"kelly": {"fraction": 0.25,
                                                       "max_position_fraction": 1.0}})
    half = kelly.fraction_for(rng, config={"kelly": {"fraction": 0.5,
                                                     "max_position_fraction": 1.0}})
    assert 0 < quarter < 1.0, "the fixture must not be pinned at the cap"
    assert half == pytest.approx(quarter * 2, rel=1e-6)


def test_a_strong_edge_is_still_clamped_by_the_position_cap():
    """A high signal-to-noise series drives raw Kelly far above 1.0. What
    reaches the caller is the cap, not the formula's answer."""
    rng = [0.004, -0.002, 0.006, 0.0, 0.003, -0.001] * 20
    fraction = kelly.fraction_for(rng, config={"kelly": {"fraction": 0.25,
                                                        "max_position_fraction": 0.15}})
    assert fraction == pytest.approx(0.15)


# --- portfolio Kelly ---------------------------------------------------------

def test_portfolio_kelly_sizes_two_independent_strategies_separately():
    means = [0.01, 0.01]
    identity = [[0.04, 0.0], [0.0, 0.04]]
    fractions = kelly.portfolio_fractions(means, identity,
                                          config={"kelly": {"max_position_fraction": 1.0}})
    assert fractions[0] == pytest.approx(fractions[1])
    assert all(f > 0 for f in fractions)


def test_portfolio_kelly_cuts_the_size_of_correlated_strategies():
    """Two strategies that lose money at the same moments are one bet sized
    twice. The covariance matrix is what turns that from an assumption into
    arithmetic."""
    means = [0.01, 0.01]
    independent = [[0.04, 0.0], [0.0, 0.04]]
    correlated = [[0.04, 0.036], [0.036, 0.04]]     # rho = 0.9

    cfg = {"kelly": {"max_position_fraction": 1.0}}
    apart = kelly.portfolio_fractions(means, independent, config=cfg)
    together = kelly.portfolio_fractions(means, correlated, config=cfg)
    assert sum(together) < sum(apart), \
        "correlated strategies must be sized smaller in total"


def test_two_identical_strategies_are_refused_rather_than_double_sized():
    """A singular covariance matrix means one strategy entered twice. Falling
    back to independent sizing would size both in full — the exact error the
    matrix exists to prevent."""
    means = [0.01, 0.01]
    singular = [[0.04, 0.04], [0.04, 0.04]]
    with pytest.raises(kelly.EdgeNotEstimable, match="singular"):
        kelly.portfolio_fractions(means, singular)


def test_portfolio_kelly_refuses_in_sample_too():
    with pytest.raises(kelly.EdgeNotEstimable):
        kelly.portfolio_fractions([0.01], [[0.04]], in_sample=True)


def test_the_inverse_is_actually_an_inverse():
    matrix = [[4.0, 1.0, 0.0], [1.0, 3.0, 1.0], [0.0, 1.0, 2.0]]
    inverse = kelly._invert(matrix)
    size = len(matrix)
    for i in range(size):
        for j in range(size):
            product = sum(matrix[i][k] * inverse[k][j] for k in range(size))
            assert product == pytest.approx(1.0 if i == j else 0.0, abs=1e-9)


# --- the caps that sit above Kelly -------------------------------------------

def test_caps_override_kelly_rather_than_negotiating_with_it():
    """Kelly says 40% of a $100,000 book at $50 a share = 800 shares. The
    position cap says $15,000 = 300. The answer is 300."""
    units = kelly.apply_caps(0.40, equity=100_000, price=50.0,
                             max_position_value=15_000)
    assert units == 300


def test_the_exposure_and_group_rooms_both_bind():
    assert kelly.apply_caps(0.5, equity=100_000, price=100.0,
                            exposure_room=20_000) == 200
    assert kelly.apply_caps(0.5, equity=100_000, price=100.0,
                            group_room=5_000) == 50
    # The tightest cap wins.
    assert kelly.apply_caps(0.5, equity=100_000, price=100.0,
                            max_position_value=30_000, exposure_room=20_000,
                            group_room=5_000) == 50


def test_a_contract_multiplier_shrinks_the_unit_count():
    """One ES contract at 5,000 is $250,000 of index, not $5,000. A sizer that
    ignores the multiplier buys fifty times too many."""
    without = kelly.apply_caps(0.5, equity=1_000_000, price=5_000.0,
                               multiplier=1.0)
    with_mult = kelly.apply_caps(0.5, equity=1_000_000, price=5_000.0,
                                 multiplier=50.0)
    assert without == 100
    assert with_mult == 2


def test_no_edge_means_no_position():
    assert kelly.apply_caps(0.0, equity=100_000, price=50.0) == 0
    assert kelly.apply_caps(0.3, equity=0, price=50.0) == 0
    assert kelly.apply_caps(0.3, equity=100_000, price=0.0) == 0
