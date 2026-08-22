"""The three states of an intraday session, and why they are not wall times.

An intraday book has to stop opening positions before it stops holding them.
Being wrong about either boundary is how an intraday strategy silently becomes
an overnight one, which is the single risk the whole flat-by-close rule exists
to remove.

The times asked for were "no positions at 20:30, flat by 20:55, local British".
These tests assert exactly those times — and then assert that they MOVE when
the clocks do, because a hard-coded London time points at the wrong part of the
American session for two weeks each spring and autumn.
"""
from datetime import datetime, timezone

import pytest

from assistant.core import market_clock as clock


def london(year, month, day, hour, minute, *, utc_offset):
    """A London wall-clock moment, expressed in UTC."""
    return datetime(year, month, day, hour - utc_offset, minute, tzinfo=timezone.utc)


# 2026-08-21 is a Friday in British Summer Time: London is UTC+1, New York is
# UTC-4, so the 16:00 US close lands at 21:00 in London.
SUMMER = dict(utc_offset=1)


@pytest.mark.parametrize("hour,minute,phase", [
    (14, 35, clock.PHASE_OPEN),              # just after the US open
    (20, 29, clock.PHASE_OPEN),
    (20, 30, clock.PHASE_NO_NEW_ENTRIES),    # the boundary itself is closed
    (20, 54, clock.PHASE_NO_NEW_ENTRIES),
    (20, 55, clock.PHASE_LIQUIDATE),         # the boundary itself is closed
    (20, 59, clock.PHASE_LIQUIDATE),
    (21, 1, clock.PHASE_CLOSED),
    (13, 0, clock.PHASE_CLOSED),             # before the US open
])
def test_the_requested_london_times_are_what_the_rule_produces(hour, minute, phase):
    now = london(2026, 8, 21, hour, minute, **SUMMER)
    assert clock.session_phase("US", now,
                               entry_cutoff_minutes=30, flat_minutes=5) == phase


def test_the_boundaries_move_with_the_clocks_rather_than_staying_at_2030():
    """November: London is on GMT and New York on EST, so the US close is
    21:00 London again — but only because BOTH shifted. The rule has to be
    stated against the close, not against a London wall time, or it lands in
    the wrong part of the session during the weeks the two shifts disagree.

    2026-11-02 is the Monday inside that gap: the US has already fallen back,
    Britain fell back on 2026-10-25, so the US close is at 21:00 London. The
    week BEFORE — 2026-10-26 — Britain is on GMT and New York still on EDT, so
    the close is at 20:00 London and a hard-coded 20:30 cutoff would sit half
    an hour AFTER the market had already shut.
    """
    # 2026-10-26 is a Monday. London GMT (UTC+0), New York still EDT (UTC-4),
    # so the 16:00 ET close is 20:00 London.
    late_october = london(2026, 10, 26, 19, 45, utc_offset=0)
    assert clock.session_phase("US", late_october,
                               entry_cutoff_minutes=30, flat_minutes=5) \
        == clock.PHASE_NO_NEW_ENTRIES
    # The naive rule would still have been opening positions here.
    assert clock.minutes_to_close("US", late_october) == pytest.approx(15.0)


def test_london_gets_its_own_close_not_new_yorks():
    """The LSE shuts at 16:30 London. An intraday book holding UK names against
    the American calendar would carry them for four and a half hours after
    there was anything to trade against."""
    now = london(2026, 8, 21, 16, 10, **SUMMER)
    assert clock.session_phase("LSE", now,
                               entry_cutoff_minutes=30, flat_minutes=5) \
        == clock.PHASE_NO_NEW_ENTRIES
    assert clock.session_phase("US", now,
                               entry_cutoff_minutes=30, flat_minutes=5) \
        == clock.PHASE_OPEN


def test_a_weekend_is_closed_however_the_windows_are_set():
    saturday = london(2026, 8, 22, 20, 40, **SUMMER)
    assert clock.session_phase("US", saturday) == clock.PHASE_CLOSED
    assert clock.minutes_to_close("US", saturday) is None


def test_a_holiday_is_closed():
    thanksgiving = london(2026, 11, 26, 19, 0, utc_offset=0)
    assert clock.session_phase("US", thanksgiving) == clock.PHASE_CLOSED


def test_windows_that_would_close_positions_while_still_opening_them_are_refused():
    """A flat deadline further from the close than the entry cutoff means the
    book liquidates and then opens again in the same session — an intraday
    engine that ends the day holding something. Refused rather than sorted
    into a working order, because it can only be a mistake."""
    with pytest.raises(ValueError, match="further from the close"):
        clock.session_phase("US", london(2026, 8, 21, 19, 0, **SUMMER),
                            entry_cutoff_minutes=5, flat_minutes=30)


def test_minutes_to_close_is_what_a_strategy_sizes_its_horizon_against():
    now = london(2026, 8, 21, 19, 0, **SUMMER)
    assert clock.minutes_to_close("US", now) == pytest.approx(120.0)


def test_the_close_moment_is_the_venues_own_bell():
    day = london(2026, 8, 21, 15, 0, **SUMMER)
    assert clock.close_moment("US", now=day).isoformat() == "2026-08-21T20:00:00+00:00"
    assert clock.close_moment("LSE", now=day).isoformat() == "2026-08-21T15:30:00+00:00"
    assert clock.open_moment("US", now=day).isoformat() == "2026-08-21T13:30:00+00:00"
