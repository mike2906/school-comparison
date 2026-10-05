"""
Tests for Bulgarian education system utilities.

These tests are CRITICAL - the age group calculation is the core of the application.
"""
from app.utils.education import (
    AGE_GROUP_KEYS,
    calculate_age_group,
    calculate_gymnasium_score,
    grade_to_points,
)


class TestCalculateAgeGroup:
    """Test age group calculation with 100% coverage."""

    def test_nursery_age_0(self):
        """Child born in enrollment year is nursery (0 years old)."""
        assert calculate_age_group(2025, 2025) == 'nursery'

    def test_nursery_age_1(self):
        """Child born 1 year before enrollment is nursery (1 year old)."""
        assert calculate_age_group(2025, 2024) == 'nursery'

    def test_nursery_age_2(self):
        """Child born 2 years before enrollment is nursery (2 years old)."""
        assert calculate_age_group(2025, 2023) == 'nursery'

    def test_first_group_age_3(self):
        """Child born 3 years before enrollment is first group (3 years old)."""
        assert calculate_age_group(2025, 2022) == 'first'

    def test_first_group_jan_vs_dec(self):
        """
        CRITICAL: Children born Jan 1 and Dec 31 of same year enter same group.
        This is the Bulgarian system - uses calendar year, not birth date.
        """
        # Both children born in 2022, enrolling in 2025
        # Difference is 3 years for both
        # Both should be in 'first' group
        jan_1_child = calculate_age_group(2025, 2022)
        dec_31_child = calculate_age_group(2025, 2022)
        assert jan_1_child == dec_31_child == 'first'

    def test_second_group_age_4(self):
        """Child born 4 years before enrollment is second group."""
        assert calculate_age_group(2025, 2021) == 'second'

    def test_third_group_age_5(self):
        """Child born 5 years before enrollment is third group."""
        assert calculate_age_group(2025, 2020) == 'third'

    def test_preschool_age_6(self):
        """Child born 6 years before enrollment is preschool (preparatory)."""
        assert calculate_age_group(2025, 2019) == 'preschool'

    def test_preschool_example_from_agents_md(self):
        """Example from AGENTS.md: enrollment 2027, birth 2021 = preschool."""
        assert calculate_age_group(2027, 2021) == 'preschool'

    def test_grade_1_4_age_7(self):
        """Child born 7 years before enrollment is grade 1-4."""
        assert calculate_age_group(2025, 2018) == 'grade_1_4'

    def test_grade_1_4_age_8(self):
        """Child born 8 years before enrollment is grade 1-4."""
        assert calculate_age_group(2025, 2017) == 'grade_1_4'

    def test_grade_1_4_age_9(self):
        """Child born 9 years before enrollment is grade 1-4."""
        assert calculate_age_group(2025, 2016) == 'grade_1_4'

    def test_grade_1_4_age_10(self):
        """Child born 10 years before enrollment is grade 1-4."""
        assert calculate_age_group(2025, 2015) == 'grade_1_4'

    def test_grade_5_7_age_11(self):
        """Child born 11 years before enrollment is grade 5-7."""
        assert calculate_age_group(2025, 2014) == 'grade_5_7'

    def test_grade_5_7_age_12(self):
        """Child born 12 years before enrollment is grade 5-7."""
        assert calculate_age_group(2025, 2013) == 'grade_5_7'

    def test_grade_5_7_age_13(self):
        """Child born 13 years before enrollment is grade 5-7."""
        assert calculate_age_group(2025, 2012) == 'grade_5_7'

    def test_grade_8_12_age_14(self):
        """Child born 14 years before enrollment is grade 8-12."""
        assert calculate_age_group(2025, 2011) == 'grade_8_12'

    def test_grade_8_12_age_15(self):
        """Child born 15 years before enrollment is grade 8-12."""
        assert calculate_age_group(2025, 2010) == 'grade_8_12'

    def test_grade_8_12_age_18(self):
        """Child born 18 years before enrollment is grade 8-12 (final year)."""
        assert calculate_age_group(2025, 2007) == 'grade_8_12'

    def test_invalid_too_old(self):
        """Child born 19+ years before enrollment is invalid (too old)."""
        assert calculate_age_group(2025, 2006) is None

    def test_negative_diff(self):
        """Birth year after enrollment year returns None (invalid)."""
        # diff = 2025 - 2026 = -1, which is < 0 (not yet born for that year)
        assert calculate_age_group(2025, 2026) is None

    def test_far_future(self):
        """Child born many years in the future returns None (invalid)."""
        # diff = 2025 - 2030 = -5, which is < 0
        assert calculate_age_group(2025, 2030) is None

    def test_realistic_scenario_2024(self):
        """Realistic scenario: Child born in 2024, enrolling in 2027."""
        # diff = 2027 - 2024 = 3
        assert calculate_age_group(2027, 2024) == 'first'

    def test_realistic_scenario_2018(self):
        """Realistic scenario: Child born in 2018, enrolling in 2025."""
        # diff = 2025 - 2018 = 7
        assert calculate_age_group(2025, 2018) == 'grade_1_4'


