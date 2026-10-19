"""Translate-texts stage and the response projections that use its translations: website and
register text a page shows as written gets the other language's words. The model is stubbed."""

from __future__ import annotations

import pytest

from app.models.pricing import PriceSource, Pricing
from app.models.school import School, SchoolLocation
from app.scrapers.stages import text_translations as stage
from app.utils.text_translations import needs_translation, stored
from tests.test_api import PRICING_VERIFIED_AT, _pricing_evidence_page, _validation_report, _verified_pricing_context

INSTALMENT = "вноска II, до 15.11.26 г."
ENGLISH_PLAN = "Full-day Program"
CLASS_NAME = "Математически - АЕ интензивно, НЕ; М и Инф"
BUILDING = "ДГ №91 Слънчев кът - сграда Слънце"
FACILITY = "Комутатор за компютърния кабинет"

ANSWERS = {
    INSTALMENT: "Instalment II, by 15.11.26",
    CLASS_NAME: "Mathematics - intensive English, German; Mathematics and Informatics",
    FACILITY: "Switch for the computer room",
    ENGLISH_PLAN: "Целодневна програма",
}


def _caller(answers=ANSWERS):
    calls = []

    async def call(texts, locale):
        calls.append((locale, list(texts)))
        return [answers.get(text) for text in texts]

    call.calls = calls
    return call


def test_needs_translation():
    assert needs_translation(INSTALMENT, "en") and not needs_translation(INSTALMENT, "bg")
    assert needs_translation(ENGLISH_PLAN, "bg") and not needs_translation(ENGLISH_PLAN, "en")
    # Numbers alone are in no language; a Latin brand inside Bulgarian words is Bulgarian.
    assert not needs_translation("2026/2027", "bg") and not needs_translation("2026/2027", "en")
    assert not needs_translation("Програма STEM", "bg")


def test_checked_keeps_only_a_translation_in_the_target_language_with_the_same_numbers():
    assert stage.checked(INSTALMENT, " Instalment  II, by 15.11.26 ", "en") == "Instalment II, by 15.11.26"
    assert stage.checked(INSTALMENT, "Instalment 2, by 15.11.26", "en") is None  # an added number
    assert stage.checked(INSTALMENT, "Instalment II", "en") is None  # a dropped date
    assert stage.checked(INSTALMENT, "Вноска II, by 15.11.26", "en") is None  # still Cyrillic
    assert stage.checked(ENGLISH_PLAN, "Full-day Programme програма", "bg") is None  # mostly Latin
    assert stage.checked(ENGLISH_PLAN, ENGLISH_PLAN, "bg") is None
    assert stage.checked(ENGLISH_PLAN, None, "bg") is None
    assert stage.checked("Такса", "x" * 100, "en") is None  # too long to be the same words


def _school(**kwargs):
    defaults = dict(
        name_i18n={"bg": "Частно училище Тест"},
        country_code="bg",
        city="sofia",
        school_type="private",
        education_level="primary",
        website_url="https://test-school.bg",
        scrape_status="extracted",
        attributes={"data_validation": _validation_report(status="ok"), "facilities": [FACILITY]},
        admission_info={
            "cutoff_summary": [
                {"stage": "grade_8", "class_code": "00897", "class_name": CLASS_NAME, "building": BUILDING},
            ]
        },
    )
    return School(**(defaults | kwargs))


async def _priced_school(db, **kwargs):
    school = _school(**kwargs)
    db.add(school)
    await db.flush()
    db.add(SchoolLocation(school_id=school.id, address_i18n={"bg": "София"}, lat=42.7, lng=23.3, is_primary=True))
    page = await _pricing_evidence_page(db, school, "https://test-school.bg/fees")
    for plan_name, english in ((INSTALMENT, None), ("Месечна такса", "Monthly fee"), (ENGLISH_PLAN, None)):
        db.add(
            Pricing(
                school_id=school.id,
                category="tuition",
                amount=500,
                currency="EUR",
                period="monthly",
                plan_name=plan_name,
                source=PriceSource.OFFICIAL,
                scraped_at=PRICING_VERIFIED_AT,
                source_url="https://test-school.bg/fees",
                source_page_id=page.id,
                pricing_context=_verified_pricing_context(0.9, plan_name_en=english),
            )
        )
    await db.commit()
    return school


