"""Canonical academic-year normalisation and the September rollover."""

from datetime import date

import pytest

from app.utils.academic_year import (
    CURRENT,
    DATED_OTHER,
    NOT_STATED,
    academic_year_is_resolvable,
    academic_year_status,
    current_academic_year,
    normalize_academic_year,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        # Every spelling observed in the live pricing data collapses to one form.
        ("2026/2027", "2026/2027"),
        ("2026-2027", "2026/2027"),
        ("2025-2026", "2025/2026"),
        ("2025/2026", "2025/2026"),
        ("2025 / 2026", "2025/2026"),
        # Dashes schools actually use, and the abbreviated second half.
        ("2026 – 2027", "2026/2027"),
        ("2026 — 2027", "2026/2027"),
        ("2025/26", "2025/2026"),
        # Embedded in the school's own sentence.
        ("Такси за обучение за учебната 2026 - 2027 г.", "2026/2027"),
    ],
)
def test_normalize_accepts_observed_variants(raw, expected):
    assert normalize_academic_year(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "   ",
        "garbage",
        # A multi-year development strategy looks like a year range but is not one.
        # Treating it as an academic year would silently mislabel a school's prices.
        "Стратегия за развитие 2022-2027",
        "2022-2027",
        "2020/2026",
        # Date fragments that a looser pattern would happily match.
        "2016/67",
        "2026/02",
        # A backwards range is not an academic year either.
        "2027-2026",
    ],
)
def test_normalize_rejects_non_consecutive_and_junk(raw):
    assert normalize_academic_year(raw) is None


def test_resolvable_allows_absent_year_but_not_junk():
    assert academic_year_is_resolvable(None) is True
    assert academic_year_is_resolvable("") is True
    assert academic_year_is_resolvable("2026/2027") is True
    assert academic_year_is_resolvable("2022-2027") is False
    assert academic_year_is_resolvable("garbage") is False


@pytest.mark.parametrize(
    "today,expected",
    [
        # Rollover is 1 September.
        (date(2026, 8, 31), "2025/2026"),
        (date(2026, 9, 1), "2026/2027"),
        (date(2026, 9, 17), "2026/2027"),
        (date(2026, 12, 31), "2026/2027"),
        (date(2027, 1, 1), "2026/2027"),
        (date(2027, 8, 31), "2026/2027"),
        (date(2027, 9, 1), "2027/2028"),
    ],
)
def test_current_academic_year_rolls_over_in_september(today, expected):
    assert current_academic_year(today) == expected


def test_status_across_the_rollover_boundary():
    # The same stored value flips from current to historical as the year turns.
    assert academic_year_status("2025/2026", date(2026, 8, 31)) == CURRENT
    assert academic_year_status("2025/2026", date(2026, 9, 1)) == DATED_OTHER
    assert academic_year_status("2026-2027", date(2026, 9, 1)) == CURRENT
    assert academic_year_status("2026-2027", date(2026, 8, 31)) == DATED_OTHER


def test_status_handles_absent_and_unparseable_years():
    assert academic_year_status(None) == NOT_STATED
    assert academic_year_status("  ") == NOT_STATED
    # An unparseable year must never be presented as current.
    assert academic_year_status("2022-2027", date(2026, 9, 17)) == DATED_OTHER