class TestGradeToPoints:
    """Test grade to points conversion table."""

    def test_grade_6_0(self):
        """Grade 6.0 (Excellent) converts to 50 points."""
        assert grade_to_points(6.0) == 50

    def test_grade_5_5(self):
        """Grade 5.5 (Very Good+) converts to 39 points."""
        assert grade_to_points(5.5) == 39

    def test_grade_5_0(self):
        """Grade 5.0 (Very Good) converts to 26 points."""
        assert grade_to_points(5.0) == 26

    def test_grade_4_5(self):
        """Grade 4.5 (Good+) converts to 18 points."""
        assert grade_to_points(4.5) == 18

    def test_grade_4_0(self):
        """Grade 4.0 (Good) converts to 14 points."""
        assert grade_to_points(4.0) == 14

    def test_grade_3_5(self):
        """Grade 3.5 (Satisfactory+) converts to 10 points."""
        assert grade_to_points(3.5) == 10

    def test_grade_3_0(self):
        """Grade 3.0 (Satisfactory) converts to 7 points."""
        assert grade_to_points(3.0) == 7

    def test_grade_2_5(self):
        """Grade 2.5 (Poor+) converts to 4 points."""
        assert grade_to_points(2.5) == 4

    def test_grade_2_0(self):
        """Grade 2.0 (Poor) converts to 2 points."""
        assert grade_to_points(2.0) == 2

    def test_grade_below_2_0(self):
        """Grade below 2.0 converts to 0 points."""
        assert grade_to_points(1.9) == 0
        assert grade_to_points(1.0) == 0
        assert grade_to_points(0.0) == 0

    def test_grade_between_thresholds(self):
        """Grade between thresholds rounds down."""
        assert grade_to_points(5.75) == 39  # Between 5.5 and 6.0, rounds down to 5.5
        assert grade_to_points(4.25) == 14  # Between 4.0 and 4.5, rounds down to 4.0
        assert grade_to_points(3.25) == 7   # Between 3.0 and 3.5, rounds down to 3.0

    def test_grade_max(self):
        """Maximum grade (6.0) gives maximum points."""
        assert grade_to_points(6.0) == 50

    def test_grade_slightly_above_6(self):
        """Grade above 6.0 (shouldn't happen, but test) gives 50 points."""
        assert grade_to_points(6.1) == 50


