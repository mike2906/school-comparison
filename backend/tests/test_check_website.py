"""A school's real site given by hand (256's ou115.com, which search does not find)."""

from unittest.mock import AsyncMock, patch

import pytest

from app.models import School
from app.scrapers import url_validator
from app.scrapers.stages import website as website_stage
from app.scrapers.url_validator import ValidationResult


@pytest.mark.parametrize(
    "verdict,apply,stored",
    [
        (ValidationResult.VALID, True, True),
        (ValidationResult.VALID, False, False),  # a dry run writes nothing
        (ValidationResult.INVALID, True, False),  # a wrong guess never withholds the current site
    ],
)
async def test_a_given_site_is_stored_only_when_valid(db_session, verdict, apply, stored):
    school = School(country_code="bg", city="sofia", name_i18n={"bg": "115 ОУ"}, school_type="state",
        education_level="primary",
    )
    db_session.add(school)
    await db_session.commit()

    validate = AsyncMock(return_value=(verdict, "https://ou115.com/", "checked"))
    update = AsyncMock()
    with patch.object(url_validator, "validate_school_url", validate), patch.object(
        url_validator, "_update_validation_result", update
    ):
        await website_stage.run_validate_url(db_session, school.id, "bg", url="ou115.com", apply=apply)

    assert validate.await_args.kwargs["update_db"] is False
    assert validate.await_args.kwargs["school_city"] == "sofia"
    assert update.await_count == (1 if stored else 0)
    if stored:
        assert update.await_args.args[2:4] == (ValidationResult.VALID, "https://ou115.com/")


async def test_a_given_site_does_not_replace_an_accepted_one(db_session):
    school = School(
        country_code="bg", city="sofia", name_i18n={"bg": "115 ОУ"}, school_type="state", education_level="primary",
        website_url="https://ou115.com/", scrape_status="extracted",
    )
    db_session.add(school)
    await db_session.commit()
    validate = AsyncMock()
    with patch.object(url_validator, "validate_school_url", validate):
        assert await website_stage.run_validate_url(db_session, school.id, "bg", url="https://ou115.com/", apply=True) is None
    validate.assert_not_awaited()


async def test_a_given_site_without_a_final_url_is_stored_normalised(db_session):
    school = School(
        country_code="bg", city="sofia", name_i18n={"bg": "115 ОУ"}, school_type="state", education_level="primary",
        scrape_status="failed_validate",
    )
    db_session.add(school)
    await db_session.commit()
    update = AsyncMock()
    with patch.object(
        url_validator, "validate_school_url", AsyncMock(return_value=(ValidationResult.VALID, None, "ok"))
    ), patch.object(url_validator, "_update_validation_result", update):
        await website_stage.run_validate_url(db_session, school.id, "bg", url="ou115.com", apply=True)
    assert update.await_args.args[1] == update.await_args.args[3] == "https://ou115.com"


async def test_replace_swaps_an_accepted_site_and_drops_what_it_gave(db_session):
    """587 (a kindergarten) had its sibling school's drujba.org; its own site is dgs.drujba.org."""
    from app.models import SourcePage
    from app.models.scrape_log import ScrapeType
    from app.utils.website_data import WEBSITE_DATA_WITHHELD_KEY

    school = School(
        country_code="bg", city="sofia", name_i18n={"bg": "ЧДГ Дружба-София"}, school_type="private",
        education_level="kindergarten", website_url="https://drujba.org/", scrape_status="extracted",
        attributes={"validated_website_url": "https://drujba.org/"},
    )
    db_session.add(school)
    await db_session.commit()
    old_page = SourcePage(
        school_id=school.id, scrape_type=ScrapeType.WEBSITE, source_url="https://drujba.org/transport/",
        content_hash="x", is_valid=True,
    )
    db_session.add(old_page)
    await db_session.commit()

    update = AsyncMock()
    with patch.object(
        url_validator, "validate_school_url",
        AsyncMock(return_value=(ValidationResult.VALID, "https://dgs.drujba.org/", "match")),
    ), patch.object(url_validator, "_update_validation_result", update):
        await website_stage.run_validate_url(
            db_session, school.id, "bg", url="https://dgs.drujba.org/", apply=True, replace=True
        )

    await db_session.refresh(school)
    await db_session.refresh(old_page)
    assert school.website_url == "https://dgs.drujba.org/"
    assert school.scrape_status == "pending"  # validation then moves it to "validated"
    assert school.attributes["website_replaced_url"] == "https://drujba.org/"
    assert "shared_site_check" not in school.attributes  # no shared-site decision was made
    assert school.attributes[WEBSITE_DATA_WITHHELD_KEY] is True  # the old extraction stays hidden
    assert "validated_website_url" not in school.attributes
    assert old_page.is_valid is False  # its fees no longer publish
    assert update.await_count == 1


