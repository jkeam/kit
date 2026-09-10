"""Tests for the cron matching logic in gateway/scheduler.py."""

from datetime import datetime

from gateway.scheduler import cron_matches


def test_all_wildcards_matches_any_time():
    assert cron_matches("* * * * *", datetime(2026, 1, 1, 9, 30))


def test_exact_minute_and_hour_must_match():
    assert cron_matches("0 9 * * *", datetime(2026, 1, 1, 9, 0))
    assert not cron_matches("0 9 * * *", datetime(2026, 1, 1, 9, 1))
    assert not cron_matches("0 9 * * *", datetime(2026, 1, 1, 10, 0))


def test_step_values():
    assert cron_matches("*/15 * * * *", datetime(2026, 1, 1, 0, 30))
    assert not cron_matches("*/15 * * * *", datetime(2026, 1, 1, 0, 20))


def test_comma_separated_list():
    assert cron_matches("0,30 * * * *", datetime(2026, 1, 1, 0, 30))
    assert not cron_matches("0,30 * * * *", datetime(2026, 1, 1, 0, 15))


def test_wrong_field_count_never_matches():
    assert not cron_matches("* * * *", datetime(2026, 1, 1))
    assert not cron_matches("* * * * * *", datetime(2026, 1, 1))


def test_non_numeric_field_never_matches():
    assert not cron_matches("bogus * * * *", datetime(2026, 1, 1, 0, 0))
