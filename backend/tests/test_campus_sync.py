"""UF42(b): campuses and age groups from the verified site."""

from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models import School
from app.models.school import SchoolLocation, SchoolLocationAgeGroupShift
from app.scrapers import campus_sync as cs
from app.scrapers.campus_sync import (
    AGE_GROUP_TAG_PREFIX,
    CAMPUS_TAG,
    CAMPUSES_KEY,
    ExistingLocation,
    SitePage,
    age_group_defs,
    apply_campus_sync,
    extract_campus_candidates,
    grade_offset,
    plan_campus_sync,
    remove_website_campus_data,
    stated_range,
)
from app.scrapers.url_validator import ValidationResult, _update_validation_result

# The country's education_config age groups (bg), as stored in `countries`.
BG_CONFIG = {
    "age_groups": [
        {"key": "nursery", "category": "kindergarten", "min_diff": 0, "max_diff": 1,
         "label_i18n": {"bg": "Ясла", "en": "Nursery"}},
        {"key": "first", "category": "kindergarten", "min_diff": 3, "max_diff": 3,
         "label_i18n": {"bg": "I група", "en": "First group"}},
        {"key": "second", "category": "kindergarten", "min_diff": 4, "max_diff": 4,
         "label_i18n": {"bg": "II група", "en": "Second group"}},
        {"key": "third", "category": "kindergarten", "min_diff": 5, "max_diff": 5,
         "label_i18n": {"bg": "III група", "en": "Third group"}},
        {"key": "preschool", "category": ["kindergarten", "school"], "min_diff": 6, "max_diff": 6,
         "label_i18n": {"bg": "Подготвителна", "en": "Preschool"}},
        {"key": "grade_1_4", "category": "school", "min_diff": 7, "max_diff": 10,
         "label_i18n": {"bg": "1-4 клас", "en": "Grades 1-4"}},
        {"key": "grade_5_7", "category": "school", "min_diff": 11, "max_diff": 13,
         "label_i18n": {"bg": "5-7 клас", "en": "Grades 5-7"}},
        {"key": "grade_8_12", "category": "school", "min_diff": 14, "max_diff": 18,
         "label_i18n": {"bg": "8-12 клас", "en": "Grades 8-12"}},
    ]
}
DEFS = age_group_defs(BG_CONFIG)

SCHOOL_SITE = "https://sofia-school.maplebear.bg/"
KG_SITE = "https://sofia-kindergarten.maplebear.bg/"

# Shapes of sofia-school.maplebear.bg (contact page in Bulgarian, admissions in English).
SCHOOL_CONTACT = """### Данни за контакт
**Maple Bear Камбаните :**
**Maple Bear Бояна:**
**Работно време:**
Понеделник – Петък: 9:00 – 17:00
### Местоположение 1
**Maple Bear Камбаните**
ул. Витошки Камбани 9
София 1756
България
### Местоположение 2
**Maple Bear Бояна**
ул. Панорамен Път 38
София 1616
"""
SCHOOL_ADMISSIONS = (
    "Requested level of entry\nPreschool (Grade 0)\nGrade 1\nGrade 5 (not available in Maple Bear Boyana)\n"
    "Our Boyana location serves students from Preschool (Grade 0) to Grade 4 and offers a smaller, cozier "
    "setting ideal for younger children. The Kambanite location accommodates students from Preschool through "
    "Grade 7 and provides a larger space to support a wider range of activities and age groups."
)
KG_CONTACT = "Maple Bear Kindergarten Vitosha\nул. Йордан Стубел 16\nСофия\nДетска градина"


def school_pages():
    return [
        SitePage(SCHOOL_SITE + "kontakti/", SCHOOL_CONTACT, "contact"),
        SitePage(SCHOOL_SITE + "en/admissions", SCHOOL_ADMISSIONS, "admission"),
    ]


def extract(pages, url, level):
    return extract_campus_candidates(
        pages, website_url=url, city="sofia", education_level=level, education_config=BG_CONFIG
    )


# ---------------------------------------------------------------------------
# Maple Bear
# ---------------------------------------------------------------------------

def test_maple_bear_school_campuses_and_their_stated_ranges():
    payload = extract(school_pages(), SCHOOL_SITE, "lower_secondary")

    by_street = {c["address"]: c for c in payload["campuses"]}
    assert set(by_street) == {"ул. Витошки Камбани 9", "ул. Панорамен Път 38"}
    assert by_street["ул. Витошки Камбани 9"]["diff_range"] == [6, 13]  # Preschool through Grade 7
    assert by_street["ул. Панорамен Път 38"]["diff_range"] == [6, 10]  # Preschool (Grade 0) to Grade 4
    assert "Kambanite location accommodates" in by_street["ул. Витошки Камбани 9"]["range_evidence"]


