"""
Seed script for Sofia School Comparison App - EXPANDED VERSION
Run with: DATABASE_URL=<local sofia_schools_demo URL> uv run python -m scripts.seed_data --reset-demo-data

Creates 100 realistic schools in Sofia with comprehensive coverage:
- Multiple schools per type (state/private/international)
- Every age group represented multiple times
- Enough private schools at each level for comparison
- Real Sofia addresses and coordinates across different neighborhoods
- Pricing data, NVO results, admission thresholds
"""

import argparse
import asyncio
import re

from sqlalchemy import delete
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.models.country import Country
from app.models.exam_results import ExamResult
from app.models.field_source import FieldSource, SourceConfidence, SourceType
from app.models.pricing import PriceCategory, PricePeriod, PriceSource, Pricing
from app.models.school import School, SchoolLocation, SchoolLocationAgeGroupShift

# Bulgarian to Latin transliteration mapping
BULGARIAN_TO_LATIN = {
    'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ж': 'zh',
    'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm', 'н': 'n',
    'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u', 'ф': 'f',
    'х': 'h', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'sht', 'ъ': 'a',
    'ь': 'y', 'ю': 'yu', 'я': 'ya',
    'А': 'A', 'Б': 'B', 'В': 'V', 'Г': 'G', 'Д': 'D', 'Е': 'E', 'Ж': 'Zh',
    'З': 'Z', 'И': 'I', 'Й': 'Y', 'К': 'K', 'Л': 'L', 'М': 'M', 'Н': 'N',
    'О': 'O', 'П': 'P', 'Р': 'R', 'С': 'S', 'Т': 'T', 'У': 'U', 'Ф': 'F',
    'Х': 'H', 'Ц': 'Ts', 'Ч': 'Ch', 'Ш': 'Sh', 'Щ': 'Sht', 'Ъ': 'A',
    'Ь': 'Y', 'Ю': 'Yu', 'Я': 'Ya',
}

# Common Bulgarian abbreviations and their English equivalents
ABBREVIATIONS = {
    'ЧДГ': 'PKG',      # Chastna Detska Gradina -> Private Kindergarten
    'ЧУ': 'PS',        # Chastno Uchilishte -> Private School
    'ЧСУ': 'PSS',      # Chastno Sredno Uchilishte -> Private Secondary School
    'ДГ': 'KG',        # Detska Gradina -> Kindergarten
    'СУ': 'SU',        # Sredno Uchilishte -> Secondary School
    'ОУ': 'PS',        # Osnovno Uchilishte -> Primary School
    'НЕГ': 'FLG',      # Natsionalna Ezikova Gimnazia -> Foreign Language Gymnasium
    'ПМГ': 'SMG',      # Prirodno-Matematicheska Gimnazia -> Science/Math Gymnasium
}


def transliterate_bulgarian(text: str) -> str:
    """Transliterate Bulgarian text to Latin characters."""
    if not text:
        return text

    # First handle abbreviations
    result = text
    for bg_abbr, en_abbr in ABBREVIATIONS.items():
        result = re.sub(r'\b' + re.escape(bg_abbr) + r'\b', en_abbr, result)

    # Then transliterate remaining Bulgarian characters
    transliterated = ''
    for char in result:
        if char in BULGARIAN_TO_LATIN:
            transliterated += BULGARIAN_TO_LATIN[char]
        else:
            transliterated += char

    return transliterated


def transliterate_address(address: str) -> str:
    """Transliterate Bulgarian address to Latin characters."""
    if not address:
        return address

    # Street abbreviations
    street_abbr = {
        'ул.': 'ul.',
        'бул.': 'bul.',
        'кв.': 'kv.',
    }

    result = address
    for bg_abbr, en_abbr in street_abbr.items():
        result = result.replace(bg_abbr, en_abbr)

    # Transliterate neighborhood names and street names
    return transliterate_bulgarian(result)


def make_i18n(bg_value: str, en_value: str | None = None, fallback=None) -> dict:
    if en_value is None:
        en_value = fallback(bg_value) if fallback else bg_value
    return {"bg": bg_value, "en": en_value}

def make_summary_i18n(bg_short: str, en_short: str | None = None, bg_long: str | None = None, en_long: str | None = None) -> dict:
    if bg_long is None:
        bg_long = bg_short
    if en_short is None:
        en_short = bg_short
    if en_long is None:
        en_long = en_short
    return {
        "bg": {"short": bg_short, "long": bg_long},
        "en": {"short": en_short, "long": en_long},
    }

def legacy_school(**kwargs) -> School:
    name = kwargs.pop("name", None)
    name_en = kwargs.pop("name_en", None)
    summary_bg = kwargs.pop("summary_bg", None)
    summary_en = kwargs.pop("summary_en", None)

    if "country_code" not in kwargs:
        kwargs["country_code"] = "bg"

    if "name_i18n" not in kwargs and name:
        kwargs["name_i18n"] = make_i18n(name, name_en, fallback=transliterate_bulgarian)
    if "summary_i18n" not in kwargs and summary_bg:
        kwargs["summary_i18n"] = make_summary_i18n(summary_bg, summary_en)

    return School(**kwargs)


def legacy_location(**kwargs) -> SchoolLocation:
    address = kwargs.pop("address", None)
    address_en = kwargs.pop("address_en", None)
    age_groups = kwargs.pop("age_groups", None)
    age_group = kwargs.pop("age_group", None)
    shift = kwargs.pop("shift", None)
    has_organised_groups = kwargs.pop("has_organised_groups", None)
    location_tags = kwargs.pop("location_tags", None)

    if "address_i18n" not in kwargs and address:
        kwargs["address_i18n"] = make_i18n(address, address_en, fallback=transliterate_address)

    if location_tags is not None:
        kwargs["location_tags"] = location_tags
    location = SchoolLocation(**kwargs)
    groups = []
    if age_groups:
        groups.extend(age_groups)
    if age_group:
        groups.append(age_group)
    if groups:
        unique_groups = sorted({group for group in groups if group})
        location.age_group_shifts = [
            SchoolLocationAgeGroupShift(
                age_group=group,
                shift=shift,
                has_organised_groups=has_organised_groups,
            )
            for group in unique_groups
        ]
    return location


def merge_locations(locations: list[SchoolLocation]) -> list[SchoolLocation]:
    merged: dict[tuple, SchoolLocation] = {}
    for location in locations:
        address_bg = (location.address_i18n or {}).get("bg")
        address_en = (location.address_i18n or {}).get("en")
        key = (
            address_bg,
            address_en,
            location.lat,
            location.lng,
        )
        if key not in merged:
            merged[key] = location
            continue

        current = merged[key]
        current.is_primary = current.is_primary or location.is_primary
        if not current.phone and location.phone:
            current.phone = location.phone

        existing_by_group = {link.age_group: link for link in current.age_group_shifts}
        for link in location.age_group_shifts:
            if link.age_group not in existing_by_group:
                current.age_group_shifts.append(
                    SchoolLocationAgeGroupShift(
                        age_group=link.age_group,
                        shift=link.shift,
                        has_organised_groups=link.has_organised_groups,
                    )
                )
                existing_by_group[link.age_group] = current.age_group_shifts[-1]
                continue
            existing = existing_by_group[link.age_group]
            if existing.shift is None and link.shift is not None:
                existing.shift = link.shift
            if existing.has_organised_groups is None and link.has_organised_groups is not None:
                existing.has_organised_groups = link.has_organised_groups

        if location.location_tags:
            current_tags = set(current.location_tags or [])
            for tag in location.location_tags:
                current_tags.add(tag)
            current.location_tags = sorted(current_tags)

    return list(merged.values())


def set_locations(school: School, locations: list[SchoolLocation]) -> None:
    school.locations = merge_locations(locations)


def clamp_score(value: float, min_value: float = 50.0, max_value: float = 100.0) -> float:
    return max(min_value, min(max_value, value))


def build_nvo_results(
    base_bg: float,
    base_math: float,
    exam_type: str,
    year_offsets: list[tuple[int, float, float]],
) -> list[ExamResult]:
    results: list[ExamResult] = []
    for year, delta_bg, delta_math in year_offsets:
        results.append(
            ExamResult(
                year=year,
                exam_type=exam_type,
                subject="Bulgarian Language",
                metric="average_score",
                value=clamp_score(base_bg + delta_bg),
                source_url="https://nvo.mon.bg",
            )
        )
        results.append(
            ExamResult(
                year=year,
                exam_type=exam_type,
                subject="Mathematics",
                metric="average_score",
                value=clamp_score(base_math + delta_math),
                source_url="https://nvo.mon.bg",
            )
        )
    return results


def map_price_source_to_type(source: PriceSource) -> SourceType:
    if source == PriceSource.OFFICIAL:
        return SourceType.OFFICIAL_WEBSITE
    if source == PriceSource.SCRAPED_WEBSITE:
        return SourceType.SCRAPED_WEBSITE
    if source == PriceSource.FORUM:
        return SourceType.COMMUNITY_FORUM
    if source == PriceSource.NOT_FOUND:
        return SourceType.UNKNOWN
    return SourceType.UNKNOWN


def map_source_type_to_confidence_score(source_type: SourceType) -> float:
    if source_type in {SourceType.OFFICIAL_WEBSITE, SourceType.GOVERNMENT, SourceType.SCHOOL_CONTACT}:
        return 0.95
    if source_type == SourceType.SCRAPED_WEBSITE:
        return 0.75
    if source_type in {SourceType.COMMUNITY_FORUM, SourceType.PARENT_SUBMITTED}:
        return 0.4
    return 0.3


