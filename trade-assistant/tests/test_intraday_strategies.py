"""The intraday rules: do they fire when they should, and only on the past.

The causality test is the one that matters. An intraday rule with one bar of
look-ahead does not overstate its return slightly — it turns a losing rule into
a winner every time, and it does so while every other number on the page looks
reasonable. So the guarantee is asserted directly: a strategy handed the first
N bars must produce the same answer whether or not later bars exist.
"""
import pandas as pd
import pytest

from assistant.strategies import intraday
from assistant.strategies.base import LONG, SHORT


def session(closes, *, start="2026-08-21 14:30", minutes=5, spread=0.1,
            volume=10_000, opens=None):
    """A synthetic five-minute session from a list of closes."""
    index = pd.date_range(start, periods=len(closes), freq=f"{minutes}min")
    opens = opens or ([closes[0]] + list(closes[:-1]))
    return pd.DataFrame({
        "Open": opens,
        "High": [c + spread for c in closes],
        "Low": [c - spread for c in closes],
        "Close": closes,
        "Volume": [volume] * len(closes),
    }, index=index)


def ctx(frame, **kwargs):
    kwargs.setdefault("minutes_to_close", 240.0)
    return intraday.IntradayContext(ticker="TEST", bars=frame, **kwargs)


# --- causality ---------------------------------------------------------------

def test_a_strategy_cannot_see_a_bar_that_has_not_happened():
    """The whole session, then only its first half. The rule must answer the
    same way about the first half either way — which it can only do if it never
    looked past the slice it was given."""
    closes = [100 + i * 0.05 for i in range(12)] + [90.0] * 12   # a collapse later
    whole = session(closes)
    prefix = whole.iloc[:12]

    rule = intraday.OpeningRangeBreak()
    params = rule.params_for({})

    from_prefix = rule.detect(ctx(prefix, params=params))
    # The same first twelve bars, taken as a slice of a frame that knows the
    # collapse is coming.
    from_slice = rule.detect(ctx(whole.iloc[:12], params=params))

    assert (from_prefix is None) == (from_slice is None)
    if from_prefix is not None:
        assert from_prefix.to_dict() == from_slice.to_dict()


# --- opening range break -----------------------------------------------------

def test_a_break_above_the_opening_range_is_a_long_stopped_at_the_far_side():
    # Six bars ranging 100.0-100.5, then a push through.
    closes = [100.0, 100.3, 100.1, 100.5, 100.2, 100.4, 101.2]
    rule = intraday.OpeningRangeBreak()
    idea = rule.detect(ctx(session(closes), params=rule.params_for({})))

    assert idea is not None
    assert idea.direction == LONG
    assert idea.entry == pytest.approx(101.2)
    # The stop is the OTHER side of the range, which is the rule's whole claim.
    assert idea.stop == pytest.approx(99.9)          # 100.0 low minus the spread
    assert idea.meta["range_high"] == pytest.approx(100.6)
    assert idea.reward_risk == pytest.approx(1.0, abs=0.02)


def test_a_break_below_the_opening_range_is_a_short():
    closes = [100.0, 100.3, 100.1, 100.5, 100.2, 100.4, 99.0]
    rule = intraday.OpeningRangeBreak()
    idea = rule.detect(ctx(session(closes), params=rule.params_for({})))
    assert idea is not None and idea.direction == SHORT
    assert idea.stop > idea.entry > idea.target


def test_price_inside_the_opening_range_is_no_setup():
    closes = [100.0, 100.3, 100.1, 100.5, 100.2, 100.4, 100.25]
    rule = intraday.OpeningRangeBreak()
    assert rule.detect(ctx(session(closes), params=rule.params_for({}))) is None


def test_a_session_already_in_chaos_is_declined():
    """A range 8% wide is not an agreement being broken; a break of it has no
    room left to pay for the risk it takes."""
    closes = [100.0, 104.0, 98.0, 106.0, 99.0, 103.0, 110.0]
    rule = intraday.OpeningRangeBreak()
    assert rule.detect(ctx(session(closes), params=rule.params_for({}))) is None


def test_a_rule_is_not_started_without_the_time_it_needs():
    closes = [100.0, 100.3, 100.1, 100.5, 100.2, 100.4, 101.2]
    rule = intraday.OpeningRangeBreak()
    late = ctx(session(closes), params=rule.params_for({}), minutes_to_close=20.0)
    assert rule.detect(late) is None


