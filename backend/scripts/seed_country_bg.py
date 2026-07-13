"""Seed the Bulgaria country configuration."""
import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import select
from app.database import async_session_maker
from app.models.country import Country

BULGARIA_CONFIG = Country(
    code="bg",
    name_i18n={"bg": "България", "en": "Bulgaria"},
    education_config={
        "age_groups": [
            {"key": "nursery", "label_i18n": {"bg": "Ясла", "en": "Nursery"}, "min_diff": 0, "max_diff": 2, "category": "kindergarten"},
            {"key": "first", "label_i18n": {"bg": "Първа група", "en": "First group"}, "min_diff": 3, "max_diff": 3, "category": "kindergarten"},
            {"key": "second", "label_i18n": {"bg": "Втора група", "en": "Second group"}, "min_diff": 4, "max_diff": 4, "category": "kindergarten"},
            {"key": "third", "label_i18n": {"bg": "Трета група", "en": "Third group"}, "min_diff": 5, "max_diff": 5, "category": "kindergarten"},
            {"key": "preschool", "label_i18n": {"bg": "Предучилищна група", "en": "Preschool"}, "min_diff": 6, "max_diff": 6, "category": "kindergarten"},
            {"key": "grade_1_4", "label_i18n": {"bg": "1-4 клас", "en": "Grades 1-4"}, "min_diff": 7, "max_diff": 10, "category": "school"},
            {"key": "grade_5_7", "label_i18n": {"bg": "5-7 клас", "en": "Grades 5-7"}, "min_diff": 11, "max_diff": 13, "category": "school"},
            {"key": "grade_8_12", "label_i18n": {"bg": "8-12 клас", "en": "Grades 8-12"}, "min_diff": 14, "max_diff": 18, "category": "school"},
        ],
        "education_levels": [
            {"key": "nursery", "label_i18n": {"bg": "Детска ясла", "en": "Nursery"}},
            {"key": "kindergarten", "label_i18n": {"bg": "Детска градина", "en": "Kindergarten"}},
            {"key": "primary", "label_i18n": {"bg": "Начално училище", "en": "Primary school"}},
            {"key": "lower_secondary", "label_i18n": {"bg": "Прогимназия", "en": "Lower secondary"}},
            {"key": "upper_secondary", "label_i18n": {"bg": "Гимназия", "en": "Upper secondary"}},
        ],
        "school_types": [
            {"key": "state", "label_i18n": {"bg": "Държавно", "en": "State"}},
            {"key": "private", "label_i18n": {"bg": "Частно", "en": "Private"}},
            {"key": "international", "label_i18n": {"bg": "Международно", "en": "International"}},
        ],
        "shifts": [
            {"key": "morning", "label_i18n": {"bg": "Сутрешна смяна", "en": "Morning shift"}},
            {"key": "afternoon", "label_i18n": {"bg": "Следобедна смяна", "en": "Afternoon shift"}},
            {"key": "full_day", "label_i18n": {"bg": "Целодневна", "en": "Full day"}},
        ],
        "exam_types": [
            {"key": "nvo_4", "label_i18n": {"bg": "НВО 4 клас", "en": "NVO Grade 4"}},
            {"key": "nvo_7", "label_i18n": {"bg": "НВО 7 клас", "en": "NVO Grade 7"}},
            {"key": "nvo_10", "label_i18n": {"bg": "НВО 10 клас", "en": "NVO Grade 10"}},
        ],
        "exam_subjects": [
            {"key": "bulgarian", "label_i18n": {"bg": "Български език и литература", "en": "Bulgarian language"}},
            {"key": "math", "label_i18n": {"bg": "Математика", "en": "Mathematics"}},
        ],
        "admission_systems": {
            "kindergarten_state": "points",
            "gymnasium": "nvo_score",
            "private": "interview_test",
        },
        "grade_scale": {"min": 2, "max": 6},
        "grade_to_points_table": {
            "6.00": 50, "5.50": 39, "5.00": 26, "4.50": 18,
            "4.00": 14, "3.50": 10, "3.00": 7, "2.50": 4, "2.00": 2,
        },
        "age_calculation_method": "enrollment_year_minus_birth_year",
    },
    map_config={
        "center": [42.6977, 23.3219],
        "bounds": [[42.50, 23.10], [42.86, 23.60]],
        "default_zoom": 12,
        "geocoding": {
            "country_codes": "bg",
            "city_suffix": ", София, България",
        },
    },
    supported_languages=["bg", "en"],
    default_language="bg",
    default_currency="BGN",
)


async def seed():
    async with async_session_maker() as session:
        # Check if already exists
        result = await session.execute(select(Country).where(Country.code == "bg"))
        existing = result.scalar_one_or_none()

        if existing:
            # Update existing
            existing.name_i18n = BULGARIA_CONFIG.name_i18n
            existing.education_config = BULGARIA_CONFIG.education_config
            existing.map_config = BULGARIA_CONFIG.map_config
            existing.supported_languages = BULGARIA_CONFIG.supported_languages
            existing.default_language = BULGARIA_CONFIG.default_language
            existing.default_currency = BULGARIA_CONFIG.default_currency
            print("Updated existing Bulgaria country config.")
        else:
            session.add(BULGARIA_CONFIG)
            print("Inserted Bulgaria country config.")

        await session.commit()


if __name__ == "__main__":
    asyncio.run(seed())
