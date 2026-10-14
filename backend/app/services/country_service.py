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


def get_valid_keys(config: dict, field: str) -> list[str]:
    """Get valid keys for a config field (age_groups, education_levels, etc.)."""
    return [item["key"] for item in config.get(field, [])]


def is_valid_value(config: dict, field: str, value: str) -> bool:
    """Check if a value is valid for a given config field."""
    return value in get_valid_keys(config, field)