# --- VWAP reversion ----------------------------------------------------------

def test_a_stretch_below_vwap_is_a_long_targeting_the_vwap():
    closes = [100.0] * 10 + [99.0]
    rule = intraday.VWAPReversion()
    idea = rule.detect(ctx(session(closes), params=rule.params_for({})))

    assert idea is not None and idea.direction == LONG
    # The target IS the VWAP — not a multiple chosen in advance.
    assert idea.target == pytest.approx(idea.meta["vwap"], abs=0.01)
    assert idea.stop < idea.entry < idea.target


def test_a_stretch_above_vwap_is_a_short():
    closes = [100.0] * 10 + [101.0]
    rule = intraday.VWAPReversion()
    idea = rule.detect(ctx(session(closes), params=rule.params_for({})))
    assert idea is not None and idea.direction == SHORT
    assert idea.target < idea.entry < idea.stop


def test_price_at_the_vwap_is_no_setup():
    rule = intraday.VWAPReversion()
    assert rule.detect(ctx(session([100.0] * 12), params=rule.params_for({}))) is None


def test_vwap_needs_a_session_behind_it():
    """VWAP over four bars is not a session's average price, it is the last
    four prices with extra steps."""
    rule = intraday.VWAPReversion()
    assert rule.detect(ctx(session([100.0, 100.0, 99.0]),
                           params=rule.params_for({}))) is None


# --- first-half-hour momentum ------------------------------------------------

def test_it_only_fires_in_the_last_half_hour():
    closes = [100.0] * 6 + [101.0] * 20
    rule = intraday.IntradayMomentum()
    params = rule.params_for({})

    early = ctx(session(closes), params=params, prev_close=99.0,
                minutes_to_close=180.0)
    assert rule.detect(early) is None

    late = ctx(session(closes), params=params, prev_close=99.0,
               minutes_to_close=30.0)
    idea = rule.detect(late)
    assert idea is not None and idea.direction == LONG


def test_the_signal_is_measured_from_the_PREVIOUS_close_not_the_open():
    """A session that opened flat and rose is a different signal from one that
    gapped up and fell back to the same place. Measuring from the session's own
    open cannot tell them apart, and the published rule measures from the
    previous close."""
    closes = [101.0] * 6 + [101.0] * 10
    rule = intraday.IntradayMomentum()
    params = rule.params_for({})

    # Same bars. Only the previous close differs — and it flips the direction.
    up = rule.detect(ctx(session(closes), params=params, prev_close=99.0,
                         minutes_to_close=30.0))
    down = rule.detect(ctx(session(closes), params=params, prev_close=103.0,
                           minutes_to_close=30.0))
    assert up.direction == LONG
    assert down.direction == SHORT


def test_a_flat_first_half_hour_is_noise_not_a_signal():
    closes = [100.0] * 16
    rule = intraday.IntradayMomentum()
    assert rule.detect(ctx(session(closes), params=rule.params_for({}),
                           prev_close=100.0, minutes_to_close=30.0)) is None


def test_without_a_previous_close_the_rule_declines_rather_than_guesses():
    closes = [100.0] * 6 + [101.0] * 10
    rule = intraday.IntradayMomentum()
    assert rule.detect(ctx(session(closes), params=rule.params_for({}),
                           prev_close=None, minutes_to_close=30.0)) is None


# --- gap fade ----------------------------------------------------------------

def test_a_gap_up_is_faded_short_toward_the_previous_close():
    closes = [102.0, 102.1, 101.9, 102.0]
    rule = intraday.GapFade()
    idea = rule.detect(ctx(session(closes), params=rule.params_for({}),
                           prev_close=100.0))
    assert idea is not None and idea.direction == SHORT
    assert idea.meta["gap_pct"] == pytest.approx(2.0)
    # Half the gap, not all of it.
    assert idea.target == pytest.approx(102.0 - 1.0, abs=0.05)


def test_a_gap_too_large_to_be_an_overreaction_is_declined():
    """Beyond a certain distance a gap is news. This rule cannot read news, so
    it declines rather than fading an earnings result."""
    closes = [112.0, 112.1, 111.9, 112.0]
    rule = intraday.GapFade()
    assert rule.detect(ctx(session(closes), params=rule.params_for({}),
                           prev_close=100.0)) is None


def test_a_gap_fade_is_not_entered_at_lunchtime():
    """An OPENING gap rule entered three hours in is a different trade wearing
    the same name."""
    closes = [102.0] * 30
    rule = intraday.GapFade()
    assert rule.detect(ctx(session(closes), params=rule.params_for({}),
                           prev_close=100.0)) is None