def test_maple_bear_school_plan_adds_kambanite_and_preschool_at_boyana():
    payload = extract(school_pages(), SCHOOL_SITE, "lower_secondary")
    plan = plan_campus_sync(
        payload,
        education_level="lower_secondary",
        education_config=BG_CONFIG,
        existing=[ExistingLocation(1149, 'ул. "Панорамен път" № 38', {"grade_1_4", "grade_5_7"}, True)],
        other_institution_addresses=[(556, 'кв. Витоша, ул. "Йордан Стубел" № 16')],
    )

    assert [(n["address"], n["age_groups"]) for n in plan["new_locations"]] == [
        ("ул. Витошки Камбани 9", ["preschool", "grade_1_4", "grade_5_7"])
    ]
    assert plan["age_groups"] == [
        {"location_id": 1149, "address": 'ул. "Панорамен път" № 38', "add": ["preschool"],
         "evidence": plan["age_groups"][0]["evidence"]}
    ]


def test_maple_bear_kindergarten_gets_no_school_campuses():
    # Its own verified site lists one address: nothing new.
    payload = extract([SitePage(KG_SITE + "contact/", KG_CONTACT, "contact")], KG_SITE, "kindergarten")
    plan = plan_campus_sync(
        payload, education_level="kindergarten", education_config=BG_CONFIG,
        existing=[ExistingLocation(1102, 'кв. Витоша, ул. "Йордан Стубел" № 16', set(), True)],
        other_institution_addresses=[(596, 'ул. "Панорамен път" № 38')],
    )
    assert plan["new_locations"] == []
    # And the school's pages are not its site.
    assert extract(school_pages(), KG_SITE, "kindergarten") is None


# ---------------------------------------------------------------------------
# Negative cases
# ---------------------------------------------------------------------------

GENERIC_CONTACT = """Контакти
Основно училище Пример
ул. Липа 5
София 1000
Административен офис
бул. Витоша 100
София
Детска градина Пример
ул. Бреза 7
София
Филиал Пловдив
ул. Марица 3
Пловдив
Нашият партньор
ул. Роза 9, София
"""


def test_partner_office_other_city_and_sibling_kindergarten_addresses_are_not_campuses():
    payload = extract(
        [SitePage("https://primer.bg/kontakti", GENERIC_CONTACT, "contact")], "https://primer.bg/", "lower_secondary"
    )
    kept = [c["address"] for c in payload["campuses"]]
    skipped = {c["address"]: c["reason"] for c in payload["skipped"]}
    assert kept == ["ул. Липа 5"]
    assert skipped["бул. Витоша 100"] == "office_or_partner_address"
    assert skipped["ул. Бреза 7"] == "describes_other_level"
    assert skipped["ул. Марица 3"] == "city_not_stated"
    assert skipped["ул. Роза 9, София"] == "office_or_partner_address"


def test_sibling_institution_address_is_never_a_new_campus():
    payload = {"site": "x", "campuses": [
        {"address": "ул. Липа 5", "label": "", "context": ""},
        {"address": "ул. Бреза 7", "label": "", "context": ""},
    ], "school_level": None}
    plan = plan_campus_sync(
        payload, education_level="lower_secondary", education_config=BG_CONFIG,
        existing=[ExistingLocation(1, 'ул. "Липа" № 5', set(), True)],
        other_institution_addresses=[(9, 'ул. "Бреза" № 7')],
    )
    assert plan["new_locations"] == []
    assert plan["skipped"] == [{"address": "ул. Бреза 7", "reason": "address_of_institution_9"}]


def test_range_without_campus_name_is_ignored_when_there_are_several_campuses():
    pages = school_pages()
    pages[1] = SitePage(SCHOOL_SITE + "en/admissions", "We teach students from Preschool to Grade 8.", "admission")
    payload = extract(pages, SCHOOL_SITE, "lower_secondary")
    assert all("diff_range" not in c for c in payload["campuses"])


