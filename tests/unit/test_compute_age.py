# tests/unit/test_compute_age.py
from datetime import date

from javdb.spider.services.actor_age import compute_age


def test_basic_age():
    assert compute_age("1990-05-20", date(2026, 6, 4)) == 36


def test_birthday_not_yet_reached_at_reference():
    assert compute_age("1990-12-31", date(2026, 6, 4)) == 35


def test_birthday_on_reference_day():
    assert compute_age("2000-06-04", date(2026, 6, 4)) == 26


def test_reference_before_birthday_this_year():
    # reference = a movie release date earlier in the year than the birthday
    assert compute_age("2000-06-04", date(2026, 6, 1)) == 25


def test_unparseable_returns_none():
    assert compute_age("not-a-date", date(2026, 6, 4)) is None
    assert compute_age("", date(2026, 6, 4)) is None


def test_reference_before_birth_returns_none():
    assert compute_age("2030-01-01", date(2026, 6, 4)) is None