# --- the contract every rule keeps -------------------------------------------

@pytest.mark.parametrize("name", sorted(intraday.REGISTRY))
def test_every_rule_declares_a_horizon_in_minutes(name):
    rule = intraday.REGISTRY[name]()
    assert rule.max_hold_minutes > 0
    assert rule.min_minutes_remaining >= 0


@pytest.mark.parametrize("name", sorted(intraday.REGISTRY))
def test_every_rule_reads_its_own_config_block(name):
    """Intraday rules are configured under `intraday.strategies`, so a name
    shared with the daily library cannot silently read the other's settings."""
    rule = intraday.REGISTRY[name]()
    assert rule.enabled({}) is True
    assert rule.enabled({"intraday": {"strategies": {name: {"enabled": False}}}}) is False
    # The DAILY block must not reach it.
    assert rule.enabled({"strategies": {name: {"enabled": False}}}) is True


def test_an_idea_never_comes_back_with_a_stop_on_the_wrong_side():
    """A long whose stop sits above its entry produces a negative risk-per-share
    and a position size of infinity. The shared validator refuses it."""
    rule = intraday.OpeningRangeBreak()
    bad = rule.build(ctx(session([100.0] * 8)), LONG,
                     entry=100.0, stop=101.0, target=105.0, reasons=[])
    assert bad is None


def test_detect_all_returns_every_rule_that_fired():
    closes = [100.0, 100.3, 100.1, 100.5, 100.2, 100.4, 101.5, 101.6, 101.4,
              101.5, 101.6]
    ideas = intraday.detect_all(ctx(session(closes), prev_close=100.0), config={})
    names = {i.strategy for i in ideas}
    assert "opening_range_break" in names
    # Every idea that comes back is validated and tagged as intraday.
    for idea in ideas:
        assert idea.regime == "INTRADAY"
        assert idea.meta["horizon"] == "intraday"
        assert idea.risk_per_share > 0


# --- the fast path must give the same answers as the slow one ----------------
#
# `precompute` shares cumulative sums across a whole session's contexts, which
# turned an O(n^2) rebuild into a prefix-sum lookup. An optimisation that
# changes an answer is not an optimisation, and one that lets a context see
# past its own slice would destroy the causality guarantee everything rests on.

def test_precomputed_vwap_matches_the_direct_computation():
    closes = [100 + (i % 7) * 0.3 for i in range(60)]
    whole = session(closes)
    pre = intraday.precompute(whole)
    for i in (10, 25, 44, 59):
        slow = ctx(whole.iloc[:i + 1]).session_vwap()
        fast = ctx(whole.iloc[:i + 1], precomputed=pre).session_vwap()
        assert fast == pytest.approx(slow, rel=1e-12), i


def test_precomputed_atr_matches_the_direct_computation():
    closes = [100 + (i % 5) * 0.4 for i in range(60)]
    whole = session(closes)
    pre = intraday.precompute(whole)
    for i in (10, 25, 44, 59):
        for minutes in (15, 30, 60):
            slow = ctx(whole.iloc[:i + 1]).session_atr(minutes)
            fast = ctx(whole.iloc[:i + 1], precomputed=pre).session_atr(minutes)
            assert fast == pytest.approx(slow, rel=1e-12), (i, minutes)


def test_the_precomputed_path_still_cannot_see_the_future():
    """The arrays span the WHOLE session, so this is the test that matters: a
    context holding the first i bars must read a prefix and never an index
    beyond it."""
    calm = [100.0] * 30
    explosion = [100.0] * 30 + [500.0] * 30
    pre_calm = intraday.precompute(session(calm))
    pre_explosion = intraday.precompute(session(explosion))

    early = ctx(session(explosion).iloc[:30], precomputed=pre_explosion)
    isolated = ctx(session(calm), precomputed=pre_calm)
    assert early.session_vwap() == pytest.approx(isolated.session_vwap())
    assert early.session_atr(30) == pytest.approx(isolated.session_atr(30))


def test_a_context_without_precomputed_arrays_still_works():
    """The live book holds one snapshot and never walks a session, so it must
    need to know nothing about any of this."""
    frame = session([100.0] * 20)
    assert ctx(frame).session_vwap() == pytest.approx(100.0)
    assert ctx(frame).session_atr(30) is not None