def test_school_wide_ranges_are_not_tied_to_a_building():
    pages = [SitePage("https://solo.bg/kontakti", "Контакти\nул. Липа 5, София", "contact"),
             SitePage("https://solo.bg/priem", "Ваканция за 1 – 11 клас. Приемаме ученици от 1 до 7 клас.", "admission")]
    plan = plan_campus_sync(
        extract(pages, "https://solo.bg/", "lower_secondary"), education_level="lower_secondary",
        education_config=BG_CONFIG, existing=[ExistingLocation(1, 'ул. "Липа" № 5', {"grade_1_4"}, True)],
        other_institution_addresses=[],
    )
    assert plan == {"new_locations": [], "age_groups": [],
                    "skipped": [{"address": "ул. Липа 5, София", "reason": "single_address_not_a_new_campus"}]} or (
        plan["new_locations"] == [] and plan["age_groups"] == []
    )


def test_bulgarian_campus_sentences_state_the_ranges():
    pages = school_pages()
    pages[1] = SitePage(
        SCHOOL_SITE + "priem/",
        "5. клас ( не се предлага в Maple Bear Бояна)\n"
        "Сградата ни в Бояна приема деца от подготвителен клас(ПУК) до 4. клас и осигурява уютна среда. "
        "Сградата ни в Камбаните помещава ученици от подготвителен до 7. клас и разполага с по-обширна база.",
        "admission",
    )
    by_street = {c["address"]: c.get("diff_range") for c in extract(pages, SCHOOL_SITE, "lower_secondary")["campuses"]}
    assert by_street == {"ул. Витошки Камбани 9": [6, 13], "ул. Панорамен Път 38": [6, 10]}


def test_school_heading_on_a_kindergarten_site_is_not_its_campus():
    contact = "## Детска градина\nАдрес:\nул. Липа 5\n1113 София\n## Училище\nАдрес:\nул. Бреза 7\n1113 София"
    payload = extract([SitePage("https://waldorf-x.bg/kontakti", contact, "contact")], "https://waldorf-x.bg/",
                      "kindergarten")
    assert [c["address"] for c in payload["campuses"]] == ["ул. Липа 5"]
    assert {s["address"]: s["reason"] for s in payload["skipped"]} == {"ул. Бреза 7": "describes_other_level"}


# ---------------------------------------------------------------------------
# Age mapping through education_config
# ---------------------------------------------------------------------------

def test_grade_offset_comes_from_config_labels():
    assert grade_offset(DEFS) == 6
    assert grade_offset(age_group_defs({"age_groups": []})) is None


@pytest.mark.parametrize(
    "sentence,expected",
    [
        ("serves students from Preschool (Grade 0) to Grade 4 and offers", (6, 10)),
        ("accommodates students from Preschool through Grade 7 and provides", (6, 13)),
        ("Обучаваме ученици от 1 до 7 клас.", (7, 13)),
        ("Паралелки 1-4 клас", (7, 10)),
        ("Подготовка за 1 клас", None),
        ("from Grade 1 to Grade 4, and also from Grade 5 to Grade 7", None),  # two ranges: ambiguous
    ],
)
def test_stated_ranges(sentence, expected):
    assert stated_range(sentence, DEFS, grade_offset(DEFS)) == expected


# ---------------------------------------------------------------------------
# Database: created only on a verified site, removed on every withhold path
# ---------------------------------------------------------------------------

async def _school_with_candidates(db_session, *, withheld=False):
    from app.models.country import Country

    db_session.add(Country(code="bg", name_i18n={"bg": "България"}, education_config=BG_CONFIG, map_config={},
                           supported_languages=["bg", "en"], default_language="bg", default_currency="EUR"))
    attrs = {
        "validated_website_url": SCHOOL_SITE,
        "data_validation": {"_schema_version": 1, "status": "ok"},
        CAMPUSES_KEY: extract(school_pages(), SCHOOL_SITE, "lower_secondary"),
    }
    if withheld:
        attrs["website_data_withheld"] = True
    school = School(name_i18n={"bg": "Канадско мече"}, country_code="bg", city="sofia", school_type="private",
                    education_level="lower_secondary", website_url=SCHOOL_SITE, scrape_status="extracted",
                    attributes=attrs)
    primary = SchoolLocation(address_i18n={"bg": 'ул. "Панорамен път" № 38'}, is_primary=True, location_tags=[])
    primary.age_group_shifts = [SchoolLocationAgeGroupShift(age_group="grade_1_4"),
                                SchoolLocationAgeGroupShift(age_group="grade_5_7")]
    school.locations = [primary]
    db_session.add(school)
    await db_session.commit()
    return school


async def _locations(db_session, school_id):
    return (await db_session.execute(
        select(SchoolLocation).options(selectinload(SchoolLocation.age_group_shifts))
        .where(SchoolLocation.school_id == school_id).order_by(SchoolLocation.id)
        .execution_options(populate_existing=True)
    )).scalars().all()


