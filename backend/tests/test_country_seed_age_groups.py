"""The Bulgaria age groups that the frontend uses to answer "what year are you enrolling?".

Age group = enrollment year - birth year (never the exact birth date). Every difference from
0 to 18 must land in exactly one group; P2.1 was a gap at 2 that showed 2-year-olds nothing.
"""

from scripts.seed_country_bg import BULGARIA_CONFIG

AGE_GROUPS = BULGARIA_CONFIG.education_config["age_groups"]


def _groups_for(diff: int) -> list[str]:
    return [group["key"] for group in AGE_GROUPS if group["min_diff"] <= diff <= group["max_diff"]]


def test_every_year_difference_from_0_to_18_has_exactly_one_group():
    for diff in range(0, 19):
        assert len(_groups_for(diff)) == 1, (diff, _groups_for(diff))
    assert _groups_for(19) == []


def test_kindergarten_and_school_boundaries():
    expected = {
        0: "nursery", 2: "nursery", 3: "first", 4: "second", 5: "third", 6: "preschool",
        7: "grade_1_4", 10: "grade_1_4", 11: "grade_5_7", 13: "grade_5_7", 14: "grade_8_12", 18: "grade_8_12",
    }
    for diff, key in expected.items():
        assert _groups_for(diff) == [key], diff
