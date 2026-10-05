"""
Education system utilities.

These functions accept a country's education_config dict, making them
data-driven rather than hardcoded to any specific country.

The age group calculation uses Target Admission Year - Birth Year.
CRITICAL: Never use exact birth dates or "age on September 1st" logic.
A child born Dec 31, 2022 and one born Jan 1, 2022 both enter the same group in Sept 2025.
"""
from typing import Optional

# Default Bulgaria config for backwards compatibility
_BG_AGE_GROUPS = [
    {"key": "nursery", "min_diff": 0, "max_diff": 2},
    {"key": "first", "min_diff": 3, "max_diff": 3},
    {"key": "second", "min_diff": 4, "max_diff": 4},
    {"key": "third", "min_diff": 5, "max_diff": 5},
    {"key": "preschool", "min_diff": 6, "max_diff": 6},
    {"key": "grade_1_4", "min_diff": 7, "max_diff": 10},
    {"key": "grade_5_7", "min_diff": 11, "max_diff": 13},
    {"key": "grade_8_12", "min_diff": 14, "max_diff": 18},
]

_BG_GRADE_TO_POINTS = {
    "6.00": 50, "5.50": 39, "5.00": 26, "4.50": 18,
    "4.00": 14, "3.50": 10, "3.00": 7, "2.50": 4, "2.00": 2,
}

_BG_CONFIG = {
    "age_groups": _BG_AGE_GROUPS,
    "grade_to_points_table": _BG_GRADE_TO_POINTS,
}


def calculate_age_group(target_year: int, birth_year: int, config: Optional[dict] = None) -> Optional[str]:
    """
    Calculate age group based on target admission year and birth year.

    Formula: Age Group Index = Target Admission Year - Birth Year

    This is the correct way to calculate age groups (calendar year based).
    Do not use birth dates, months, or "age on September 1st" calculations.

    Args:
        target_year: The year the child will start school (enrollment year)
        birth_year: The child's birth year (entire calendar year)
        config: Education config dict with age_groups. Defaults to Bulgaria config.

    Returns:
        Age group code or None if invalid
    """
    if config is None:
        config = _BG_CONFIG

    diff = target_year - birth_year

    for ag in config.get("age_groups", _BG_AGE_GROUPS):
        if ag["min_diff"] <= diff <= ag["max_diff"]:
            return ag["key"]

    return None


def grade_to_points(grade: float, config: Optional[dict] = None) -> int:
    """
    Convert a grade to admission points using a conversion table.

    Args:
        grade: Grade value
        config: Education config dict with grade_to_points_table. Defaults to Bulgaria config.

    Returns:
        Points for admission calculation (0-50 for Bulgaria)
    """
    if config is None:
        config = _BG_CONFIG

    table = config.get("grade_to_points_table", _BG_GRADE_TO_POINTS)
    # Sort thresholds descending
    thresholds = sorted(
        [(float(k), v) for k, v in table.items()],
        key=lambda x: x[0],
        reverse=True,
    )
    for threshold, points in thresholds:
        if grade >= threshold:
            return points

    return 0


def calculate_gymnasium_score(
    nvo_bulgarian: float,
    nvo_math: float,
    qualifying_grade_1: float,
    qualifying_grade_2: float,
    config: Optional[dict] = None,
) -> float:
    """
    Calculate gymnasium admission score.

    Formula: NVO Bulgarian + NVO Math + Qualifying Subject 1 + Qualifying Subject 2

    Args:
        nvo_bulgarian: NVO Bulgarian score (0-100)
        nvo_math: NVO Math score (0-100)
        qualifying_grade_1: First qualifying subject grade (2-6 scale)
        qualifying_grade_2: Second qualifying subject grade (2-6 scale)
        config: Education config dict. Defaults to Bulgaria config.

    Returns:
        Total admission score
    """
    return (
        nvo_bulgarian
        + nvo_math
        + grade_to_points(qualifying_grade_1, config)
        + grade_to_points(qualifying_grade_2, config)
    )


# Age group keys for iteration (default Bulgaria)
AGE_GROUP_KEYS = [ag["key"] for ag in _BG_AGE_GROUPS]