def build_field_sources_for_school(school: School) -> list[FieldSource]:
    sources: list[FieldSource] = []

    # Pricing sources
    for idx, price in enumerate(school.pricing or []):
        category_value = price.category.value if hasattr(price.category, "value") else price.category
        period_value = price.period.value if hasattr(price.period, "value") else price.period
        source_value = price.source.value if hasattr(price.source, "value") else price.source
        source_type = map_price_source_to_type(price.source)
        amount_value = price.amount
        if amount_value is None and (getattr(price, "amount_min", None) is not None or getattr(price, "amount_max", None) is not None):
            if getattr(price, "amount_min", None) is not None and getattr(price, "amount_max", None) is not None:
                amount_value = f"{price.amount_min}-{price.amount_max}"
            else:
                amount_value = price.amount_min if getattr(price, "amount_min", None) is not None else price.amount_max
        sources.append(FieldSource(
            category="pricing",
            field_key=f"pricing.{category_value}",
            field_path=f"pricing[{idx}]",
            value_text=f"{amount_value} {price.currency} / {period_value}",
            source_type=source_type,
            source_name=source_value,
            source_url=price.source_url or school.website_url,
            display_url=price.source_url or school.website_url,
            scraped_at=price.scraped_at,
            last_verified=price.scraped_at,
            confidence=SourceConfidence.HIGH if price.source == PriceSource.OFFICIAL else SourceConfidence.MEDIUM,
            confidence_score=map_source_type_to_confidence_score(source_type),
        ))

    # NVO sources
    exam_results = school.exam_results or []
    if exam_results:
        seen = set()
        for result in exam_results:
            key = (result.exam_type, result.subject)
            if key in seen:
                continue
            seen.add(key)
            sources.append(FieldSource(
                category="academic",
                field_key="nvo_results",
                field_path=f"exam_results[{result.exam_type}:{result.subject}]",
                value_text=f"{result.subject} {result.exam_type}",
                source_type=SourceType.GOVERNMENT,
                source_name="mon",
                source_url=result.source_url,
                display_url=result.source_url,
                scraped_at=result.scraped_at,
                last_verified=result.scraped_at,
                confidence=SourceConfidence.HIGH,
                confidence_score=map_source_type_to_confidence_score(SourceType.GOVERNMENT),
            ))

    # Admission info sources (state kindergartens/gymnasiums)
    admission_info = school.admission_info or {}
    if admission_info.get("platform_url"):
        sources.append(FieldSource(
            category="admission",
            field_key="admission_info",
            field_path="admission_info",
            value_text="Admission thresholds",
            source_type=SourceType.GOVERNMENT,
            source_name="kg.sofia.bg",
            source_url=admission_info.get("platform_url"),
            display_url=admission_info.get("platform_url"),
            confidence=SourceConfidence.HIGH,
            confidence_score=map_source_type_to_confidence_score(SourceType.GOVERNMENT),
        ))

    return sources


NVO_FIVE_YEAR_DELTAS = [
    (2020, -2.8, -3.2),
    (2021, -1.4, -1.8),
    (2022, 0.0, -0.2),
    (2023, 1.1, 0.8),
    (2024, 2.0, 1.7),
]

NVO_THREE_YEAR_DELTAS = [
    (2022, -1.2, -1.5),
    (2023, 0.6, 0.4),
    (2024, 1.5, 1.1),
]


def validate_seed_target(database_url: str, *, reset_demo_data: bool) -> None:
    """Allow destructive seeding only on the explicitly named local demo database."""
    if not reset_demo_data:
        raise ValueError("Seeding replaces demo data; pass --reset-demo-data explicitly.")
    try:
        url = make_url(database_url)
    except ArgumentError:
        raise ValueError("Invalid DATABASE_URL; use the local sofia_schools_demo database.") from None
    if (
        url.drivername != "postgresql+asyncpg"
        or url.host not in {"localhost", "127.0.0.1", "::1"}
        or url.database != "sofia_schools_demo"
        or url.query
    ):
        raise ValueError(
            "Refusing to seed: DATABASE_URL must point to sofia_schools_demo on loopback, "
            "without connection query overrides. See README.md."
        )


