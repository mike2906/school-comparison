"""Country configuration service with data-driven education helpers."""
from typing import Optional


def calculate_age_group(config: dict, target_year: int, birth_year: int) -> Optional[str]:
    """
    Calculate age group based on target admission year and birth year,
    using the country's education_config.

    Args:
        config: education_config dict from Country model
        target_year: The year the child will start school (enrollment year)
        birth_year: The child's birth year (entire calendar year)

    Returns:
        Age group key or None if no match
    """
    diff = target_year - birth_year

    for ag in config.get("age_groups", []):
        if ag["min_diff"] <= diff <= ag["max_diff"]:
            return ag["key"]

    return None


def grade_to_points(config: dict, grade: float) -> int:
    """
    Convert a grade to admission points using the country's grade_to_points_table.

    Args:
        config: education_config dict from Country model
        grade: Grade value

    Returns:
        Points for admission calculation
    """
    table = config.get("grade_to_points_table", {})
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
    config: dict,
    nvo_scores: dict[str, float],
    qualifying_grades: list[float],
) -> float:
    """
    Calculate gymnasium admission score using config-driven formula.

    Args:
        config: education_config dict from Country model
        nvo_scores: Dict of {subject_key: score} e.g. {"bulgarian": 90, "math": 85}
        qualifying_grades: List of qualifying subject grades

    Returns:
        Total admission score
    """
    total = sum(nvo_scores.values())
    for grade in qualifying_grades:
        total += grade_to_points(config, grade)
    return total


def get_valid_keys(config: dict, field: str) -> list[str]:
    """Get valid keys for a config field (age_groups, education_levels, etc.)."""
    return [item["key"] for item in config.get(field, [])]


def is_valid_value(config: dict, field: str, value: str) -> bool:
    """Check if a value is valid for a given config field."""
    return value in get_valid_keys(config, field)