@pytest.mark.asyncio
async def test_the_stage_translates_once_and_keeps_exactly_what_the_page_shows(db_session):
    school = await _priced_school(db_session)

    call = _caller()
    report = await stage.run_text_translations(db_session, school_ids=[school.id], call=call)
    # The agent's English counts: "Месечна такса" is not asked for.
    assert report["asked"] == {"en": 3, "bg": 1} and report["translated"] == {"en": 3, "bg": 1}
    assert report["schools_changed"] == 1 and "text_translations" not in school.attributes  # a dry run

    await stage.run_text_translations(db_session, school_ids=[school.id], call=_caller(), apply=True)
    translations = stored(school.attributes)
    assert translations["en"] == {
        INSTALMENT: "Instalment II, by 15.11.26",
        CLASS_NAME: ANSWERS[CLASS_NAME],
        FACILITY: ANSWERS[FACILITY],
    }
    assert translations["bg"] == {ENGLISH_PLAN: "Целодневна програма"}

    # Nothing new: no model call and nothing written.
    again = _caller()
    report = await stage.run_text_translations(db_session, school_ids=[school.id], call=again, apply=True)
    assert again.calls == [] and report["schools_changed"] == 0

    # A text the page no longer shows is dropped; one the model could not translate is kept
    # as null, so it is not asked again.
    school.attributes = {**school.attributes, "facilities": ["Нов басейн"]}
    await db_session.commit()
    await stage.run_text_translations(db_session, school_ids=[school.id], call=_caller(), apply=True)
    assert stored(school.attributes)["en"] == {
        INSTALMENT: "Instalment II, by 15.11.26",
        CLASS_NAME: ANSWERS[CLASS_NAME],
        "Нов басейн": None,
    }
    again = _caller()
    await stage.run_text_translations(db_session, school_ids=[school.id], call=again, apply=True)
    assert again.calls == []


@pytest.mark.asyncio
async def test_another_schools_translation_is_reused_without_a_call(db_session):
    first = await _priced_school(db_session)
    await stage.run_text_translations(db_session, school_ids=[first.id], call=_caller(), apply=True)
    second = await _priced_school(db_session, name_i18n={"bg": "Частно училище Второ"})

    call = _caller()
    await stage.run_text_translations(db_session, call=call, apply=True)
    assert call.calls == [] and stored(second.attributes) == stored(first.attributes)


@pytest.mark.asyncio
async def test_the_api_shows_the_translations_on_each_page(seeded_db, seeded_client):
    school = await _priced_school(seeded_db)
    await stage.run_text_translations(seeded_db, school_ids=[school.id], call=_caller(), apply=True)

    detail = (await seeded_client.get(f"/schools/{school.id}")).json()
    contexts = {row["plan_name"]: row["pricing_context"] for row in detail["pricing"]}
    assert contexts[INSTALMENT]["plan_name_en"] == "Instalment II, by 15.11.26"
    assert contexts["Месечна такса"]["plan_name_en"] == "Monthly fee"  # the agent's English stays
    assert contexts[ENGLISH_PLAN]["plan_name_bg"] == "Целодневна програма"
    assert contexts[ENGLISH_PLAN]["plan_name_en"] is None

    row = detail["admission_info"]["cutoff_summary"][0]
    assert row["class_name_en"] == ANSWERS[CLASS_NAME]
    assert row["building_en"] == "Kindergarten No. 91 Slanchev kat - building Slantse"
    assert detail["attributes_i18n"]["en"]["facilities"] == [ANSWERS[FACILITY]]
    assert detail["attributes_i18n"]["bg"]["facilities"] == [FACILITY]
    # The stored block itself never ships.
    assert "text_translations" not in str(detail)

    listed = next(row for row in (await seeded_client.get("/schools")).json() if row["id"] == school.id)
    assert listed["admission_info"]["cutoff_summary"][0]["class_name_en"] == ANSWERS[CLASS_NAME]


@pytest.mark.asyncio
async def test_a_mismatched_batch_is_asked_again_and_a_failed_language_does_not_stop_the_other(db_session):
    school = await _priced_school(db_session)

    async def call(texts, locale):
        if locale == "en" and (len(texts) > 1 or texts == [FACILITY]):
            raise stage.BatchMismatch("ids shifted")
        return [ANSWERS.get(text) for text in texts]

    # A mismatched batch is asked text by text; a text that still mismatches is stored as null,
    # so the hourly publish does not ask it (and back up the DB) again and again.
    report = await stage.run_text_translations(db_session, school_ids=[school.id], call=call, apply=True)
    assert report["translated"] == {"en": 2, "bg": 1} and report["stopped"] is None
    assert stored(school.attributes)["en"] == {
        INSTALMENT: ANSWERS[INSTALMENT],
        CLASS_NAME: ANSWERS[CLASS_NAME],
        FACILITY: None,
    }
    assert (await stage.run_text_translations(db_session, school_ids=[school.id], plan=True))["schools_to_change"] == 0

    async def broken(texts, locale):
        raise RuntimeError("provider down")

    school.attributes = {**school.attributes, "facilities": ["Нов басейн"]}
    await db_session.commit()
    report = await stage.run_text_translations(db_session, school_ids=[school.id], call=broken, apply=True)
    assert "en: run stopped: provider down" in report["stopped"]
    # What was stored before stays.
    assert stored(school.attributes)["bg"] == {ENGLISH_PLAN: "Целодневна програма"}


