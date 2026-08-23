"""A chain of quotes turned into implied volatilities.

The chain layer is where a plausible-looking volatility surface gets invented.
Every test here is about a way that happens: a stale last trade standing in for
a live market, a guessed dividend bending the whole curve the same way, a mid
IV quoted off a spread wide enough to contain any answer you like, and NaN
arithmeticking into a number.

No network. A synthetic chain built from the pricer at a KNOWN volatility is a
stronger check than a live one, because the right answer is known in advance.
"""
import math

import pytest

from assistant.research import option_chain
from assistant.risk import options

SPOT, YEARS, RATE = 100.0, 30 / 365, 0.05


def chain_at(vol, *, half_spread=0.02, strikes=range(85, 116, 5), dividend=0.0,
             skew_per_strike=0.0):
    """A two-sided chain priced from a known volatility."""
    rows = []
    for strike in strikes:
        v = vol + skew_per_strike * (SPOT - strike)
        call = options.price("call", SPOT, strike, YEARS, RATE, v, dividend)
        put = options.price("put", SPOT, strike, YEARS, RATE, v, dividend)
        rows.append({"strike": float(strike),
                     "call_bid": max(0.01, call - half_spread),
                     "call_ask": call + half_spread,
                     "put_bid": max(0.01, put - half_spread),
                     "put_ask": put + half_spread})
    return rows


# --- the volatility comes back ----------------------------------------------

def test_a_flat_chain_returns_the_volatility_it_was_built_from():
    out = option_chain.build(chain_at(0.22), SPOT, YEARS, RATE)
    assert out["surface"]["atm_iv"] == pytest.approx(0.22, abs=1e-3)
    assert out["surface"]["skew_25pct"] == pytest.approx(0.0, abs=2e-3)


def test_a_skewed_chain_shows_the_skew():
    """Downside puts priced richer than upside calls, which is what an equity
    index chain actually looks like."""
    out = option_chain.build(chain_at(0.22, skew_per_strike=0.004),
                             SPOT, YEARS, RATE)
    assert out["surface"]["skew_25pct"] > 0.03


# --- the forward is derived, not guessed -------------------------------------

def test_the_forward_comes_from_put_call_parity():
    """F = S e^rT with no dividend. Derived from the chain's own quotes rather
    than assumed, because a guessed dividend bends every implied vol in the
    chain the same way — which is indistinguishable from a real skew and rather
    more convincing than one."""
    out = option_chain.build(chain_at(0.22), SPOT, YEARS, RATE)
    assert out["forward"] == pytest.approx(SPOT * math.exp(RATE * YEARS), abs=0.05)
    assert out["carry"] == pytest.approx(0.0, abs=1e-3)
    assert "parity" in out["carry_source"]


def test_a_dividend_paying_underlying_has_its_carry_recovered():
    out = option_chain.build(chain_at(0.22, dividend=0.03), SPOT, YEARS, RATE)
    assert out["carry"] == pytest.approx(0.03, abs=2e-3)
    # And recovering it keeps the volatility honest.
    assert out["surface"]["atm_iv"] == pytest.approx(0.22, abs=2e-3)


def test_guessing_the_dividend_instead_would_have_bent_the_whole_curve():
    """The failure this defends against, demonstrated. Price a 3%-yielding
    underlying, then imply vols assuming no dividend: every strike moves, and
    it looks like structure."""
    rows = chain_at(0.22, dividend=0.03)
    honest = option_chain.build(rows, SPOT, YEARS, RATE)
    naive = [options.implied_vol("call", r["call_mid"], SPOT, r["strike"],
                                 YEARS, RATE, 0.0)
             for r in honest["rows"] if r["call_mid"] and r["call_iv_mid"]]
    naive = [v for v in naive if v is not None]
    assert naive, "expected some solvable strikes"
    # The naive vols are systematically off, and not by a rounding amount.
    assert max(abs(v - 0.22) for v in naive) > 0.004


def test_without_a_two_sided_strike_the_forward_falls_back_and_says_so():
    rows = [{"strike": 100.0, "call_bid": 2.0, "call_ask": 2.1,
             "put_bid": None, "put_ask": None}]
    out = option_chain.build(rows, SPOT, YEARS, RATE, dividend=0.01)
    assert out["forward"] is None
    assert out["carry"] == pytest.approx(0.01)
    assert "could not be derived" in out["carry_source"]


