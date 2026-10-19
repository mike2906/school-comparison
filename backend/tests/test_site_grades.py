"""Grade bands from the school's own site (parent trial, 2026-10-19: AAS "8-12", ЕСПА "1-12")."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models import School
from app.models.school import SchoolLocation, SchoolLocationAgeGroupShift
from app.scrapers.campus_sync import SITE_GRADES_ADDED_PREFIX, SITE_GRADES_HIDDEN_PREFIX, remove_website_campus_data
from app.scrapers.stages import site_grades as stage
from app.scrapers.stages.site_grades import fee_label_grades, planned_bands, site_grades


@pytest.mark.parametrize(
    "label, grades",
    [
        ("Kindergarten – Grade 5", {0, 1, 2, 3, 4, 5}),  # 506
        ("Pre-Kindergarten", {0}),
        ("Grade 9 – Grade 12", {9, 10, 11, 12}),
        ("ПУК – III клас", {0, 1, 2, 3}),  # 172
        ("Подготвителна група - 5 годишни", {0}),
        ("Начално училище", {1, 2, 3, 4}),
        # The intensive-language year before 8th grade is not the class before first.
        ("Подготвителна година", set()),
        ("Подготвителен клас", set()),
        (None, set()),
    ],
)
def test_fee_label_grades(label, grades):
    assert fee_label_grades(label) == grades


def _fee(category, age_group, plan_name=None):
    return SimpleNamespace(category=category, age_group=age_group, plan_name=plan_name, pricing_context={})


def test_site_grades_reads_tuition_admission_and_highest_grade():
    pricing = [
        _fee("tuition", "Pre-Kindergarten"),
        _fee("tuition", "Grade 6 – Grade 8"),
        _fee("extracurricular", "Grade 9 – Grade 12"),  # a club is not the grades taught
    ]
    glance = {"admission": [{"grade": 0, "grade_to": 2}, {"grade": None}], "highest_grade": 12}
    assert site_grades(pricing, glance) == ({0, 1, 2, 6, 7, 8}, 12)
    assert site_grades([], None) == (set(), None)


def test_planned_bands():
    # AAS: register says 8-12 (treaty school), the site names Pre-K to grade 12.
    assert planned_bands({"grade_8_12"}, {0, 1, 5, 9}, 12) == ({"preschool", "grade_1_4", "grade_5_7"}, set())
    # ЕСПА: licensed to 12, teaches to 7, nothing on the site names 8-12.
    register = {"preschool", "grade_1_4", "grade_5_7", "grade_8_12"}
    assert planned_bands(register, {0, 1, 7}, 7) == (set(), {"grade_8_12"})
    # A tuition row for 8th grade keeps the band whatever the highest grade says.
    assert planned_bands(register, {8}, 7) == (set(), set())
    # No highest grade stated: nothing is hidden.
    assert planned_bands(register, set(), None) == (set(), set())
    # A misread highest grade never takes every register band away (a gymnasium "to grade 7").
    assert planned_bands({"grade_8_12"}, set(), 7) == ({"grade_5_7"}, set())


async def _bands(db, school_id):
    location = (
        await db.execute(
            select(SchoolLocation)
            .options(selectinload(SchoolLocation.age_group_shifts))
            .where(SchoolLocation.school_id == school_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    return sorted(location.age_groups), sorted(location.location_tags or [])


async def test_stage_adds_hides_and_undoes(db_session):
    school = School(
        country_code="bg", city="sofia", name_i18n={"bg": "ЕСПА"}, school_type="private",
        education_level="upper_secondary", website_url="https://espa.bg/", scrape_status="navigated",
    )
    location = SchoolLocation(school=school, address_i18n={"bg": "ул. Градина 23"}, location_tags=[])
    location.age_group_shifts = [
        SchoolLocationAgeGroupShift(age_group=band) for band in ("grade_1_4", "grade_5_7", "grade_8_12")
    ]
    db_session.add(school)
    await db_session.commit()
    school_id = school.id

    with patch.object(stage, "site_grades", return_value=({0, 1, 7}, 7)):
        report = await stage.run_site_grades(db_session)
        assert report["changes"][0]["add"] == ["preschool"] and report["changes"][0]["hide"] == ["grade_8_12"]
        assert (await _bands(db_session, school_id))[0] == ["grade_1_4", "grade_5_7", "grade_8_12"]  # dry run

        await stage.run_site_grades(db_session, apply=True)
        assert await _bands(db_session, school_id) == (
            ["grade_1_4", "grade_5_7", "preschool"],
            [SITE_GRADES_HIDDEN_PREFIX + "grade_8_12", SITE_GRADES_ADDED_PREFIX + "preschool"],
        )
        # A second run has nothing to do.
        assert (await stage.run_site_grades(db_session, apply=True))["changes"] == []

    # The site stops naming those grades: the register's bands come back.
    with patch.object(stage, "site_grades", return_value=(set(), None)):
        await stage.run_site_grades(db_session, apply=True)
    assert await _bands(db_session, school_id) == (["grade_1_4", "grade_5_7", "grade_8_12"], [])


async def test_register_import_and_withholding_respect_the_site_bands(db_session):
    from app.schemas.scraping import DiscoveredLocation
    from app.scrapers.sources.base_adapter import BaseSourceAdapter

    class RegistryAdapter(BaseSourceAdapter):
        async def discover(self, limit=None, sample_ratio=0.0):
            return []

    school = School(
        country_code="bg", city="sofia", name_i18n={"bg": "AAS"}, school_type="international",
        education_level="upper_secondary", website_url="https://aas.bg/", scrape_status="navigated",
    )
    location = SchoolLocation(
        school=school, address_i18n={"bg": "Панчарево"},
        location_tags=[SITE_GRADES_ADDED_PREFIX + "grade_1_4", SITE_GRADES_HIDDEN_PREFIX + "preschool"],
    )
    location.age_group_shifts = [SchoolLocationAgeGroupShift(age_group=b) for b in ("grade_1_4", "grade_8_12")]
    db_session.add(school)
    await db_session.commit()
    school_id = school.id

    # The register states preschool and 8-12 again: the site's band stays, the hidden one stays hidden.
    incoming = DiscoveredLocation(address_i18n={"bg": "Панчарево"}, age_groups=["preschool", "grade_8_12"])
    await RegistryAdapter(db=db_session)._sync_source_location(location, incoming)
    await db_session.commit()
    assert (await _bands(db_session, school_id))[0] == ["grade_1_4", "grade_8_12"]

    # The website is withheld: its band goes, the hidden register band comes back.
    await remove_website_campus_data(db_session, school_id)
    await db_session.commit()
    assert await _bands(db_session, school_id) == (["grade_8_12", "preschool"], [])
