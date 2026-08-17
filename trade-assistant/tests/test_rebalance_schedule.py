"""When a rebalance is due, under each configured schedule."""
from datetime import datetime, timezone

from assistant.paper.session import _rebalance_due, _rebalance_window


class FakeBook:
    def __init__(self, last_rebalance=None, last_rebalance_at=None):
        self.last_rebalance = last_rebalance
        self.last_rebalance_at = last_rebalance_at


def cfg(schedule, split_hour=12):
    return {"paper": {"rebalance_every": schedule,
                      "half_day_split_hour_utc": split_hour}}


def utc(y, m, d, hh=0, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=timezone.utc)


def at(moment):
    return moment.isoformat(timespec="seconds")


# -- a brand-new book ------------------------------------------------------

def test_a_book_that_has_never_rebalanced_is_always_due():
    for schedule in ("monthly", "weekly", "daily", "half_day"):
        assert _rebalance_due(FakeBook(), "2026-08-17", cfg(schedule)) is True


# -- half_day --------------------------------------------------------------

def test_second_half_of_the_day_is_due_after_a_morning_rebalance():
    book = FakeBook(last_rebalance_at=at(utc(2026, 8, 17, 9)))
    assert _rebalance_due(book, "2026-08-17", cfg("half_day"),
                          now=utc(2026, 8, 17, 14)) is True


def test_same_half_of_the_day_is_not_due_again():
    book = FakeBook(last_rebalance_at=at(utc(2026, 8, 17, 14)))
    assert _rebalance_due(book, "2026-08-17", cfg("half_day"),
                          now=utc(2026, 8, 17, 18)) is False


def test_morning_is_due_again_the_next_day():
    book = FakeBook(last_rebalance_at=at(utc(2026, 8, 17, 18)))
    assert _rebalance_due(book, "2026-08-18", cfg("half_day"),
                          now=utc(2026, 8, 18, 9)) is True


def test_the_split_hour_is_configurable():
    book = FakeBook(last_rebalance_at=at(utc(2026, 8, 17, 15)))
    # With a 17:00 split, 15:00 and 16:00 are the same window.
    assert _rebalance_due(book, "2026-08-17", cfg("half_day", split_hour=17),
                          now=utc(2026, 8, 17, 16)) is False
    assert _rebalance_due(book, "2026-08-17", cfg("half_day", split_hour=17),
                          now=utc(2026, 8, 17, 18)) is True


# -- the other schedules ---------------------------------------------------

def test_daily_is_due_once_per_calendar_day():
    book = FakeBook(last_rebalance_at=at(utc(2026, 8, 17, 9)))
    assert _rebalance_due(book, "2026-08-17", cfg("daily"),
                          now=utc(2026, 8, 17, 20)) is False
    assert _rebalance_due(book, "2026-08-18", cfg("daily"),
                          now=utc(2026, 8, 18, 9)) is True


def test_weekly_turns_on_the_iso_week_not_seven_days():
    book = FakeBook(last_rebalance_at=at(utc(2026, 8, 17, 9)))   # Monday
    assert _rebalance_due(book, "2026-08-21", cfg("weekly"),
                          now=utc(2026, 8, 21, 9)) is False      # same week
    assert _rebalance_due(book, "2026-08-24", cfg("weekly"),
                          now=utc(2026, 8, 24, 9)) is True       # next Monday


def test_monthly_turns_on_the_month_boundary():
    book = FakeBook(last_rebalance_at=at(utc(2026, 8, 17, 9)))
    assert _rebalance_due(book, "2026-08-31", cfg("monthly"),
                          now=utc(2026, 8, 31, 9)) is False
    assert _rebalance_due(book, "2026-09-01", cfg("monthly"),
                          now=utc(2026, 9, 1, 9)) is True


def test_an_unknown_schedule_falls_back_to_monthly():
    """A typo must not silently turn a monthly book into a daily one."""
    book = FakeBook(last_rebalance_at=at(utc(2026, 8, 17, 9)))
    assert _rebalance_due(book, "2026-08-18", cfg("hourly"),
                          now=utc(2026, 8, 18, 9)) is False


# -- books written before this setting existed -----------------------------

def test_a_legacy_book_with_only_a_date_still_answers_monthly():
    """`last_rebalance_at` did not exist before 2026-08-17. A book carrying
    only the old date field must not read as 'never rebalanced'."""
    book = FakeBook(last_rebalance="2026-08-17")
    assert _rebalance_due(book, "2026-08-20", cfg("monthly"),
                          now=utc(2026, 8, 20, 9)) is False
    assert _rebalance_due(book, "2026-09-01", cfg("monthly"),
                          now=utc(2026, 9, 1, 9)) is True


def test_a_legacy_book_is_due_in_the_afternoon_under_half_day():
    """A bare date cannot say which half of that day it belonged to. Treated as
    the first window, so an upgrade allows one extra rebalance rather than
    suppressing one."""
    book = FakeBook(last_rebalance="2026-08-17")
    assert _rebalance_due(book, "2026-08-17", cfg("half_day"),
                          now=utc(2026, 8, 17, 18)) is True


def test_a_corrupt_timestamp_does_not_crash_the_session():
    book = FakeBook(last_rebalance_at="not-a-timestamp")
    assert _rebalance_due(book, "2026-08-17", cfg("half_day"),
                          now=utc(2026, 8, 17, 18)) in (True, False)


# -- window labels ---------------------------------------------------------

def test_the_session_date_drives_the_calendar_not_the_wall_clock():
    """`today` is the caller's session date. Reading the date from the clock
    instead made every schedule ignore it, so a session replaying an old date
    was judged against whatever window the machine was in when it ran."""
    book = FakeBook(last_rebalance_at=at(utc(2026, 1, 5, 9)))
    # No `now` passed, so only the time of day may come from the clock.
    assert _rebalance_due(book, "2026-01-06", cfg("monthly")) is False
    assert _rebalance_due(book, "2026-02-02", cfg("monthly")) is True


def test_window_labels_are_distinct_across_the_split():
    morning = _rebalance_window("half_day", utc(2026, 8, 17, 9), 12)
    afternoon = _rebalance_window("half_day", utc(2026, 8, 17, 13), 12)
    assert morning != afternoon
    assert morning.endswith("-A") and afternoon.endswith("-B")