# --- a quote is a range, not a price -----------------------------------------

def test_every_strike_carries_the_volatility_each_side_implies():
    out = option_chain.build(chain_at(0.22), SPOT, YEARS, RATE)
    row = next(r for r in out["rows"] if r["strike"] == 100.0)
    assert row["call_iv_bid"] < row["call_iv_mid"] < row["call_iv_ask"]
    assert row["call_iv_width"] == pytest.approx(
        row["call_iv_ask"] - row["call_iv_bid"], abs=1e-9)


def test_the_same_spread_says_far_less_about_a_cheap_wing_option():
    """The reason a mid IV alone is misleading. Four cents on a £2.72
    at-the-money option pins the vol to a third of a point; four cents on a
    £0.04 wing option leaves three and a half points of range."""
    out = option_chain.build(chain_at(0.22), SPOT, YEARS, RATE)
    atm = next(r for r in out["rows"] if r["strike"] == 100.0)
    wing = next(r for r in out["rows"] if r["strike"] == 115.0)
    assert wing["call_iv_width"] > atm["call_iv_width"] * 5


def test_a_skew_smaller_than_the_spread_is_flagged_as_such():
    """Three points of skew read off quotes each six points wide is not a
    skew, it is two spreads."""
    tight = option_chain.build(chain_at(0.22, skew_per_strike=0.004,
                                        half_spread=0.01), SPOT, YEARS, RATE)
    wide = option_chain.build(chain_at(0.22, skew_per_strike=0.0002,
                                       half_spread=0.30), SPOT, YEARS, RATE)
    assert tight["surface"]["skew_exceeds_spread"] is True
    assert wide["surface"]["skew_exceeds_spread"] is False


def test_a_wide_quote_is_marked_wide():
    rows = [{"strike": 100.0, "call_bid": 1.05, "call_ask": 1.45,
             "put_bid": 1.0, "put_ask": 1.1}]
    out = option_chain.build(rows, SPOT, YEARS, RATE)
    row = out["rows"][0]
    assert row["call_wide"] is True
    assert row["put_wide"] is False
    assert row["call_spread_pct"] == pytest.approx(32.0, abs=0.5)


# --- what it refuses ---------------------------------------------------------

def test_a_one_sided_quote_has_no_mid():
    rows = [{"strike": 100.0, "call_bid": 2.0, "call_ask": None,
             "put_bid": None, "put_ask": 2.2}]
    out = option_chain.build(rows, SPOT, YEARS, RATE)
    assert out["rows"][0]["call_mid"] is None
    assert out["rows"][0]["put_mid"] is None


def test_a_crossed_quote_has_no_mid():
    """Ask below bid is a broken feed, and averaging it produces a number that
    looks entirely reasonable."""
    rows = [{"strike": 100.0, "call_bid": 3.0, "call_ask": 2.0,
             "put_bid": 1.0, "put_ask": 1.1}]
    out = option_chain.build(rows, SPOT, YEARS, RATE)
    assert out["rows"][0]["call_mid"] is None


def test_an_empty_chain_is_empty_rather_than_zero():
    """With no options subscription every field comes back None. That must be
    visibly different from a chain of zero volatilities."""
    rows = [{"strike": float(k), "call_bid": None, "call_ask": None,
             "put_bid": None, "put_ask": None} for k in (95, 100, 105)]
    out = option_chain.build(rows, SPOT, YEARS, RATE)
    assert out["surface"]["quoted_strikes"] == 0
    assert "not the same as a volatility of zero" in out["surface"]["note"]
    assert all(r["call_iv_mid"] is None for r in out["rows"])
    assert all(r["call_delta"] is None for r in out["rows"])


def test_a_strike_whose_price_cannot_identify_a_vol_reports_none():
    """Deep in the money, where vega is zero and any volatility reprices."""
    out = option_chain.build(chain_at(0.22, strikes=[60]), SPOT, YEARS, RATE)
    assert out["rows"][0]["call_iv_mid"] is None