async def seed_database(*, reset_demo_data: bool = False):
    """Create and populate test data with comprehensive coverage"""
    
    settings = get_settings()
    validate_seed_target(settings.DATABASE_URL, reset_demo_data=reset_demo_data)
    engine = create_async_engine(settings.DATABASE_URL)
    async_session = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    
    async with async_session() as session:

        # Clear existing data to avoid duplicate schools on reseed
        await session.execute(delete(ExamResult))
        await session.execute(delete(Pricing))
        await session.execute(delete(SchoolLocationAgeGroupShift))
        await session.execute(delete(SchoolLocation))
        await session.execute(delete(FieldSource))
        await session.execute(delete(School))
        await session.execute(delete(Country))
        await session.commit()

        country = Country(
            code="bg",
            name_i18n=make_i18n("България", "Bulgaria"),
            education_config={
                "education_levels": [
                    {"key": "nursery", "label_i18n": make_i18n("Ясла", "Nursery")},
                    {"key": "kindergarten", "label_i18n": make_i18n("Детска градина", "Kindergarten")},
                    {"key": "primary", "label_i18n": make_i18n("Начален етап", "Primary")},
                    {"key": "lower_secondary", "label_i18n": make_i18n("Прогимназия", "Lower secondary")},
                    {"key": "upper_secondary", "label_i18n": make_i18n("Гимназия", "Upper secondary")},
                ],
                "age_groups": [
                    {"key": "nursery", "label_i18n": make_i18n("Ясла", "Nursery"), "category": "kindergarten", "min_diff": 0, "max_diff": 2},
                    {"key": "first", "label_i18n": make_i18n("I група", "First group"), "category": "kindergarten", "min_diff": 3, "max_diff": 3},
                    {"key": "second", "label_i18n": make_i18n("II група", "Second group"), "category": "kindergarten", "min_diff": 4, "max_diff": 4},
                    {"key": "third", "label_i18n": make_i18n("III група", "Third group"), "category": "kindergarten", "min_diff": 5, "max_diff": 5},
                    {"key": "preschool", "label_i18n": make_i18n("Подготвителна", "Preschool"), "category": ["kindergarten", "school"], "min_diff": 6, "max_diff": 6},
                    {"key": "grade_1_4", "label_i18n": make_i18n("1-4 клас", "Grades 1-4"), "category": "school", "min_diff": 7, "max_diff": 10},
                    {"key": "grade_5_7", "label_i18n": make_i18n("5-7 клас", "Grades 5-7"), "category": "school", "min_diff": 11, "max_diff": 13},
                    {"key": "grade_8_12", "label_i18n": make_i18n("8-12 клас", "Grades 8-12"), "category": "school", "min_diff": 14, "max_diff": 18},
                ],
                "school_types": [
                    {"key": "state", "label_i18n": make_i18n("Държавно", "State")},
                    {"key": "private", "label_i18n": make_i18n("Частно", "Private")},
                    {"key": "international", "label_i18n": make_i18n("Международно", "International")},
                ],
                "shifts": [
                    {"key": "morning", "label_i18n": make_i18n("Сутрешна смяна", "Morning")},
                    {"key": "afternoon", "label_i18n": make_i18n("Следобедна смяна", "Afternoon")},
                    {"key": "full_day", "label_i18n": make_i18n("Целодневна", "Full day")},
                ],
            },
            map_config={
                "center": [42.6977, 23.3219],
                "zoom": 12,
            },
            supported_languages=["bg", "en"],
            default_language="bg",
            default_currency="BGN",
        )
        session.add(country)

        schools = []
        
        # ===== PRIVATE KINDERGARTENS (20 schools) =====
        
        # 1. Premium private kindergarten - Lozenets
        school = legacy_school(
            name="ЧДГ Златно зрънце",
            name_en=transliterate_bulgarian("ЧДГ Златно зрънце"),
            school_type="private",
            education_level="kindergarten",
            website_url="https://zlatnozrnce.com",
            summary_bg="Частна детска градина в кв. Лозенец с индивидуален подход и малки групи.",
            summary_en="Private kindergarten in Lozenets with individual approach and small groups.",
            num_pupils=45,
            attributes={
                "languages_of_instruction": ["bulgarian", "english"],
                "has_canteen": True,
                "activities_offered": ["english", "art", "music", "sport"],
                "language_focus": [
                    {"language": "english", "level": "immersion"},
                    {"language": "english", "level": "bilingual"},
                ],
                "special_programs": ["music_program", "extended_day", "meals_provided"],
                "facilities": ["cafeteria", "library", "sports_facilities"],
                "teaching_approach": ["montessori"]
            }
        )
        set_locations(school, [
            legacy_location(age_group="nursery", address="ул. Кричим 25, Лозенец, София",
                          address_en=transliterate_address("ул. Кричим 25, Лозенец, София"),
                          lat=42.6735, lng=23.3355, phone="+359 88 123 4567",
                          shift="full_day", has_organised_groups=False, is_primary=True),
            legacy_location(age_group="first", address="ул. Кричим 25, Лозенец, София",
                          address_en=transliterate_address("ул. Кричим 25, Лозенец, София"),
                          lat=42.6735, lng=23.3355, phone="+359 88 123 4567",
                          shift="full_day", has_organised_groups=False, is_primary=False),
            legacy_location(age_group="second", address="ул. Кричим 25, Лозенец, София",
                          address_en=transliterate_address("ул. Кричим 25, Лозенец, София"),
                          lat=42.6735, lng=23.3355, phone="+359 88 123 4567",
                          shift="full_day", has_organised_groups=False, is_primary=False),
        ])
        school.pricing = [
            Pricing(age_group="nursery", category=PriceCategory.TUITION, amount=900, period=PricePeriod.MONTHLY, source=PriceSource.OFFICIAL),
            Pricing(age_group="first", category=PriceCategory.TUITION, amount=800, period=PricePeriod.MONTHLY, source=PriceSource.OFFICIAL),
            Pricing(age_group="second", category=PriceCategory.TUITION, amount=750, period=PricePeriod.MONTHLY, source=PriceSource.OFFICIAL),
            Pricing(age_group=None, category=PriceCategory.FOOD, amount=120, period=PricePeriod.MONTHLY, source=PriceSource.OFFICIAL),
        ]
        schools.append(school)
        
        # 2. Mid-range private kindergarten - Center
        school = legacy_school(
            name="ЧДГ Детски свят",
            name_en=transliterate_bulgarian("ЧДГ Детски свят"),
            school_type="private",
            education_level="kindergarten",
            summary_bg="Частна детска градина в центъра с акцент върху английски език и спорт.",
            summary_en="Private kindergarten in the center with focus on English and sports.",
            num_pupils=60,
            attributes={
                "languages_of_instruction": ["bulgarian", "english"],
                "has_canteen": True,
                "language_focus": [
                    {"language": "english", "level": "immersion"},
                ],
                "special_programs": ["sports_program", "extended_day", "meals_provided"],
                "facilities": ["sports_facilities", "cafeteria"]
            }
        )
        set_locations(school, [
            legacy_location(age_group="first", address="ул. Цар Иван Асен II 42, Център, София",
                          address_en=transliterate_address("ул. Цар Иван Асен II 42, Център, София"),
                          lat=42.6977, lng=23.3219, shift="full_day", is_primary=True),
            legacy_location(age_group="second", address="ул. Цар Иван Асен II 42, Център, София",
                          address_en=transliterate_address("ул. Цар Иван Асен II 42, Център, София"),
                          lat=42.6977, lng=23.3219, shift="full_day", is_primary=False),
            legacy_location(age_group="third", address="ул. Цар Иван Асен II 42, Център, София",
                          address_en=transliterate_address("ул. Цар Иван Асен II 42, Център, София"),
                          lat=42.6977, lng=23.3219, shift="full_day", is_primary=False),
        ])
        school.pricing = [
            Pricing(age_group="first", category=PriceCategory.TUITION, amount=650, period=PricePeriod.MONTHLY, source=PriceSource.SCRAPED_WEBSITE),
            Pricing(age_group="second", category=PriceCategory.TUITION, amount=600, period=PricePeriod.MONTHLY, source=PriceSource.SCRAPED_WEBSITE),
            Pricing(age_group="third", category=PriceCategory.TUITION, amount=550, period=PricePeriod.MONTHLY, source=PriceSource.SCRAPED_WEBSITE),
        ]
        schools.append(school)
        
        # 3-20: More private kindergartens across Sofia neighborhoods
        private_kgs = [
            {"name": "ЧДГ Слънчев ден", "area": "Младост", "lat": 42.6450, "lng": 23.3750, "price": 550},
            {"name": "ЧДГ Щастливо детство", "area": "Люлин", "lat": 42.7150, "lng": 23.2450, "price": 480},
            {"name": "ЧДГ Приказен свят", "area": "Студентски град", "lat": 42.6520, "lng": 23.3580, "price": 720},
            {"name": "ЧДГ Малки таланти", "area": "Витоша", "lat": 42.6620, "lng": 23.3110, "price": 850},
            {"name": "ЧДГ Дъга", "area": "Красно село", "lat": 42.6805, "lng": 23.2890, "price": 520},
            {"name": "ЧДГ Пчелица", "area": "Надежда", "lat": 42.7280, "lng": 23.3150, "price": 490},
            {"name": "ЧДГ Малки изследователи", "area": "Манастирски ливади", "lat": 42.6685, "lng": 23.2805, "price": 780},
            {"name": "ЧДГ Немски свят", "area": "Гео Милев", "lat": 42.6825, "lng": 23.3615, "price": 620},
            {"name": "ЧДГ Дъбче", "area": "Банишора", "lat": 42.7075, "lng": 23.3205, "price": 510},
            {"name": "ЧДГ Светулка", "area": "Хиподрума", "lat": 42.6765, "lng": 23.2920, "price": 690},
            {"name": "ЧДГ Умни деца", "area": "Изгрев", "lat": 42.6730, "lng": 23.3570, "price": 740},
            {"name": "ЧДГ Слънце и море", "area": "Суха река", "lat": 42.7215, "lng": 23.3630, "price": 500},
            {"name": "ЧДГ Звездичка", "area": "Западен парк", "lat": 42.6940, "lng": 23.2635, "price": 530},
            {"name": "ЧДГ Планинче", "area": "Драгалевци", "lat": 42.6430, "lng": 23.3375, "price": 880},
            {"name": "ЧДГ Весели стъпки", "area": "Дървеница", "lat": 42.6645, "lng": 23.3510, "price": 610},
            {"name": "ЧДГ Приказка", "area": "Павлово", "lat": 42.6640, "lng": 23.2720, "price": 540},
            {"name": "ЧДГ Морско конче", "area": "Слатина", "lat": 42.6845, "lng": 23.3640, "price": 570},
            {"name": "ЧДГ Лагера", "area": "Лагера", "lat": 42.6815, "lng": 23.2810, "price": 560},
        ]
        
        for kg_data in private_kgs:
            school = legacy_school(
                name=kg_data["name"],
                name_en=transliterate_bulgarian(kg_data["name"]),
                school_type="private",
                education_level="kindergarten",
                summary_bg=f"Частна детска градина в кв. {kg_data['area']}.",
                summary_en=f"Private kindergarten in {kg_data['area']} neighborhood.",
                num_pupils=50,
                attributes={
                    "languages_of_instruction": ["bulgarian"],
                    "has_canteen": True,
                    "special_programs": ["extended_day", "meals_provided"],
                    "facilities": ["cafeteria"]
                }
            )
            if kg_data["name"] == "ЧДГ Малки таланти":
                school.attributes = {
                    "languages_of_instruction": ["bulgarian", "russian"],
                    "has_canteen": True,
                    "language_focus": [
                        {"language": "russian", "level": "enrichment"},
                    ],
                    "special_programs": ["music_program", "arts_program"],
                    "facilities": ["library", "cafeteria"],
                    "teaching_approach": ["project_based"]
                }
                set_locations(school, [
                    legacy_location(age_group="preschool", address="бул. България 105, Борово, София",
                                  address_en=transliterate_address("бул. България 105, Борово, София"),
                                  lat=42.6717, lng=23.2872,
                                  shift="full_day", is_primary=False),
                    legacy_location(age_group="first", address="ул. Сребърна 18, Лозенец, София",
                                  address_en=transliterate_address("ул. Сребърна 18, Лозенец, София"),
                                  lat=42.6549, lng=23.3237, shift="full_day", is_primary=True),
                    legacy_location(age_group="second", address="ул. Сребърна 18, Лозенец, София",
                                  address_en=transliterate_address("ул. Сребърна 18, Лозенец, София"),
                                  lat=42.6549, lng=23.3237, shift="full_day", is_primary=False),
                ])
            elif kg_data["name"] == "ЧДГ Малки изследователи":
                school.attributes = {
                    "languages_of_instruction": ["bulgarian", "english"],
                    "has_canteen": True,
                    "language_focus": [
                        {"language": "english", "level": "immersion"},
                    ],
                    "special_programs": ["extended_day", "meals_provided", "sports_program"],
                    "facilities": ["cafeteria", "transportation", "sports_facilities"],
                    "teaching_approach": ["montessori"],
                    "class_size": 14
                }
                school.admission_info = {"status": "accepting", "requirements": "interview"}
                set_locations(school, [
                    legacy_location(age_group="first", address="ул. Иван Багрянов 12, Манастирски ливади, София",
                                  address_en=transliterate_address("ул. Иван Багрянов 12, Манастирски ливади, София"),
                                  lat=42.6685, lng=23.2805, shift="full_day", is_primary=True),
                    legacy_location(age_group="second", address="ул. Иван Багрянов 12, Манастирски ливади, София",
                                  address_en=transliterate_address("ул. Иван Багрянов 12, Манастирски ливади, София"),
                                  lat=42.6685, lng=23.2805, shift="full_day", is_primary=False),
                    legacy_location(age_group="third", address="ул. Иван Багрянов 12, Манастирски ливади, София",
                                  address_en=transliterate_address("ул. Иван Багрянов 12, Манастирски ливади, София"),
                                  lat=42.6685, lng=23.2805, shift="full_day", is_primary=False),
                ])
            elif kg_data["name"] == "ЧДГ Немски свят":
                school.attributes = {
                    "languages_of_instruction": ["bulgarian", "german"],
                    "has_canteen": True,
                    "language_focus": [
                        {"language": "german", "level": "immersion"},
                    ],
                    "special_programs": ["arts_program", "meals_provided"],
                    "facilities": ["cafeteria", "library"],
                    "teaching_approach": ["waldorf"],
                    "class_size": 15
                }
                school.admission_info = {"status": "waitlist", "requirements": "interview"}
                set_locations(school, [
                    legacy_location(age_group="first", address="ул. Марагидик 6, Гео Милев, София",
                                  address_en=transliterate_address("ул. Марагидик 6, Гео Милев, София"),
                                  lat=42.6825, lng=23.3615, shift="full_day", is_primary=True),
                    legacy_location(age_group="second", address="ул. Марагидик 6, Гео Милев, София",
                                  address_en=transliterate_address("ул. Марагидик 6, Гео Милев, София"),
                                  lat=42.6825, lng=23.3615, shift="full_day", is_primary=False),
                ])
            else:
                set_locations(school, [
                    legacy_location(age_group="first", address=f"кв. {kg_data['area']}, София",
                                  address_en=transliterate_address(f"кв. {kg_data['area']}, София"),
                                  lat=kg_data["lat"], lng=kg_data["lng"], shift="full_day", is_primary=True),
                    legacy_location(age_group="second", address=f"кв. {kg_data['area']}, София",
                                  address_en=transliterate_address(f"кв. {kg_data['area']}, София"),
                                  lat=kg_data["lat"], lng=kg_data["lng"], shift="full_day", is_primary=False),
                    legacy_location(age_group="third", address=f"кв. {kg_data['area']}, София",
                                  address_en=transliterate_address(f"кв. {kg_data['area']}, София"),
                                  lat=kg_data["lat"], lng=kg_data["lng"], shift="full_day", is_primary=False),
                ])
            school.pricing = [
                Pricing(age_group="first", category=PriceCategory.TUITION, amount=kg_data["price"], 
                       period=PricePeriod.MONTHLY, source=PriceSource.SCRAPED_WEBSITE),
                Pricing(age_group="second", category=PriceCategory.TUITION, amount=kg_data["price"]-50, 
                       period=PricePeriod.MONTHLY, source=PriceSource.SCRAPED_WEBSITE),
            ]
            schools.append(school)
        
        # ===== STATE KINDERGARTENS (18 schools) =====
        
        state_kgs = [
            {"name": 'ДГ № 78 "Слънчев ден"', "area": "Красно село", "lat": 42.6805, "lng": 23.2890, "points": [8,6,4], "status": "accepting"},
            {"name": 'ДГ № 45 "Радост"', "area": "Лозенец", "lat": 42.6795, "lng": 23.3345, "points": [11,9,7], "status": "waitlist"},
            {"name": 'ДГ № 112 "Детелина"', "area": "Младост", "lat": 42.6420, "lng": 23.3720, "points": [7,5,3], "status": "accepting"},
            {"name": 'ДГ № 23 "Звънче"', "area": "Люлин", "lat": 42.7120, "lng": 23.2480, "points": [5,3,2], "status": "full"},
            {"name": 'ДГ № 67 "Пролет"', "area": "Студентски град", "lat": 42.6550, "lng": 23.3620, "points": [9,7,5], "status": "waitlist"},
            {"name": 'ДГ № 189 "Детски рай"', "area": "Надежда", "lat": 42.7250, "lng": 23.3180, "points": [6,4,2], "status": "accepting"},
            {"name": 'ДГ № 200 "Мечо Пух"', "area": "Овча купел", "lat": 42.6770, "lng": 23.2640, "points": [7,6,4], "status": "full"},
            {"name": 'ДГ № 155 "Вярна дружина"', "area": "Дружба 1", "lat": 42.6610, "lng": 23.4010, "points": [10,8,6], "status": "waitlist"},
            {"name": 'ДГ № 30 "Зорница"', "area": "Бели брези", "lat": 42.6680, "lng": 23.2950, "points": [9,7,5], "status": "accepting"},
            {"name": 'ДГ № 90 "Веселина"', "area": "Левски", "lat": 42.7205, "lng": 23.3730, "points": [6,5,3], "status": "waitlist"},
            {"name": 'ДГ № 10 "Камбанка"', "area": "Зона Б-5", "lat": 42.7020, "lng": 23.3110, "points": [8,6,4], "status": "accepting"},
            {"name": 'ДГ № 50 "Славейче"', "area": "Толстой", "lat": 42.7210, "lng": 23.3350, "points": [7,6,4], "status": "full"},
            {"name": 'ДГ № 27 "Люляк"', "area": "Дървеница", "lat": 42.6665, "lng": 23.3550, "points": [9,7,5], "status": "accepting"},
            {"name": 'ДГ № 41 "Чайка"', "area": "Иван Вазов", "lat": 42.6840, "lng": 23.3195, "points": [10,8,6], "status": "waitlist"},
            {"name": 'ДГ № 104 "Живко"', "area": "Орландовци", "lat": 42.7420, "lng": 23.3480, "points": [5,4,3], "status": "accepting"},
            {"name": 'ДГ № 3 "Сребърна звезда"', "area": "Княжево", "lat": 42.6540, "lng": 23.2485, "points": [6,5,3], "status": "waitlist"},
            {"name": 'ДГ № 53 "Детелина"', "area": "Връбница", "lat": 42.7460, "lng": 23.2860, "points": [5,4,2], "status": "full"},
            {"name": 'ДГ № 101 "Лютиче"', "area": "Хладилника", "lat": 42.6645, "lng": 23.3270, "points": [9,7,5], "status": "accepting"},
        ]
        
        for kg_data in state_kgs:
            school = legacy_school(
                name=kg_data["name"],
                name_en=transliterate_bulgarian(kg_data["name"]),
                school_type="state",
                education_level="kindergarten",
                summary_bg=f"Държавна детска градина в кв. {kg_data['area']}.",
                summary_en=f"State kindergarten in {kg_data['area']} neighborhood.",
                num_pupils=180,
                admission_info={
                    "system": "points",
                    "platform_url": "https://kg.sofia.bg",
                    "status": kg_data.get("status"),
                    "historical_thresholds": [
                        {
                            "year": 2024,
                            "age_group": "first",
                            "rounds": [
                                {"round": 1, "last_admitted_points": kg_data["points"][0], "admitted_count": 35},
                                {"round": 2, "last_admitted_points": kg_data["points"][1], "admitted_count": 12},
                                {"round": 3, "last_admitted_points": kg_data["points"][2], "admitted_count": 5}
                            ]
                        }
                    ]
                },
                attributes={"languages_of_instruction": ["bulgarian"], "has_canteen": True}
            )
            set_locations(school, [
                legacy_location(age_group="first", address=f"кв. {kg_data['area']}, София",
                              address_en=transliterate_address(f"кв. {kg_data['area']}, София"),
                              lat=kg_data["lat"], lng=kg_data["lng"], shift="full_day", is_primary=True),
                legacy_location(age_group="second", address=f"кв. {kg_data['area']}, София",
                              address_en=transliterate_address(f"кв. {kg_data['area']}, София"),
                              lat=kg_data["lat"], lng=kg_data["lng"], shift="full_day", is_primary=False),
            ])
            schools.append(school)
        
        # ===== PRIVATE PRIMARY SCHOOLS (14 schools) =====
        
        # British School of Sofia
        school = legacy_school(
            name="Британско училище в София",
            name_en="British School of Sofia",
            school_type="private",
            education_level="primary",
            website_url="https://www.britishschoolsofia.bg",
            summary_bg="Британското училище предлага образование по британската учебна програма.",
            summary_en="The British School offers British curriculum education.",
            num_pupils=350,
            attributes={
                "languages_of_instruction": ["english"],
                "has_canteen": True,
                "uniform_required": True,
                "language_focus": [
                    {"language": "english", "level": "immersion"},
                ],
            }
        )
        set_locations(school, [
            legacy_location(age_group="preschool", address="ул. Панайот Волов 27, Лозенец, София",
                          address_en=transliterate_address("ул. Панайот Волов 27, Лозенец, София"),
                          lat=42.6725, lng=23.3365, shift="full_day", is_primary=True),
            legacy_location(age_group="grade_1_4", address="ул. Чипровци 1, Лозенец, София",
                          address_en=transliterate_address("ул. Чипровци 1, Лозенец, София"),
                          lat=42.6701, lng=23.3388, shift="full_day", is_primary=False),
        ])
        school.pricing = [
            Pricing(
                age_group="preschool",
                category=PriceCategory.TUITION,
                amount=14000,
                period=PricePeriod.YEARLY,
                academic_year="2026-2027",
                plan_name="Plan 1",
                source=PriceSource.OFFICIAL,
            ),
            Pricing(
                age_group="preschool",
                category=PriceCategory.TUITION,
                amount=3600,
                period=PricePeriod.TERM,
                academic_year="2026-2027",
                plan_name="Term plan",
                source=PriceSource.OFFICIAL,
            ),
            Pricing(
                age_group="preschool",
                category=PriceCategory.TUITION,
                amount=7200,
                period=PricePeriod.SEMESTER,
                academic_year="2026-2027",
                plan_name="Semester plan",
                source=PriceSource.OFFICIAL,
            ),
            Pricing(
                age_group="grade_1_4",
                category=PriceCategory.TUITION,
                amount=None,
                amount_min=17500,
                amount_max=18500,
                period=PricePeriod.YEARLY,
                academic_year="2026-2027",
                plan_name="Plan 1",
                source=PriceSource.OFFICIAL,
            ),
        ]
        schools.append(school)

        # Private primary school with shifts at a single location
        school = legacy_school(
            name="ЧУ Свети Паисий",
            name_en=transliterate_bulgarian("ЧУ Свети Паисий"),
            school_type="private",
            education_level="primary",
            summary_bg="Частно училище с утринна и следобедна смяна.",
            summary_en="Private school with morning and afternoon shifts.",
            num_pupils=280,
            attributes={
                "languages_of_instruction": ["bulgarian", "english"],
                "has_canteen": True,
                "special_programs": ["extended_day", "meals_provided"],
                "facilities": ["cafeteria", "sports_facilities"],
            }
        )
        set_locations(school, [
            legacy_location(age_group="grade_1_4", address="ул. Ген. Гурко 18, Център, София",
                          address_en=transliterate_address("ул. Ген. Гурко 18, Център, София"),
                          lat=42.6942, lng=23.3275, shift="morning", has_organised_groups=True, is_primary=True),
            legacy_location(age_group="grade_5_7", address="ул. Ген. Гурко 18, Център, София",
                          address_en=transliterate_address("ул. Ген. Гурко 18, Център, София"),
                          lat=42.6942, lng=23.3275, shift="afternoon", has_organised_groups=False, is_primary=False),
        ])
        school.pricing = [
            Pricing(age_group="grade_1_4", category=PriceCategory.TUITION, amount=12000, period=PricePeriod.YEARLY, source=PriceSource.OFFICIAL),
            Pricing(age_group="grade_5_7", category=PriceCategory.TUITION, amount=13500, period=PricePeriod.YEARLY, source=PriceSource.OFFICIAL),
        ]
        schools.append(school)

        # Private primary school with three locations
        school = legacy_school(
            name="ЧУ Три кампуса",
            name_en=transliterate_bulgarian("ЧУ Три кампуса"),
            school_type="private",
            education_level="primary",
            summary_bg="Частно училище с три кампуса в различни райони.",
            summary_en="Private school with three campuses across Sofia.",
            num_pupils=420,
            attributes={
                "languages_of_instruction": ["english"],
                "has_canteen": True,
                "special_programs": ["meals_provided", "arts_program"],
                "facilities": ["library", "sports_facilities"],
            }
        )
        set_locations(school, [
            legacy_location(age_group="preschool", address="ул. Шипка 12, Център, София",
                          address_en=transliterate_address("ул. Шипка 12, Център, София"),
                          lat=42.6955, lng=23.3340, shift="full_day", is_primary=True),
            legacy_location(age_group="grade_1_4", address="бул. България 45, Борово, София",
                          address_en=transliterate_address("бул. България 45, Борово, София"),
                          lat=42.6768, lng=23.2878, shift="full_day", is_primary=False),
            legacy_location(age_group="grade_5_7", address="ул. Атанас Далчев 8, Изток, София",
                          address_en=transliterate_address("ул. Атанас Далчев 8, Изток, София"),
                          lat=42.6722, lng=23.3515, shift="full_day", is_primary=False),
        ])
        school.pricing = [
            Pricing(age_group="preschool", category=PriceCategory.TUITION, amount=11000, period=PricePeriod.YEARLY, source=PriceSource.OFFICIAL),
            Pricing(age_group="grade_1_4", category=PriceCategory.TUITION, amount=15000, period=PricePeriod.YEARLY, source=PriceSource.OFFICIAL),
            Pricing(age_group="grade_5_7", category=PriceCategory.TUITION, amount=16500, period=PricePeriod.YEARLY, source=PriceSource.OFFICIAL),
        ]
        schools.append(school)

        # Private school with campus specializations (science vs arts)
        school = legacy_school(
            name="ЧУ Наука и изкуства",
            name_en=transliterate_bulgarian("ЧУ Наука и изкуства"),
            school_type="private",
            education_level="primary",
            summary_bg="Частно училище с различни кампуси за науки и изкуства.",
            summary_en="Private school with specialized science and arts campuses.",
            num_pupils=360,
            attributes={
                "languages_of_instruction": ["bulgarian", "english"],
                "has_canteen": True,
                "special_programs": ["arts_program", "science_lab", "meals_provided"],
                "facilities": ["library", "sports_facilities", "laboratory"],
            }
        )
        set_locations(school, [
            legacy_location(
                age_groups=["grade_1_4", "grade_5_7"],
                address="бул. Цариградско шосе 125, Дружба, София",
                address_en=transliterate_address("бул. Цариградско шосе 125, Дружба, София"),
                lat=42.6572, lng=23.4042,
                shift="morning",
                has_organised_groups=True,
                location_tags=["science_focus"],
                is_primary=True,
            ),
            legacy_location(
                age_groups=["grade_1_4", "grade_5_7"],
                address="ул. Солунска 34, Център, София",
                address_en=transliterate_address("ул. Солунска 34, Център, София"),
                lat=42.6914, lng=23.3198,
                shift="afternoon",
                has_organised_groups=False,
                location_tags=["arts_focus"],
                is_primary=False,
            ),
        ])
        school.pricing = [
            Pricing(age_group="grade_1_4", category=PriceCategory.TUITION, amount=14000, period=PricePeriod.YEARLY, source=PriceSource.OFFICIAL),
            Pricing(age_group="grade_5_7", category=PriceCategory.TUITION, amount=15500, period=PricePeriod.YEARLY, source=PriceSource.OFFICIAL),
        ]
        schools.append(school)
        
        # International School of Sofia (treated as private with language focus)
        school = legacy_school(
            name="Международно училище София",
            name_en="International School of Sofia",
            school_type="private",
            education_level="primary",
            website_url="https://www.issofia.org",
            summary_bg="Международно училище с програма по Cambridge Primary и IB PYP.",
            summary_en="International school with Cambridge Primary and IB PYP programs.",
            num_pupils=280,
            admission_info={"status": "accepting", "requirements": "interview"},
            attributes={
                "languages_of_instruction": ["english"],
                "has_canteen": True,
                "language_focus": [
                    {"language": "english", "level": "immersion"},
                    {"language": "english", "level": "bilingual"},
                ],
                "special_programs": ["extended_day", "meals_provided"],
                "facilities": ["library", "sports_facilities"],
                "teaching_approach": ["ib_program"]
            }
        )
        set_locations(school, [
            legacy_location(age_group="grade_1_4", address="бул. Пенчо Славейков 73, Витоша, София",
                          address_en=transliterate_address("бул. Пенчо Славейков 73, Витоша, София"),
                          lat=42.6620, lng=23.3110, shift="full_day", is_primary=True),
        ])
        school.pricing = [
            Pricing(age_group="grade_1_4", category=PriceCategory.TUITION, amount=16500, period=PricePeriod.YEARLY, source=PriceSource.OFFICIAL),
        ]
        schools.append(school)

        # Two more private primary schools
        private_primary = [
            {"name": "Частно училище Младост", "area": "Младост", "lat": 42.6480, "lng": 23.3780, "price": 6500, "nvo": [77.2, 74.5], "nvo_years": 5},
            {"name": "ЧУ Св. София", "area": "Център", "lat": 42.6950, "lng": 23.3280, "price": 8500, "nvo": [81.4, 78.2], "nvo_years": 5},
            {"name": "ЧУ Академия за таланти", "area": "Изток", "lat": 42.6760, "lng": 23.3520, "price": 9800, "nvo": [83.1, 80.5], "nvo_years": 5},
            {"name": "ЧУ Светлина", "area": "Красна поляна", "lat": 42.6935, "lng": 23.2680, "price": 6200, "nvo": [74.2, 71.6], "nvo_years": 3},
            {"name": "ЧУ Добруджа", "area": "Левски", "lat": 42.7160, "lng": 23.3760, "price": 5900, "nvo": [73.5, 70.2], "nvo_years": 5},
            {"name": "ЧУ Свети Георги", "area": "Илинден", "lat": 42.7085, "lng": 23.2895, "price": 6400, "nvo": [76.8, 73.4], "nvo_years": 5},
            {"name": "ЧУ Златен мост", "area": "Лозенец", "lat": 42.6715, "lng": 23.3335, "price": 10200, "nvo": [84.0, 81.3], "nvo_years": 5},
            {"name": "ЧУ Радостина", "area": "Овча купел", "lat": 42.6760, "lng": 23.2590, "price": 5600, "nvo": [72.4, 69.5], "nvo_years": 3},
            {"name": "ЧУ Перла", "area": "Дружба 2", "lat": 42.6530, "lng": 23.3970, "price": 6000, "nvo": [75.1, 72.9], "nvo_years": 5},
            {"name": "ЧУ Климент", "area": "Слатина", "lat": 42.6860, "lng": 23.3635, "price": 7000, "nvo": [79.0, 76.4], "nvo_years": 5},
            {"name": "ЧУ Доверие", "area": "Витоша", "lat": 42.6625, "lng": 23.3095, "price": 9300, "nvo": [82.2, 79.5], "nvo_years": 5},
            {"name": "ЧУ Рила", "area": "Сердика", "lat": 42.7185, "lng": 23.3260, "price": 5800, "nvo": [71.8, 69.0], "nvo_years": 3},
        ]

        for school_data in private_primary:
            school = legacy_school(
                name=school_data["name"],
                name_en=transliterate_bulgarian(school_data["name"]),
                school_type="private",
                education_level="primary",
                summary_bg=f"Частно начално училище в кв. {school_data['area']}.",
                summary_en=f"Private primary school in {school_data['area']}.",
                num_pupils=120,
                attributes={
                    "languages_of_instruction": ["bulgarian"],
                    "has_canteen": True,
                    "language_focus": [
                        {"language": "spanish", "level": "enrichment"},
                    ] if school_data["name"] == "Частно училище Младост" else [
                        {"language": "german", "level": "bilingual"},
                    ],
                }
            )
            if school_data["name"] == "ЧУ Академия за таланти":
                school.attributes = {
                    "languages_of_instruction": ["bulgarian", "english"],
                    "has_canteen": True,
                    "language_focus": [{"language": "english", "level": "bilingual"}],
                    "special_programs": ["arts_program", "sports_program", "meals_provided"],
                    "facilities": ["sports_facilities", "library", "cafeteria"],
                    "teaching_approach": ["project_based"],
                    "class_size": 15
                }
                school.admission_info = {"status": "accepting", "requirements": "test"}
            elif school_data["name"] == "ЧУ Светлина":
                school.attributes = {
                    "languages_of_instruction": ["bulgarian"],
                    "has_canteen": True,
                    "language_focus": [{"language": "english", "level": "enrichment"}],
                    "special_programs": ["extended_day", "meals_provided"],
                    "facilities": ["cafeteria", "transportation"],
                    "class_size": 16
                }
                school.admission_info = {"status": "waitlist", "requirements": "interview"}
            elif school_data["name"] == "ЧУ Св. София":
                school.admission_info = {"status": "accepting", "requirements": "interview"}
            set_locations(school, [
                legacy_location(age_group="grade_1_4", address=f"кв. {school_data['area']}, София",
                              address_en=transliterate_address(f"кв. {school_data['area']}, София"),
                              lat=school_data["lat"], lng=school_data["lng"], shift="full_day", is_primary=True),
            ])
            school.pricing = [
                Pricing(age_group="grade_1_4", category=PriceCategory.TUITION,
                       amount=school_data["price"], period=PricePeriod.YEARLY, source=PriceSource.SCRAPED_WEBSITE),
            ]
            if school_data.get("nvo"):
                year_offsets = NVO_FIVE_YEAR_DELTAS if school_data.get("nvo_years", 5) == 5 else NVO_THREE_YEAR_DELTAS
                school.exam_results = build_nvo_results(
                    base_bg=school_data["nvo"][0],
                    base_math=school_data["nvo"][1],
                    exam_type="nvo_4",
                    year_offsets=year_offsets,
                )
            schools.append(school)

        # ===== INTERNATIONAL SCHOOLS (5 schools) =====

        school = legacy_school(
            name="Sofia International Academy",
            name_en="Sofia International Academy",
            school_type="international",
            education_level="primary",
            website_url="https://sia.example.com",
            summary_bg="Международно училище с английска програма и проектно обучение.",
            summary_en="International school with English curriculum and project-based learning.",
            num_pupils=240,
            admission_info={"status": "accepting", "requirements": "interview"},
            attributes={
                "languages_of_instruction": ["english"],
                "language_focus": [{"language": "english", "level": "immersion"}],
                "special_programs": ["extended_day", "meals_provided"],
                "facilities": ["library", "sports_facilities", "cafeteria"],
                "teaching_approach": ["project_based"]
            }
        )
        set_locations(school, [
            legacy_location(age_group="grade_1_4", address="ул. Николай Хайтов 12, Дианабад, София",
                          address_en=transliterate_address("ул. Николай Хайтов 12, Дианабад, София"),
                          lat=42.6758, lng=23.3535, shift="full_day", is_primary=True),
        ])
        school.pricing = [
            Pricing(age_group="grade_1_4", category=PriceCategory.TUITION, amount=19500,
                   period=PricePeriod.YEARLY, source=PriceSource.OFFICIAL),
        ]
        schools.append(school)

        school = legacy_school(
            name="Френско международно училище София",
            name_en="French International School Sofia",
            school_type="international",
            education_level="lower_secondary",
            website_url="https://french-school.example.com",
            summary_bg="Френско международно училище с фокус върху двуезично обучение.",
            summary_en="French international school with bilingual curriculum.",
            num_pupils=180,
            admission_info={"status": "waitlist", "requirements": "test"},
            attributes={
                "languages_of_instruction": ["french", "english"],
                "language_focus": [{"language": "french", "level": "bilingual"}],
                "special_programs": ["arts_program", "meals_provided"],
                "facilities": ["library", "cafeteria"],
                "class_size": 16
            }
        )
        set_locations(school, [
            legacy_location(age_group="grade_5_7", address="ул. Жолио-Кюри 20, Изток, София",
                          address_en=transliterate_address("ул. Жолио-Кюри 20, Изток, София"),
                          lat=42.6722, lng=23.3520, shift="full_day", is_primary=True),
        ])
        school.pricing = [
            Pricing(age_group="grade_5_7", category=PriceCategory.TUITION, amount=21000,
                   period=PricePeriod.YEARLY, source=PriceSource.OFFICIAL),
        ]
        schools.append(school)

        additional_internationals = [
            {
                "name": "German International School Sofia",
                "name_en": "German International School Sofia",
                "area": "Бояна",
                "lat": 42.6455,
                "lng": 23.2725,
                "price": 19500,
                "status": "accepting",
                "requirements": "interview",
                "language": "german",
                "level": "immersion",
                "education_level": "primary",
                "age_group": "grade_1_4",
            },
            {
                "name": "Italian International School Sofia",
                "name_en": "Italian International School Sofia",
                "area": "Лозенец",
                "lat": 42.6735,
                "lng": 23.3315,
                "price": 20500,
                "status": "waitlist",
                "requirements": "test",
                "language": "italian",
                "level": "bilingual",
                "education_level": "lower_secondary",
                "age_group": "grade_5_7",
            },
            {
                "name": "Nordic International School Sofia",
                "name_en": "Nordic International School Sofia",
                "area": "Симеоново",
                "lat": 42.6295,
                "lng": 23.3330,
                "price": 22500,
                "status": "accepting",
                "requirements": "interview",
                "language": "english",
                "level": "immersion",
                "education_level": "upper_secondary",
                "age_group": "grade_8_12",
            },
        ]

        for school_data in additional_internationals:
            school = legacy_school(
                name=school_data["name"],
                name_en=school_data["name_en"],
                school_type="international",
                education_level=school_data["education_level"],
                summary_bg=f"Международно училище в кв. {school_data['area']}.",
                summary_en=f"International school in {school_data['area']}.",
                num_pupils=220,
                admission_info={"status": school_data["status"], "requirements": school_data["requirements"]},
                attributes={
                    "languages_of_instruction": [school_data["language"], "english"] if school_data["language"] != "english" else ["english"],
                    "language_focus": [{"language": school_data["language"], "level": school_data["level"]}],
                    "special_programs": ["extended_day", "meals_provided"],
                    "facilities": ["library", "cafeteria"],
                }
            )
            set_locations(school, [
                legacy_location(age_group=school_data["age_group"], address=f"кв. {school_data['area']}, София",
                              address_en=transliterate_address(f"кв. {school_data['area']}, София"),
                              lat=school_data["lat"], lng=school_data["lng"], shift="full_day", is_primary=True),
            ])
            school.pricing = [
                Pricing(age_group=school_data["age_group"], category=PriceCategory.TUITION, amount=school_data["price"],
                       period=PricePeriod.YEARLY, source=PriceSource.OFFICIAL),
            ]
            schools.append(school)
        
        # ===== STATE PRIMARY/BASIC SCHOOLS (14 schools) =====
        
        state_schools = [
            {"name": '73 СУ "Владислав Граматик"', "area": "Дружба 2", "lat": 42.6525, "lng": 23.3980, "nvo": [78.5, 76.2]},
            {"name": '119 СОУ "Акад. Михаил Арнаудов"', "area": "Студентски град", "lat": 42.6580, "lng": 23.3650, "nvo": [75.2, 73.8]},
            {"name": '56 ОУ "Св. Климент Охридски"', "area": "Люлин", "lat": 42.7180, "lng": 23.2520, "nvo": [72.5, 70.3]},
            {"name": '134 СОУ "Димчо Дебелянов"', "area": "Младост", "lat": 42.6390, "lng": 23.3690, "nvo": [79.8, 77.5]},
            {"name": '125 СУ "Боян Пенев"', "area": "Лозенец", "lat": 42.6762, "lng": 23.3348, "nvo": [82.4, 79.1]},
            {"name": '102 ОУ "Панайот Волов"', "area": "Обеля", "lat": 42.7350, "lng": 23.2455, "nvo": [70.2, 68.4]},
            {"name": '44 ОУ "Св. Иван Рилски"', "area": "Красно село", "lat": 42.6810, "lng": 23.2925, "nvo": [74.6, 71.5]},
            {"name": '33 ОУ "Санкт Петербург"', "area": "Гевгелийски", "lat": 42.6980, "lng": 23.3055, "nvo": [73.2, 70.1]},
            {"name": '22 ОУ "П. Р. Славейков"', "area": "Симеоново", "lat": 42.6285, "lng": 23.3345, "nvo": [76.9, 74.0]},
            {"name": '69 СУ "Димитър Маринов"', "area": "Бъкстон", "lat": 42.6685, "lng": 23.2740, "nvo": [71.6, 69.2]},
            {"name": '50 ОУ "Васил Левски"', "area": "Надежда 3", "lat": 42.7325, "lng": 23.3085, "nvo": [72.8, 69.9]},
            {"name": '85 СУ "Отец Паисий"', "area": "Подуяне", "lat": 42.7070, "lng": 23.3505, "nvo": [77.4, 74.8]},
            {"name": '115 ОУ "Св. Климент"', "area": "Младост 4", "lat": 42.6405, "lng": 23.3950, "nvo": [78.1, 75.4]},
            {"name": '63 ОУ "Христо Ботев"', "area": "Иван Вазов", "lat": 42.6820, "lng": 23.3205, "nvo": [80.0, 77.2]},
        ]
        
        for school_data in state_schools:
            school = legacy_school(
                name=school_data["name"],
                name_en=transliterate_bulgarian(school_data["name"]),
                school_type="state",
                education_level="primary",
                summary_bg=f"Държавно училище в кв. {school_data['area']} с организирани групи.",
                summary_en=f"State school in {school_data['area']} with after-school care.",
                num_pupils=650,
                attributes={"languages_of_instruction": ["bulgarian"], "has_canteen": True}
            )
            if school_data["name"] == '22 ОУ "П. Р. Славейков"':
                set_locations(school, [
                    legacy_location(age_group="grade_1_4", address="ул. Симеоновско шосе 12, Симеоново, София",
                                  address_en=transliterate_address("ул. Симеоновско шосе 12, Симеоново, София"),
                                  lat=42.6285, lng=23.3345,
                                  shift="morning", has_organised_groups=True, is_primary=True),
                    legacy_location(age_group="grade_5_7", address="ул. Симеоновско шосе 12, Симеоново, София",
                                  address_en=transliterate_address("ул. Симеоновско шосе 12, Симеоново, София"),
                                  lat=42.6285, lng=23.3345,
                                  shift="afternoon", has_organised_groups=False, is_primary=False),
                    legacy_location(age_group="grade_1_4", address="бул. Черни връх 61, Симеоново, София",
                                  address_en=transliterate_address("бул. Черни връх 61, Симеоново, София"),
                                  lat=42.6225, lng=23.3290,
                                  shift="morning", has_organised_groups=True, is_primary=False),
                    legacy_location(age_group="grade_5_7", address="бул. Черни връх 61, Симеоново, София",
                                  address_en=transliterate_address("бул. Черни връх 61, Симеоново, София"),
                                  lat=42.6225, lng=23.3290,
                                  shift="afternoon", has_organised_groups=False, is_primary=False),
                ])
            else:
                set_locations(school, [
                    legacy_location(age_group="grade_1_4", address=f"кв. {school_data['area']}, София",
                                  address_en=transliterate_address(f"кв. {school_data['area']}, София"),
                                  lat=school_data["lat"], lng=school_data["lng"],
                                  shift="morning", has_organised_groups=True, is_primary=True),
                    legacy_location(age_group="grade_5_7", address=f"кв. {school_data['area']}, София",
                                  address_en=transliterate_address(f"кв. {school_data['area']}, София"),
                                  lat=school_data["lat"], lng=school_data["lng"],
                                  shift="afternoon", has_organised_groups=False, is_primary=False),
                ])
            school.exam_results = build_nvo_results(
                base_bg=school_data["nvo"][0],
                base_math=school_data["nvo"][1],
                exam_type="nvo_4",
                year_offsets=NVO_FIVE_YEAR_DELTAS,
            )
            schools.append(school)

        # ===== STATE LOWER SECONDARY SCHOOLS (6 schools) =====

        state_lower_secondary = [
            {"name": '60 ОУ "Св. Св. Кирил и Методий"', "area": "Надежда", "lat": 42.7310, "lng": 23.3200, "nvo": [74.5, 71.2]},
            {"name": '54 ОУ "Св. Иван Рилски"', "area": "Хаджи Димитър", "lat": 42.7055, "lng": 23.3505, "nvo": [76.8, 73.9]},
            {"name": '32 ОУ "Св. Климент"', "area": "Дървеница", "lat": 42.6675, "lng": 23.3535, "nvo": [75.6, 72.8]},
            {"name": '40 ОУ "Левски"', "area": "Лозенец", "lat": 42.6735, "lng": 23.3345, "nvo": [78.0, 75.2]},
            {"name": '52 ОУ "Цар Симеон"', "area": "Западен парк", "lat": 42.6930, "lng": 23.2620, "nvo": [73.4, 70.7]},
            {"name": '94 ОУ "П. Яворов"', "area": "Сухата река", "lat": 42.7235, "lng": 23.3660, "nvo": [74.9, 71.5]},
        ]

        for school_data in state_lower_secondary:
            school = legacy_school(
                name=school_data["name"],
                name_en=transliterate_bulgarian(school_data["name"]),
                school_type="state",
                education_level="lower_secondary",
                summary_bg=f"Държавно училище (5-7 клас) в кв. {school_data['area']}.",
                summary_en=f"State lower-secondary school in {school_data['area']}.",
                num_pupils=520,
                attributes={
                    "languages_of_instruction": ["bulgarian"],
                    "has_canteen": True,
                    "special_programs": ["extended_day", "meals_provided"],
                    "facilities": ["library", "sports_facilities"],
                }
            )
            set_locations(school, [
                legacy_location(age_group="grade_5_7", address=f"кв. {school_data['area']}, София",
                              address_en=transliterate_address(f"кв. {school_data['area']}, София"),
                              lat=school_data["lat"], lng=school_data["lng"],
                              shift="morning", has_organised_groups=False, is_primary=True),
            ])
            school.exam_results = build_nvo_results(
                base_bg=school_data["nvo"][0],
                base_math=school_data["nvo"][1],
                exam_type="nvo_7",
                year_offsets=NVO_FIVE_YEAR_DELTAS,
            )
            schools.append(school)

        # ===== PRIVATE LOWER SECONDARY SCHOOLS (6 schools) =====

        private_lower_secondary = [
            {"name": "ЧУ Нови хоризонти", "area": "Младост", "lat": 42.6465, "lng": 23.3775, "price": 7800,
             "status": "accepting", "requirements": "interview", "language": "english", "level": "bilingual",
             "nvo": [78.2, 75.6], "nvo_years": 3},
            {"name": "ЧУ Проф. Константин Фотинов", "area": "Борово", "lat": 42.6705, "lng": 23.2885, "price": 6900,
             "status": "waitlist", "requirements": "test", "language": "german", "level": "enrichment",
             "nvo": [76.0, 72.8], "nvo_years": 5},
            {"name": "ЧУ Логос", "area": "Гоце Делчев", "lat": 42.6695, "lng": 23.3010, "price": 7400,
             "status": "accepting", "requirements": "test", "language": "english", "level": "enrichment",
             "nvo": [79.4, 76.3], "nvo_years": 5},
            {"name": "ЧУ Орбита", "area": "Дианабад", "lat": 42.6768, "lng": 23.3515, "price": 8200,
             "status": "waitlist", "requirements": "interview", "language": "french", "level": "bilingual",
             "nvo": [80.1, 77.5], "nvo_years": 5},
            {"name": "ЧУ Мостове", "area": "Люлин", "lat": 42.7140, "lng": 23.2505, "price": 6600,
             "status": "accepting", "requirements": "interview", "language": "spanish", "level": "enrichment",
             "nvo": [74.8, 71.7], "nvo_years": 3},
            {"name": "ЧУ Платон", "area": "Сухата река", "lat": 42.7240, "lng": 23.3695, "price": 7000,
             "status": "accepting", "requirements": "test", "language": "english", "level": "bilingual",
             "nvo": [77.3, 74.0], "nvo_years": 5},
        ]

        for school_data in private_lower_secondary:
            school = legacy_school(
                name=school_data["name"],
                name_en=transliterate_bulgarian(school_data["name"]),
                school_type="private",
                education_level="lower_secondary",
                summary_bg=f"Частно училище за 5-7 клас в кв. {school_data['area']}.",
                summary_en=f"Private lower-secondary school in {school_data['area']}.",
                num_pupils=210,
                admission_info={"status": school_data["status"], "requirements": school_data["requirements"]},
                attributes={
                    "languages_of_instruction": ["bulgarian"],
                    "language_focus": [{"language": school_data["language"], "level": school_data["level"]}],
                    "special_programs": ["extended_day", "meals_provided"],
                    "facilities": ["library", "cafeteria", "transportation"],
                    "class_size": 14 if school_data["name"] == "ЧУ Нови хоризонти" else 16
                }
            )
            set_locations(school, [
                legacy_location(age_group="grade_5_7", address=f"кв. {school_data['area']}, София",
                              address_en=transliterate_address(f"кв. {school_data['area']}, София"),
                              lat=school_data["lat"], lng=school_data["lng"],
                              shift="full_day", is_primary=True),
            ])
            school.pricing = [
                Pricing(age_group="grade_5_7", category=PriceCategory.TUITION,
                       amount=school_data["price"], period=PricePeriod.YEARLY, source=PriceSource.OFFICIAL),
            ]
            if school_data.get("nvo"):
                year_offsets = NVO_FIVE_YEAR_DELTAS if school_data.get("nvo_years", 5) == 5 else NVO_THREE_YEAR_DELTAS
                school.exam_results = build_nvo_results(
                    base_bg=school_data["nvo"][0],
                    base_math=school_data["nvo"][1],
                    exam_type="nvo_7",
                    year_offsets=year_offsets,
                )
            schools.append(school)

        # ===== PRIVATE GYMNASIUMS (7 schools) =====

        # American College of Sofia (treated as private with language focus)
        school = legacy_school(
            name="Американски колеж в София",
            name_en="American College of Sofia",
            school_type="private",
            education_level="upper_secondary",
            website_url="https://www.acs.bg",
            summary_bg="Престижно международно училище с програма по IB Diploma и SAT.",
            summary_en="Prestigious international school with IB Diploma and SAT programs.",
            num_pupils=520,
            admission_info={"status": "waitlist", "requirements": "test"},
            attributes={
                "languages_of_instruction": ["english"],
                "special_focus": "IB Diploma",
                "language_focus": [
                    {"language": "english", "level": "immersion"},
                ],
                "special_programs": ["extended_day"],
                "facilities": ["library", "computer_lab"],
                "teaching_approach": ["ib_program"]
            }
        )
        set_locations(school, [
            legacy_location(age_group="grade_8_12", address="ул. Коста Лулчев 13, Симеоново, София",
                          address_en=transliterate_address("ул. Коста Лулчев 13, Симеоново, София"),
                          lat=42.6320, lng=23.3445, shift="full_day", is_primary=True),
        ])
        school.pricing = [
            Pricing(age_group="grade_8_12", category=PriceCategory.TUITION, amount=24000,
                   period=PricePeriod.YEARLY, source=PriceSource.OFFICIAL),
        ]
        schools.append(school)

        # Private Bulgarian gymnasium
        school = legacy_school(
            name='ЧСУ "Св. Паисий Хилендарски"',
            name_en=transliterate_bulgarian('ЧСУ "Св. Паисий Хилендарски"'),
            school_type="private",
            education_level="upper_secondary",
            summary_bg="Частна гимназия с профил хуманитарни науки.",
            summary_en="Private gymnasium with humanities focus.",
            num_pupils=320,
            admission_info={"status": "accepting", "requirements": "interview"},
            attributes={"languages_of_instruction": ["bulgarian"], "special_focus": "humanities"}
        )
        set_locations(school, [
            legacy_location(age_group="grade_8_12", address="бул. Витоша 15, Център, София",
                          address_en=transliterate_address("бул. Витоша 15, Център, София"),
                          lat=42.6890, lng=23.3190, shift="full_day", is_primary=True),
        ])
        school.pricing = [
            Pricing(age_group="grade_8_12", category=PriceCategory.TUITION, amount=4500,
                   period=PricePeriod.YEARLY, source=PriceSource.SCRAPED_WEBSITE),
        ]
        school.exam_results = build_nvo_results(
            base_bg=76.5,
            base_math=72.8,
            exam_type="nvo_10",
            year_offsets=NVO_THREE_YEAR_DELTAS,
        )
        schools.append(school)

        # Additional private gymnasium with STEM focus
        school = legacy_school(
            name='ЧСУ "Проф. Иван Шишман"',
            name_en=transliterate_bulgarian('ЧСУ "Проф. Иван Шишман"'),
            school_type="private",
            education_level="upper_secondary",
            summary_bg="Частна гимназия с профил математика и технологии.",
            summary_en="Private gymnasium with math and technology focus.",
            num_pupils=280,
            admission_info={"status": "accepting", "requirements": "test"},
            attributes={
                "languages_of_instruction": ["bulgarian"],
                "special_focus": "mathematics",
                "special_programs": ["sports_program", "meals_provided"],
                "facilities": ["computer_lab", "library", "cafeteria"]
            }
        )
        set_locations(school, [
            legacy_location(age_group="grade_8_12", address="ул. Богатица 8, Лозенец, София",
                          address_en=transliterate_address("ул. Богатица 8, Лозенец, София"),
                          lat=42.6730, lng=23.3300, shift="full_day", is_primary=True),
        ])
        school.pricing = [
            Pricing(age_group="grade_8_12", category=PriceCategory.TUITION, amount=9600,
                   period=PricePeriod.YEARLY, source=PriceSource.OFFICIAL),
        ]
        school.exam_results = build_nvo_results(
            base_bg=80.2,
            base_math=84.1,
            exam_type="nvo_10",
            year_offsets=NVO_THREE_YEAR_DELTAS,
        )
        schools.append(school)

        private_gymnasiums = [
            {"name": 'ЧСУ "Св. Николай"', "area": "Иван Вазов", "lat": 42.6845, "lng": 23.3170, "price": 8800,
             "status": "accepting", "requirements": "interview", "focus": "humanities", "nvo": [78.6, 74.2]},
            {"name": 'ЧСУ "Европа"', "area": "Младост 2", "lat": 42.6535, "lng": 23.3775, "price": 10500,
             "status": "waitlist", "requirements": "test", "focus": "foreign_languages", "nvo": [82.5, 79.3]},
            {"name": 'ЧСУ "Хелиос"', "area": "Бели брези", "lat": 42.6675, "lng": 23.2955, "price": 9200,
             "status": "accepting", "requirements": "interview", "focus": "general", "nvo": [76.9, 72.8]},
            {"name": 'ЧСУ "Аполон"', "area": "Слатина", "lat": 42.6865, "lng": 23.3605, "price": 9900,
             "status": "accepting", "requirements": "test", "focus": "mathematics", "nvo": [83.7, 86.2]},
        ]

        for school_data in private_gymnasiums:
            school = legacy_school(
                name=school_data["name"],
                name_en=transliterate_bulgarian(school_data["name"]),
                school_type="private",
                education_level="upper_secondary",
                summary_bg=f"Частна гимназия в кв. {school_data['area']}.",
                summary_en=f"Private gymnasium in {school_data['area']}.",
                num_pupils=260,
                admission_info={"status": school_data["status"], "requirements": school_data["requirements"]},
                attributes={
                    "languages_of_instruction": ["bulgarian"],
                    "special_focus": school_data["focus"],
                    "special_programs": ["meals_provided"],
                    "facilities": ["library", "cafeteria"]
                }
            )
            set_locations(school, [
                legacy_location(age_group="grade_8_12", address=f"кв. {school_data['area']}, София",
                              address_en=transliterate_address(f"кв. {school_data['area']}, София"),
                              lat=school_data["lat"], lng=school_data["lng"], shift="full_day", is_primary=True),
            ])
            school.pricing = [
                Pricing(age_group="grade_8_12", category=PriceCategory.TUITION, amount=school_data["price"],
                       period=PricePeriod.YEARLY, source=PriceSource.OFFICIAL),
            ]
            school.exam_results = build_nvo_results(
                base_bg=school_data["nvo"][0],
                base_math=school_data["nvo"][1],
                exam_type="nvo_10",
                year_offsets=NVO_THREE_YEAR_DELTAS,
            )
            schools.append(school)

        # ===== STATE GYMNASIUMS (10 schools) =====

        state_gymnasiums = [
            {"name": '51 СУ "Елисавета Багряна"', "area": "Център", "lat": 42.6915, "lng": 23.3360,
             "nvo": [92.3, 89.7], "min_score": 196.5, "focus": "mathematics"},
            {"name": '18 СУ "Уилям Гладстон"', "area": "Център", "lat": 42.6960, "lng": 23.3310,
             "nvo": [88.5, 82.3], "min_score": 189.2, "focus": "foreign_languages"},
            {"name": '91 НЕГ "Проф. Константин Гълъбов"', "area": "Люлин", "lat": 42.7210, "lng": 23.2550,
             "nvo": [85.2, 80.5], "min_score": 178.5, "focus": "economics"},
            {"name": '73 СУ "Владислав Граматик"', "area": "Дружба 2", "lat": 42.6525, "lng": 23.3980,
             "nvo": [81.3, 78.9], "min_score": 172.8, "focus": "general"},
            {"name": '164 ГПИЕ "Мигел де Сервантес"', "area": "Лозенец", "lat": 42.6755, "lng": 23.3340,
             "nvo": [90.1, 86.4], "min_score": 192.0, "focus": "foreign_languages"},
            {"name": '128 СУ "Ал. Константинов"', "area": "Обеля", "lat": 42.7405, "lng": 23.2510,
             "nvo": [79.5, 76.8], "min_score": 170.4, "focus": "general"},
            {"name": '35 СЕУ "Добри Войников"', "area": "Подуяне", "lat": 42.7075, "lng": 23.3500,
             "nvo": [84.9, 81.2], "min_score": 181.0, "focus": "foreign_languages"},
            {"name": '22 СЕУ "Георги С. Раковски"', "area": "Слатина", "lat": 42.6875, "lng": 23.3620,
             "nvo": [83.1, 79.6], "min_score": 176.5, "focus": "foreign_languages"},
            {"name": '44 СУ "Неофит Бозвели"', "area": "Овча купел", "lat": 42.6755, "lng": 23.2610,
             "nvo": [78.2, 75.0], "min_score": 168.3, "focus": "general"},
            {"name": '133 СУ "Александър Пушкин"', "area": "Борово", "lat": 42.6680, "lng": 23.2850,
             "nvo": [80.4, 77.2], "min_score": 171.6, "focus": "humanities"},
        ]

        for school_data in state_gymnasiums:
            school = legacy_school(
                name=school_data["name"],
                name_en=transliterate_bulgarian(school_data["name"]),
                school_type="state",
                education_level="upper_secondary",
                summary_bg=f"Държавна гимназия с профил {school_data['focus']} в кв. {school_data['area']}.",
                summary_en=f"State gymnasium with {school_data['focus']} focus in {school_data['area']}.",
                num_pupils=750,
                admission_info={
                    "system": "nvo_score",
                    "historical_min_scores": [{"year": 2024, "min_score": school_data["min_score"]}]
                },
                attributes={"languages_of_instruction": ["bulgarian"], "special_focus": school_data["focus"]}
            )
            set_locations(school, [
                legacy_location(age_group="grade_8_12", address=f"кв. {school_data['area']}, София",
                              address_en=transliterate_address(f"кв. {school_data['area']}, София"),
                              lat=school_data["lat"], lng=school_data["lng"], shift="morning", is_primary=True),
            ])
            school.exam_results = build_nvo_results(
                base_bg=school_data["nvo"][0],
                base_math=school_data["nvo"][1],
                exam_type="nvo_7",
                year_offsets=NVO_FIVE_YEAR_DELTAS,
            )
            school.exam_results.extend(
                build_nvo_results(
                    base_bg=school_data["nvo"][0] - 6.2,
                    base_math=school_data["nvo"][1] - 5.4,
                    exam_type="nvo_10",
                    year_offsets=NVO_THREE_YEAR_DELTAS,
                )
            )
            schools.append(school)

        # ===== TEST SCHOOL: Multi-Grade NVO Results =====
        # This school demonstrates the multi-grade tab feature
        # with exam results for 4th, 7th, and 10th grades

        test_multi_grade = legacy_school(
            name='TEST: 155 СУ "Акад. Методи Попов"',
            name_en='TEST: 155 SU "Acad. Metodi Popov"',
            school_type="state",
            education_level="lower_secondary",  # Primary education level
            summary_bg="ТЕСТОВО училище за демонстрация на резултати от НВО в множество класове. Учениците показват прогресивно подобрение през годините.",
            summary_en="TEST school demonstrating multi-grade NVO results. Students show progressive improvement over the years.",
            num_pupils=950,
            admission_info={
                "system": "district",
                "status": "accepting"
            },
            attributes={
                "languages_of_instruction": ["bulgarian", "english"],
                "special_focus": "general",
                "has_canteen": True,
                "facilities": ["library", "computer_lab", "sports_field", "science_lab"],
                "class_size": 24,
                "teacher_student_ratio": "1:12",
                "school_hours": "8:00-17:00",
                "established_year": 1975,
                "language_focus": [
                    {"language": "english", "level": "intensive"},
                    {"language": "french", "level": "enrichment"}
                ],
                "teaching_approach": ["project_based", "student_centered"],
                "special_programs": ["stem_program", "robotics_club"],
                "activities_offered": ["chess", "drama", "music", "sport"]
            }
        )

        set_locations(test_multi_grade, [
            legacy_location(
                age_group="grade_1_4",
                address="бул. Цариградско шосе 115, София",
                address_en="bul. Tsarigradsko shose 115, Sofia",
                lat=42.6580,
                lng=23.3750,
                shift="morning",
                is_primary=True,
                phone="+359 2 876 5432"
            ),
            legacy_location(
                age_group="grade_5_7",
                address="бул. Цариградско шосе 115, София",
                address_en="bul. Tsarigradsko shose 115, Sofia",
                lat=42.6580,
                lng=23.3750,
                shift="morning",
                is_primary=False
            ),
            legacy_location(
                age_group="grade_8_12",
                address="бул. Цариградско шосе 117, София",
                address_en="bul. Tsarigradsko shose 117, Sofia",
                lat=42.6585,
                lng=23.3755,
                shift="morning",
                is_primary=False
            ),
        ])

        # 4th Grade NVO Results (Starting point: 72% Bulgarian, 75% Math)
        test_multi_grade.exam_results = build_nvo_results(
            base_bg=72.0,
            base_math=75.0,
            exam_type="nvo_4",
            year_offsets=NVO_FIVE_YEAR_DELTAS,  # 5 years of data
        )

        # 7th Grade NVO Results (Improved: 78% Bulgarian, 82% Math - shows growth!)
        test_multi_grade.exam_results.extend(
            build_nvo_results(
                base_bg=78.0,
                base_math=82.0,
                exam_type="nvo_7",
                year_offsets=NVO_FIVE_YEAR_DELTAS,
            )
        )

        # 10th Grade NVO Results (Best performance: 85% Bulgarian, 88% Math)
        test_multi_grade.exam_results.extend(
            build_nvo_results(
                base_bg=85.0,
                base_math=88.0,
                exam_type="nvo_10",
                year_offsets=NVO_THREE_YEAR_DELTAS,
            )
        )

        schools.append(test_multi_grade)

        # Add field sources before persisting
        for school in schools:
            school.field_sources = build_field_sources_for_school(school)
            session.add(school)
        
        await session.commit()
        
        print(f"✅ Successfully seeded {len(schools)} schools:")
        print("   Private kindergartens: 20")
        print("   State kindergartens: 18")
        print("   Private primary schools: 14")
        print("   State primary schools: 14")
        print("   State lower secondary schools: 6")
        print("   Private lower secondary schools: 6")
        print("   International schools: 5")
        print("   Private gymnasiums: 7")
        print("   State gymnasiums: 10")
        print("   🧪 TEST: Multi-grade school: 1")
        print(f"   Total locations: ~{len(schools) * 2}")
        print("\n🎯 Comprehensive coverage for filter testing!")
        print("   - Every age group has multiple options")
        print("   - State and private all represented")
        print("   - Prices range from 480 BGN/mo to 24,000 BGN/yr")
        print("   - Multiple neighborhoods covered")
        print("\n🧪 TEST SCHOOL FOR MULTI-GRADE TABS:")
        print("   School: TEST: 155 СУ 'Акад. Методи Попов'")
        print("   Has NVO results for: 4th grade (75%), 7th grade (82%), 10th grade (88%)")
        print("   Expected insight: 📈 'Students improve as they progress'")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Replace disposable local demo school data.")
    parser.add_argument("--reset-demo-data", action="store_true", help="Confirm replacement of demo data.")
    args = parser.parse_args()
    try:
        asyncio.run(seed_database(reset_demo_data=args.reset_demo_data))
    except ValueError as exc:
        parser.error(str(exc))