async def test_replace_never_drops_the_old_site_for_an_invalid_one(db_session):
    school = School(
        country_code="bg", city="sofia", name_i18n={"bg": "ЧДГ Дружба-София"}, school_type="private",
        education_level="kindergarten", website_url="https://drujba.org/", scrape_status="extracted",
    )
    db_session.add(school)
    await db_session.commit()
    with patch.object(
        url_validator, "validate_school_url", AsyncMock(return_value=(ValidationResult.INVALID, None, "no match"))
    ):
        await website_stage.run_validate_url(
            db_session, school.id, "bg", url="https://dgs.drujba.org/", apply=True, replace=True
        )
    await db_session.refresh(school)
    assert (school.website_url, school.scrape_status) == ("https://drujba.org/", "extracted")


async def test_replace_with_the_same_site_changes_nothing(db_session):
    school = School(
        country_code="bg", city="sofia", name_i18n={"bg": "ЧДГ Дружба-София"}, school_type="private",
        education_level="kindergarten", website_url="https://drujba.org/", scrape_status="extracted",
    )
    db_session.add(school)
    await db_session.commit()
    validate = AsyncMock()
    with patch.object(url_validator, "validate_school_url", validate):
        assert await website_stage.run_validate_url(
            db_session, school.id, "bg", url="drujba.org/bg", apply=True, replace=True
        ) is None
    validate.assert_not_awaited()


async def test_section_replaces_a_group_home_with_the_schools_own_section(db_session):
    """574's stored site was its group's home page; its own pages are under /anika/."""
    school = School(
        country_code="bg", city="sofia", name_i18n={"bg": "ЧДГ Аника"}, school_type="private",
        education_level="kindergarten", website_url="https://novigradini.com/#dipipopup-27586290#popup_on_load",
        scrape_status="extracted",
    )
    db_session.add(school)
    await db_session.commit()
    update = AsyncMock()
    with patch.object(
        url_validator, "validate_school_url",
        AsyncMock(return_value=(ValidationResult.VALID, "https://novigradini.com/anika/", "match")),
    ), patch.object(url_validator, "_update_validation_result", update):
        await website_stage.run_validate_url(
            db_session, school.id, "bg", url="https://novigradini.com/anika/", apply=True, replace=True, section=True
        )
    await db_session.refresh(school)
    assert school.website_url == "https://novigradini.com/anika/"
    assert update.await_count == 1


async def test_section_with_the_same_path_changes_nothing(db_session):
    school = School(
        country_code="bg", city="sofia", name_i18n={"bg": "ЧДГ Приказка без край"}, school_type="private",
        education_level="kindergarten", website_url="https://novigradini.com/pbk/#dipipopup-27586290",
        scrape_status="extracted",
    )
    db_session.add(school)
    await db_session.commit()
    validate = AsyncMock()
    with patch.object(url_validator, "validate_school_url", validate):
        assert await website_stage.run_validate_url(
            db_session, school.id, "bg", url="novigradini.com/pbk", apply=True, replace=True, section=True
        ) is None
    validate.assert_not_awaited()