def test_greeks_are_only_reported_where_a_volatility_was_found():
    out = option_chain.build(chain_at(0.22), SPOT, YEARS, RATE)
    for row in out["rows"]:
        has_iv = row["call_iv_mid"] is not None
        assert (row["call_delta"] is not None) == has_iv


# --- parity as a data-quality check ------------------------------------------

def test_a_stale_side_shows_up_as_a_parity_violation():
    """Parity is an arbitrage identity, not a model — it holds whatever
    volatility does. A strike that breaks it has a stale quote on one side, and
    every implied vol taken from that side is wrong in a way no volatility test
    would catch."""
    rows = chain_at(0.22)
    built = option_chain.build(rows, SPOT, YEARS, RATE)
    assert option_chain.parity_violations(
        built["rows"], SPOT, YEARS, RATE, built["carry"]) == []

    # Now stale one call by a pound.
    stale = built["rows"]
    target = next(r for r in stale if r["strike"] == 100.0)
    target["call_mid"] += 1.0
    bad = option_chain.parity_violations(stale, SPOT, YEARS, RATE, built["carry"])
    assert [v["strike"] for v in bad] == [100.0]
    assert bad[0]["gap"] == pytest.approx(1.0, abs=0.02)


# --- the boundary that stops NaN becoming a number ---------------------------

@pytest.mark.parametrize("raw,expected", [
    (float("nan"), None),      # IB's answer with no subscription
    (float("inf"), None),
    (-1, None),                # IB's own sentinel for "unavailable"
    (0, None),
    (None, None),
    ("not a number", None),
    (1.25, 1.25),
])
def test_ib_option_fields_are_cleaned_at_the_boundary(raw, expected):
    """NaN is the dangerous one: it is a float, it survives every isinstance
    check, and it arithmetics into a plausible number anywhere downstream that
    forgets to test for it."""
    from assistant.providers.ibkr_provider import IBKRDataProvider

    assert IBKRDataProvider.clean_option_field(raw) == expected


# --- picking the right chain out of the thirty-nine IB offers ----------------

class _Chain:
    def __init__(self, trading_class, strikes, expiries, exchange="CBOE"):
        self.tradingClass = trading_class
        self.strikes = list(range(strikes))
        self.expirations = list(range(expiries))
        self.exchange = exchange
        self.multiplier = "100"


def test_the_standard_class_is_chosen_not_whichever_came_first():
    """SPY comes back as 39 entries. Most are the standard `SPY` class with 491
    strikes from 50 to 1480; three are `2SPY`, a special class carrying exactly
    three strikes from 668 to 682.

    The old rule was "the SMART one, else the first". There is no SMART entry —
    SMART is a routing destination, not a listing venue — so it always fell
    through to the first, and IB does not return these in a stable order. One
    call got the full chain and the next got three strikes nowhere near a spot
    of 765, with no error to explain it."""
    import random

    from assistant.providers.ibkr_provider import IBKRDataProvider

    chains = [_Chain("2SPY", 3, 3), _Chain("SPY", 491, 32),
              _Chain("2SPY", 3, 3), _Chain("SPY", 491, 30)]
    for seed in range(8):
        random.Random(seed).shuffle(chains)
        picked = IBKRDataProvider._standard_chain(chains, "SPY")
        assert picked.tradingClass == "SPY"
        assert len(picked.strikes) == 491
        assert len(picked.expirations) == 32       # ties broken deterministically


def test_an_underlying_with_no_matching_class_still_gets_the_widest_chain():
    """Some underlyings genuinely list under a class that is not their symbol.
    Falling back to the widest coverage beats falling back to arbitrary."""
    from assistant.providers.ibkr_provider import IBKRDataProvider

    chains = [_Chain("WEEKLY", 4, 2), _Chain("ODD", 300, 20)]
    picked = IBKRDataProvider._standard_chain(chains, "XYZ")
    assert picked.tradingClass == "ODD"


def test_no_chains_at_all_is_an_error_not_a_crash():
    from assistant.providers.base import ProviderUnavailable
    from assistant.providers.ibkr_provider import IBKRDataProvider

    with pytest.raises(ProviderUnavailable):
        IBKRDataProvider._standard_chain([], "SPY")
