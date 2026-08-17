"""The distinction the dashboard got wrong: old is not the same as stale."""
from datetime import datetime, timezone

from assistant.core import market_clock as mc


def utc(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=timezone.utc)


# -- open / closed ---------------------------------------------------------

def test_us_open_during_session():
    # Friday 2026-08-14, 14:00 UTC = 10:00 ET, mid-session.
    assert mc.is_open("US", utc(2026, 8, 14, 14)) is True


def test_us_closed_before_the_bell():
    # Monday 2026-08-17, 04:23 UTC = 00:23 ET. This is the exact instant the
    # dashboard was showing Friday's close and being read as broken.
    assert mc.is_open("US", utc(2026, 8, 17, 4, 23)) is False


def test_us_closed_at_weekend():
    assert mc.is_open("US", utc(2026, 8, 15, 16)) is False


def test_holiday_is_not_a_trading_day():
    # US Independence Day observed 2026-07-03, a Friday.
    assert mc.is_open("US", utc(2026, 7, 3, 15)) is False


# -- last completed session ------------------------------------------------

def test_last_session_before_todays_close_is_yesterday():
    """The core rule. Today's bar does not exist until today's bell has rung."""
    assert mc.last_session_date("US", utc(2026, 8, 17, 4, 23)) == "2026-08-14"


def test_last_session_after_close_is_today():
    assert mc.last_session_date("US", utc(2026, 8, 17, 21)) == "2026-08-17"


def test_last_session_skips_the_weekend():
    assert mc.last_session_date("US", utc(2026, 8, 15, 12)) == "2026-08-14"


# -- staleness -------------------------------------------------------------

def test_fridays_close_read_on_monday_morning_is_current():
    """The regression this whole module exists to prevent.

    A Friday close read before Monday's open is the freshest price that exists.
    Reporting it as stale is what made a working feed look broken.
    """
    status = mc.bar_status("AAPL", "2026-08-14", utc(2026, 8, 17, 4, 23))
    assert status["current"] is True
    assert status["sessions_behind"] == 0
    assert status["market_open"] is False


def test_genuinely_missed_sessions_are_counted_in_sessions_not_days():
    status = mc.bar_status("AAPL", "2026-08-11", utc(2026, 8, 17, 4, 23))
    assert status["current"] is False
    # 08-12, 08-13, 08-14 traded; the weekend did not.
    assert status["sessions_behind"] == 3


def test_weekend_does_not_make_a_price_stale():
    status = mc.bar_status("AAPL", "2026-08-14", utc(2026, 8, 16, 12))
    assert status["sessions_behind"] == 0
    assert status["current"] is True


def test_missing_price_reports_no_price():
    assert mc.bar_status("AAPL", None)["label"] == "no price"


# -- venues ----------------------------------------------------------------

def test_london_listing_uses_the_london_calendar():
    assert mc.venue_for("AZN.L") == "LSE"
    assert mc.venue_for("AAPL") == "US"


def test_london_opens_before_new_york():
    # 2026-08-17 08:00 UTC: London trading, New York not yet.
    when = utc(2026, 8, 17, 8)
    assert mc.is_open("LSE", when) is True
    assert mc.is_open("US", when) is False


def test_next_open_is_none_while_open():
    assert mc.next_open("US", utc(2026, 8, 14, 14)) is None


def test_next_open_skips_to_monday_from_saturday():
    when = mc.next_open("US", utc(2026, 8, 15, 12))
    assert when.strftime("%Y-%m-%d") == "2026-08-17"


def test_summary_covers_every_venue():
    out = mc.summary(utc(2026, 8, 17, 4, 23))
    assert set(out) == set(mc.VENUES)
    assert out["US"]["last_session"] == "2026-08-14"