class TestCalculateGymnasiumScore:
    """Test gymnasium admission score calculation."""

    def test_perfect_scores(self):
        """Perfect NVO scores and grades give maximum score."""
        score = calculate_gymnasium_score(100, 100, 6.0, 6.0)
        assert score == 300.0  # 100 + 100 + 50 + 50

    def test_example_from_docstring(self):
        """Example from docstring."""
        score = calculate_gymnasium_score(90, 85, 6.0, 5.5)
        assert score == 264.0  # 90 + 85 + 50 + 39

    def test_average_performance(self):
        """Average NVO and grades."""
        score = calculate_gymnasium_score(70, 65, 4.5, 4.0)
        assert score == 167.0  # 70 + 65 + 18 + 14

    def test_minimum_passing(self):
        """Minimum passing scores."""
        score = calculate_gymnasium_score(30, 30, 3.0, 3.0)
        assert score == 74.0  # 30 + 30 + 7 + 7

    def test_all_zeros(self):
        """All zero scores (failing)."""
        score = calculate_gymnasium_score(0, 0, 0, 0)
        assert score == 0.0

    def test_mixed_scores(self):
        """Mixed high and low scores."""
        score = calculate_gymnasium_score(95, 50, 6.0, 3.0)
        assert score == 202.0  # 95 + 50 + 50 + 7

    def test_decimal_nvo_scores(self):
        """NVO scores can have decimals."""
        score = calculate_gymnasium_score(87.5, 92.3, 5.5, 5.0)
        assert score == 244.8  # 87.5 + 92.3 + 39 + 26


class TestAgeGroupKeys:
    """Test AGE_GROUP_KEYS constant."""

    def test_all_groups_present(self):
        """All 8 age groups are in the list."""
        assert len(AGE_GROUP_KEYS) == 8

    def test_correct_order(self):
        """Age groups are in chronological order."""
        expected = [
            'nursery',
            'first',
            'second',
            'third',
            'preschool',
            'grade_1_4',
            'grade_5_7',
            'grade_8_12',
        ]
        assert AGE_GROUP_KEYS == expected

    def test_all_groups_are_strings(self):
        """All age group keys are strings."""
        assert all(isinstance(key, str) for key in AGE_GROUP_KEYS)

    def test_no_duplicates(self):
        """No duplicate age groups."""
        assert len(AGE_GROUP_KEYS) == len(set(AGE_GROUP_KEYS))


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_boundary_nursery_to_first(self):
        """Nursery covers differences 0-2 and first starts at 3."""
        assert calculate_age_group(2025, 2024) == 'nursery'
        assert calculate_age_group(2025, 2023) == 'nursery'
        assert calculate_age_group(2025, 2022) == 'first'

    def test_boundary_preschool_to_grade_1_4(self):
        """Boundary between preschool and grade 1-4."""
        assert calculate_age_group(2025, 2019) == 'preschool'  # diff=6
        assert calculate_age_group(2025, 2018) == 'grade_1_4'  # diff=7

    def test_boundary_grade_1_4_to_5_7(self):
        """Boundary between grade 1-4 and grade 5-7."""
        assert calculate_age_group(2025, 2015) == 'grade_1_4'  # diff=10
        assert calculate_age_group(2025, 2014) == 'grade_5_7'  # diff=11

    def test_boundary_grade_5_7_to_8_12(self):
        """Boundary between grade 5-7 and grade 8-12."""
        assert calculate_age_group(2025, 2012) == 'grade_5_7'  # diff=13
        assert calculate_age_group(2025, 2011) == 'grade_8_12'  # diff=14

    def test_boundary_grade_8_12_to_invalid(self):
        """Boundary between grade 8-12 and invalid (too old)."""
        assert calculate_age_group(2025, 2007) == 'grade_8_12'  # diff=18
        assert calculate_age_group(2025, 2006) is None  # diff=19

    def test_very_large_target_year(self):
        """Very large target year (far future)."""
        assert calculate_age_group(3000, 2994) == 'preschool'

    def test_very_old_birth_year(self):
        """Very old birth year."""
        assert calculate_age_group(2025, 1990) is None

    def test_same_year(self):
        """Target year same as birth year."""
        assert calculate_age_group(2025, 2025) == 'nursery'
