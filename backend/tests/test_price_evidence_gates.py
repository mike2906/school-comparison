"""UF45 step 2: Stage 6 validation turns the evidence rules into publish gates.

Rows that break rules 1-4 get an error on ``pricing[{id}]`` and drop out of
``GET /schools/{id}``; a stated null period is filled in; a display name a site-group
sibling shows (rule 5) falls back to the registry name.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import Pricing, School
from app.models.pricing import PriceSource
from app.models.scrape_log import ScrapeType
from app.models.source_page import SourcePage
from app.scrapers import validator as validator_module
from app.utils.display_gating import display_name_blocked
from tests.test_price_evidence import SLAVEICHE, WALDORF


async def _school_with_page(db_session, *, level, website_url, page_text, name="Тест"):
    school = School(
        name_i18n={"bg": name},
        country_code="bg",
        school_type="private",
        education_level=level,
        city="sofia",
        website_url=website_url,
        scrape_status="extracted",
    )
    db_session.add(school)
    await db_session.flush()
    page = SourcePage(
        school_id=school.id,
        scrape_type=ScrapeType.WEBSITE,
        source_url=f"{website_url}/fees",
        content_hash="x",
        page_category="pricing",
        is_valid=True,
        raw_markdown=page_text,
    )
    db_session.add(page)
    await db_session.flush()
    return school, page


def _row(school, page, category, amount, period=None, plan_name=None, notes=None):
    return Pricing(
        school_id=school.id,
        category=category,
        amount=amount,
        currency="EUR",
        period=period,
        plan_name=plan_name,
        source=PriceSource.SCRAPED_WEBSITE,
        source_url=page.source_url,
        source_page_id=page.id,
        pricing_context={"confidence": 1.0, "notes": notes},
    )


def _issue_codes(school, row):
    report = school.attributes["data_validation"]
    return {
        issue["code"]
        for issue in report["issues"]
        if issue["field_path"] == f"pricing[{row.id}]" and issue["severity"] == "error"
    }


@pytest.mark.asyncio
async def test_630_wrong_rows_are_withheld_and_the_fixed_ones_publish(db_session, client):
    school, page = await _school_with_page(
        db_session, level="upper_secondary", website_url="https://www.waldorf.bg", page_text=WALDORF
    )
    good = _row(school, page, "tuition", 6150, "yearly", "Предучилищен и от 1-ви до 7-ми клас")
    kindergarten = _row(school, page, "tuition", 6200, "yearly", "Детска градина & предучилищна група")
    catering = _row(school, page, "tuition", 6200, "yearly", "кетъринг в детската градина")
    weekly = _row(
        school,
        page,
        "registration",
        135,
        plan_name="седмична такса за гостуващи ученици във Валдорфското училище",
    )
    db_session.add_all([good, kindergarten, catering, weekly])
    await db_session.commit()

    result = await validator_module.validate_school_data(db_session, school.id, "bg")
    assert result["status"] == "needs_review"

    await db_session.refresh(school)
    assert _issue_codes(school, good) == set()
    assert _issue_codes(school, kindergarten) == {"pricing_label_names_other_level"}
    assert _issue_codes(school, catering) == {
        "pricing_amount_not_near_label",
        "pricing_label_names_other_level",
    }
    assert _issue_codes(school, weekly) == {"pricing_period_unrepresentable"}

    response = await client.get(f"/schools/{school.id}")
    assert [row["id"] for row in response.json()["pricing"]] == [good.id]


@pytest.mark.asyncio
async def test_525_deposit_is_withheld_and_stated_period_is_filled(db_session, client):
    school, page = await _school_with_page(
        db_session, level="kindergarten", website_url="https://slaveiche.com", page_text=SLAVEICHE
    )
    full_day = _row(school, page, "tuition", 530, notes="целодневно гледане")
    deposit = _row(school, page, "tuition", 265, notes="депозит за запазване на място")
    half_day = _row(school, page, "tuition", 350, notes="половин ден с включен обяд")
    db_session.add_all([full_day, deposit, half_day])
    await db_session.commit()

    await validator_module.validate_school_data(db_session, school.id, "bg")

    await db_session.refresh(school)
    rows = {
        row.id: row
        for row in (
            await db_session.execute(select(Pricing).where(Pricing.school_id == school.id))
        ).scalars()
    }
    for row in rows.values():
        await db_session.refresh(row)
    assert rows[full_day.id].period.value == "monthly"
    assert rows[half_day.id].period is None  # the page states none next to €350
    assert _issue_codes(school, deposit) == {"pricing_deposit_as_tuition"}
    fixes = school.attributes["data_validation"]["auto_fixes"]
    assert [fix["field_path"] for fix in fixes if fix["code"] == "pricing_period_from_text"] == [
        f"pricing[{full_day.id}].period"
    ]

    response = await client.get(f"/schools/{school.id}")
    published = {row["id"]: row["period"] for row in response.json()["pricing"]}
    assert published == {full_day.id: "monthly", half_day.id: None}


@pytest.mark.asyncio
async def test_rows_without_a_valid_page_are_left_to_the_evidence_gate(db_session):
    school, page = await _school_with_page(
        db_session, level="kindergarten", website_url="https://slaveiche.com", page_text=SLAVEICHE
    )
    page.is_valid = False
    deposit = _row(school, page, "tuition", 265, notes="депозит за запазване на място")
    db_session.add(deposit)
    await db_session.commit()

    result = await validator_module.validate_school_data(db_session, school.id, "bg")

    assert result["status"] == "ok"


_CORROBORATED = {"signals": ["website_domain_alias_match", "repeated_on_page_identity"]}


async def _maple_bear(db_session, school_id_name, url, level, display):
    school = School(
        name_i18n={"bg": school_id_name},
        country_code="bg",
        school_type="private",
        education_level=level,
        city="sofia",
        website_url=url,
        scrape_status="extracted",
        attributes={"display_name_i18n": {"bg": display, "en": display}, "display_name_evidence": _CORROBORATED},
    )
    db_session.add(school)
    await db_session.flush()
    return school


@pytest.mark.asyncio
async def test_556_kindergarten_with_the_schools_name_falls_back_to_its_registry_name(db_session, client):
    kindergarten = await _maple_bear(
        db_session,
        "Частна детска градина Мейпъл Беър",
        "https://sofia-kindergarten.maplebear.bg",
        "kindergarten",
        "Maple Bear Sofia",
    )
    school = await _maple_bear(
        db_session, "Частно основно училище Мейпъл Беър", "https://maplebear.bg", "primary", "Maple Bear Sofia"
    )
    await db_session.commit()

    # Either order: the school validated first does not change the outcome.
    await validator_module.validate_school_data(db_session, school.id, "bg")
    await validator_module.validate_school_data(db_session, kindergarten.id, "bg")

    await db_session.refresh(kindergarten)
    await db_session.refresh(school)
    assert display_name_blocked(kindergarten.attributes)
    assert not display_name_blocked(school.attributes)
    response = await client.get(f"/schools/{kindergarten.id}")
    assert response.json()["resolved_name_i18n"]["bg"] == "Частна детска градина Мейпъл Беър"
    response = await client.get(f"/schools/{school.id}")
    assert response.json()["resolved_name_i18n"]["en"] == "Maple Bear Sofia"


@pytest.mark.asyncio
async def test_two_schools_of_one_brand_may_share_a_name(db_session):
    """151/392: a basic school and a gymnasium both publish "Zlatarski International School"."""
    basic = await _maple_bear(
        db_session, "Частно основно училище Златарски", "https://zlatarskischool.org", "lower_secondary",
        "Zlatarski International School",
    )
    await _maple_bear(
        db_session, "Частна езикова гимназия Златарски", "https://zlatarskischool.org/gymnasium",
        "upper_secondary", "Zlatarski International School",
    )
    await db_session.commit()

    await validator_module.validate_school_data(db_session, basic.id, "bg")

    await db_session.refresh(basic)
    assert not display_name_blocked(basic.attributes)


@pytest.mark.asyncio
async def test_distinct_sibling_names_publish(db_session, client):
    kindergarten = await _maple_bear(
        db_session,
        "Частна детска градина Мейпъл Беър",
        "https://sofia-kindergarten.maplebear.bg",
        "kindergarten",
        "Maple Bear Kindergarten Sofia",
    )
    await _maple_bear(
        db_session, "Частно основно училище Мейпъл Беър", "https://maplebear.bg", "primary", "Maple Bear Sofia"
    )
    # The same name on another site is not a sibling's.
    await _maple_bear(
        db_session, "Частно училище Друго", "https://other.bg", "primary", "Maple Bear Kindergarten Sofia"
    )
    await db_session.commit()

    await validator_module.validate_school_data(db_session, kindergarten.id, "bg")

    await db_session.refresh(kindergarten)
    assert not display_name_blocked(kindergarten.attributes)
    response = await client.get(f"/schools/{kindergarten.id}")
    assert response.json()["resolved_name_i18n"]["en"] == "Maple Bear Kindergarten Sofia"


@pytest.mark.asyncio
async def test_earlier_year_fee_history_is_not_checked_against_the_new_page(db_session, client):
    """Extraction keeps last year's rows as history; the page now shows this year's fees."""
    school, page = await _school_with_page(
        db_session,
        level="kindergarten",
        website_url="https://history.bg",
        page_text="Такси 2026/2027:\nМесечна такса 530 евро",
    )
    history = _row(school, page, "tuition", 500, "monthly")
    history.academic_year = "2025/2026"
    current = _row(school, page, "tuition", 530, "monthly")
    current.academic_year = "2026/2027"
    invented = _row(school, page, "tuition", 999, "monthly")
    invented.academic_year = "2026/2027"
    db_session.add_all([history, current, invented])
    await db_session.commit()

    await validator_module.validate_school_data(db_session, school.id, "bg")

    await db_session.refresh(school)
    assert _issue_codes(school, history) == set()
    assert _issue_codes(school, invented) == {"pricing_amount_not_near_label"}
    response = await client.get(f"/schools/{school.id}")
    assert sorted(row["id"] for row in response.json()["pricing"]) == sorted([history.id, current.id])


@pytest.mark.asyncio
async def test_a_row_made_identical_by_the_period_fill_is_removed_in_the_same_run(db_session):
    school, page = await _school_with_page(
        db_session, level="kindergarten", website_url="https://dup.bg", page_text=SLAVEICHE
    )
    stated = _row(school, page, "tuition", 530, "monthly", notes="целодневно гледане")
    unstated = _row(school, page, "tuition", 530, notes="целодневно гледане")
    db_session.add_all([stated, unstated])
    await db_session.commit()

    await validator_module.validate_school_data(db_session, school.id, "bg")

    remaining = (
        await db_session.execute(select(Pricing.id).where(Pricing.school_id == school.id))
    ).scalars().all()
    assert remaining == [stated.id]
    await db_session.refresh(school)
    report = school.attributes["data_validation"]
    assert not any(fix["field_path"].startswith(f"pricing[{unstated.id}]") and fix["code"] != "duplicate_pricing_row_removed" for fix in report["auto_fixes"])


async def _teaches(db_session, school, *age_groups):
    from app.models import SchoolLocation, SchoolLocationAgeGroupShift

    location = SchoolLocation(
        school_id=school.id, address_i18n={"bg": "ул. Тест 1"}, lat=42.7, lng=23.3, is_primary=True
    )
    db_session.add(location)
    await db_session.flush()
    db_session.add_all(
        SchoolLocationAgeGroupShift(location_id=location.id, age_group=group, shift="full_day")
        for group in age_groups
    )
    await db_session.flush()


@pytest.mark.asyncio
async def test_a_gymnasium_keeps_the_fee_that_names_it_and_not_its_siblings_page(db_session, client):
    """565 is named in a fee line of the shared site; 550 read its sibling's section."""
    site = "https://tzarsimeon.test"
    label = "Годишна такса обучение / 5-7 клас и ЧГПНП „Асен Йорданов“"
    text = f"Такси\nГодишна такса обучение / ПК – 4 клас\n5670 евро\n{label}\n5773 евро\n"
    gymnasium, page = await _school_with_page(
        db_session,
        level="upper_secondary",
        website_url=site,
        page_text=text,
        name='"ЧАСТНА ГИМНАЗИЯ ПО ПРИРОДНИ НАУКИ "АСЕН ЙОРДАНОВ" ЕООД',
    )
    basic, _ = await _school_with_page(
        db_session,
        level="lower_secondary",
        website_url=site,
        page_text=text,
        name='"ЧАСТНО ОСНОВНО УЧИЛИЩЕ ЦАР СИМЕОН ВЕЛИКИ" ЕООД',
    )
    await _teaches(db_session, gymnasium, "grade_8_12")
    await _teaches(db_session, basic, "grade_1_4", "grade_5_7")
    siblings_section = SourcePage(
        school_id=gymnasium.id,
        scrape_type=ScrapeType.WEBSITE,
        source_url=f"{site}/chastno-osnovno-uchilishte/priem-petoklasnici",
        content_hash="y",
        page_category="pricing",
        is_valid=True,
        raw_markdown="Таксата за обучение е 8110 евро.",
    )
    db_session.add(siblings_section)
    await db_session.flush()
    own = _row(gymnasium, page, "tuition", 5773, plan_name=label)
    other_band = _row(gymnasium, page, "tuition", 5670, plan_name="Годишна такса обучение / ПК – 4 клас")
    from_sibling = _row(gymnasium, siblings_section, "tuition", 8110, plan_name="Таксата за обучение")
    from_sibling.academic_year = "2026/2027"
    # Fee history is not checked against the page text, but whose page it is still counts.
    last_year = _row(gymnasium, siblings_section, "food", 174, plan_name="Таксата за храна")
    last_year.academic_year = "2025/2026"
    db_session.add_all([own, other_band, from_sibling, last_year])
    await db_session.commit()

    await validator_module.validate_school_data(db_session, gymnasium.id, "bg")
    await db_session.refresh(gymnasium)

    assert _issue_codes(gymnasium, own) == set()
    assert _issue_codes(gymnasium, other_band) == {"pricing_label_names_other_level"}
    assert _issue_codes(gymnasium, from_sibling) == {"pricing_label_names_other_level"}
    assert _issue_codes(gymnasium, last_year) == {"pricing_label_names_other_level"}
    response = await client.get(f"/schools/{gymnasium.id}")
    assert [row["id"] for row in response.json()["pricing"]] == [own.id]
