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