@pytest.mark.asyncio
async def test_apply_creates_tagged_campus_and_age_groups_then_withhold_removes_only_those(db_session):
    school = await _school_with_candidates(db_session)
    result = await apply_campus_sync(db_session, school)
    await db_session.commit()
    assert len(result["new_location_ids"]) == 1

    primary, campus = await _locations(db_session, school.id)
    assert sorted(primary.age_groups) == ["grade_1_4", "grade_5_7", "preschool"]
    assert AGE_GROUP_TAG_PREFIX + "preschool" in primary.location_tags
    assert campus.address_i18n == {"bg": "ул. Витошки Камбани 9"}
    assert CAMPUS_TAG in campus.location_tags and "address_source=website_contact" in campus.location_tags
    assert sorted(campus.age_groups) == ["grade_1_4", "grade_5_7", "preschool"]
    assert campus.is_primary is False

    await remove_website_campus_data(db_session, school.id)
    await db_session.commit()
    (primary,) = await _locations(db_session, school.id)
    assert sorted(primary.age_groups) == ["grade_1_4", "grade_5_7"]  # registry rows untouched
    assert not any(t.startswith(AGE_GROUP_TAG_PREFIX) for t in primary.location_tags)


@pytest.mark.asyncio
async def test_nothing_is_created_while_the_marker_is_set(db_session):
    school = await _school_with_candidates(db_session, withheld=True)
    result = await apply_campus_sync(db_session, school)
    assert result["new_location_ids"] == []
    assert len(await _locations(db_session, school.id)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "result,reason",
    [
        (ValidationResult.INVALID, "Connection timeout"),
        (ValidationResult.AMBIGUOUS, "unclear"),
        (ValidationResult.INVALID, "Website identity mismatch: X"),
    ],
)
async def test_url_validation_withhold_paths_delete_campus_data(db_session, result, reason):
    school = await _school_with_candidates(db_session)
    await apply_campus_sync(db_session, school)
    await db_session.commit()

    class SessionCtx:
        async def __aenter__(self):
            return db_session

        async def __aexit__(self, *_):
            return False

    with patch("app.database.async_session_maker", return_value=SessionCtx()):
        await _update_validation_result(school.id, SCHOOL_SITE, result, None, reason)

    locations = await _locations(db_session, school.id)
    assert len(locations) == 1
    assert "preschool" not in locations[0].age_groups
    assert {"grade_1_4", "grade_5_7"} <= set(locations[0].age_groups)


@pytest.mark.asyncio
async def test_shared_site_withhold_path_deletes_campus_data(db_session):
    from app.scrapers.url_validator import clear_website_derived_locations

    school = await _school_with_candidates(db_session)
    await apply_campus_sync(db_session, school)
    await db_session.commit()

    preview = await clear_website_derived_locations(db_session, school.id, preview=True)
    assert {c["action"] for c in preview} == {"delete_campus_location", "remove_website_age_groups"}
    assert len(await _locations(db_session, school.id)) == 2

    await clear_website_derived_locations(db_session, school.id, keep_officially_confirmed_addresses=True)
    await db_session.commit()
    assert len(await _locations(db_session, school.id)) == 1


@pytest.mark.asyncio
async def test_validation_creates_campuses_when_it_clears_the_marker(db_session):
    from unittest.mock import AsyncMock

    from app.scrapers.validator import validate_school_data

    school = await _school_with_candidates(db_session, withheld=True)
    attrs = dict(school.attributes)
    attrs["data_validation_attempt"] = {"status": "pending", "started_at": "2026-09-25T00:00:00+00:00"}
    attrs["extracted"] = {"contact": {"address": "ул. Панорамен Път 38"}}
    school.attributes = attrs
    await db_session.commit()
    school_id = school.id

    with patch("app.scrapers.validator.geocode_campus_locations", new=AsyncMock()) as geocode:
        result = await validate_school_data(db_session, school_id)

    assert result["status"] in {"ok", "needs_review"}
    locations = await _locations(db_session, school_id)
    assert [loc.address_i18n["bg"] for loc in locations] == ['ул. "Панорамен път" № 38', "ул. Витошки Камбани 9"]
    geocode.assert_awaited_once()
    assert geocode.await_args.args[1] == [locations[1].id]


def test_module_has_no_hardcoded_age_group_keys():
    import inspect

    source = inspect.getsource(cs)
    for key in ('"grade_1_4"', '"grade_5_7"', '"preschool"', '"nursery"'):
        assert key not in source