def test_checked_keeps_roman_numerals():
    assert stage.checked(INSTALMENT, "Instalment III, by 15.11.26", "en") is None
    assert stage.checked("ХI клас", "Grade XI", "en") == "Grade XI"  # a Cyrillic Х
    # A lone "М" is mathematics, not a numeral.
    assert stage.checked(CLASS_NAME, ANSWERS[CLASS_NAME], "en") == ANSWERS[CLASS_NAME]
    # Nor are the letters of an abbreviation.
    assert stage.checked("V.I.P. lunch", "ВИП обяд", "bg") == "ВИП обяд"


@pytest.mark.asyncio
async def test_a_stored_translation_that_fails_the_checks_now_is_asked_again(db_session):
    school = await _priced_school(db_session)
    await stage.run_text_translations(db_session, school_ids=[school.id], call=_caller(), apply=True)
    translations = stored(school.attributes)
    translations["en"][INSTALMENT] = "Instalment III, by 15.11.26"
    school.attributes = {**school.attributes, "text_translations": stage.translation_block(translations)}
    await db_session.commit()

    assert (await stage.run_text_translations(db_session, school_ids=[school.id], plan=True))["schools_to_change"] == 1
    call = _caller()
    await stage.run_text_translations(db_session, school_ids=[school.id], call=call, apply=True)
    assert call.calls == [("en", [INSTALMENT])]
    assert stored(school.attributes)["en"][INSTALMENT] == ANSWERS[INSTALMENT]


@pytest.mark.asyncio
async def test_plan_sees_a_better_translation_from_another_school(db_session):
    first = await _priced_school(db_session)
    await stage.run_text_translations(db_session, school_ids=[first.id], call=_caller(), apply=True)
    second = await _priced_school(db_session, name_i18n={"bg": "Частно училище Второ"})
    await stage.run_text_translations(db_session, school_ids=[second.id], call=_caller({}), apply=True)
    report = await stage.run_text_translations(db_session, plan=True)
    assert report["schools_to_change"] == 1 and report["asked"] == {"en": 0, "bg": 0}


@pytest.mark.asyncio
async def test_plan_says_what_apply_would_change_without_a_call(db_session):
    school = await _priced_school(db_session)
    call = _caller()
    report = await stage.run_text_translations(db_session, school_ids=[school.id], call=call, plan=True)
    assert report["schools_to_change"] == 1 and call.calls == []
    await stage.run_text_translations(db_session, school_ids=[school.id], call=_caller(), apply=True)
    report = await stage.run_text_translations(db_session, school_ids=[school.id], call=call, plan=True)
    assert report["schools_to_change"] == 0


@pytest.mark.asyncio
async def test_a_usable_translation_wins_over_another_schools_stored_failure(db_session):
    first = await _priced_school(db_session)
    await stage.run_text_translations(db_session, school_ids=[first.id], call=_caller(), apply=True)
    second = await _priced_school(db_session, name_i18n={"bg": "Частно училище Второ"})
    await stage.run_text_translations(db_session, school_ids=[second.id], call=_caller({}), apply=True)
    assert stored(second.attributes)["en"][INSTALMENT] is None

    await stage.run_text_translations(db_session, call=_caller({}), apply=True)
    assert stored(first.attributes)["en"][INSTALMENT] == "Instalment II, by 15.11.26"
    assert stored(second.attributes)["en"][INSTALMENT] == "Instalment II, by 15.11.26"


@pytest.mark.asyncio
async def test_a_fee_row_the_api_withholds_is_not_translated(db_session):
    school = await _priced_school(db_session)
    hidden = next(row for row in (await db_session.run_sync(lambda _: school.pricing)) if row.plan_name == INSTALMENT)
    hidden.source_url = None  # the row gate withholds a fee with no source
    await db_session.commit()
    call = _caller()
    await stage.run_text_translations(db_session, school_ids=[school.id], call=call)
    asked = [text for _, texts in call.calls for text in texts]
    assert INSTALMENT not in asked and ENGLISH_PLAN in asked
