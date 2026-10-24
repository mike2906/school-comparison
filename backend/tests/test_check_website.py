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
        attributes={"validated_website_url": "https://drujba.org/", "data_validation": {"_schema_version": 1}},
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
    # The old extraction stays hidden by its status, its fee rows by their pages and the
    # dropped Stage 6 report: no marker, which only an old-pipeline extraction would clear.
    assert WEBSITE_DATA_WITHHELD_KEY not in school.attributes
    assert "data_validation" not in school.attributes
    assert school.attributes["website_candidate_method"] == "check_website_replace"
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
        scrape_status="extracted", attributes={"school_facts": {"facts": []}},
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
    # Facts read from the group's home page are on the same site: they go, not show.
    assert "school_facts" not in school.attributes


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


async def test_a_given_site_starts_clean(db_session):
    """A school whose old site failed validation keeps the withholding marker, which hid
    574's facts: storing a valid site drops it, with the old report and other sites' pages."""
    from app.models import SourcePage
    from app.models.scrape_log import ScrapeType
    from app.utils.website_data import WEBSITE_DATA_WITHHELD_KEY

    school = School(
        country_code="bg", city="sofia", name_i18n={"bg": "ЧДГ Веда"}, school_type="private",
        education_level="kindergarten", scrape_status="failed_validate",
        attributes={
            WEBSITE_DATA_WITHHELD_KEY: True, "data_validation": {"_schema_version": 1},
            "school_facts": {"facts": []}, "school_facts_published": {"facts": []},
            "website_name": {"name": "x"}, "social_links": {"facebook": "https://facebook.com/x"},
            "moe_town_code": "68134",
        },
    )
    db_session.add(school)
    await db_session.commit()
    old_page = SourcePage(
        school_id=school.id, scrape_type=ScrapeType.WEBSITE, source_url="https://vedabg.com/taksi/",
        content_hash="x", is_valid=True,
    )
    own_page = SourcePage(
        school_id=school.id, scrape_type=ScrapeType.WEBSITE, source_url="https://wedaschule.com/kontakti/",
        content_hash="y", is_valid=True,
    )
    db_session.add_all([old_page, own_page])
    await db_session.commit()

    update = AsyncMock()
    with patch.object(
        url_validator, "validate_school_url",
        AsyncMock(return_value=(ValidationResult.VALID, "https://wedaschule.com/", "match")),
    ), patch.object(url_validator, "_update_validation_result", update):
        await website_stage.run_validate_url(db_session, school.id, "bg", url="wedaschule.com", apply=True)

    await db_session.refresh(school)
    await db_session.refresh(old_page)
    await db_session.refresh(own_page)
    assert WEBSITE_DATA_WITHHELD_KEY not in school.attributes
    assert "data_validation" not in school.attributes
    assert school.attributes["website_candidate_method"] == "check_website"
    # What the marker hid goes too: a re-accepted URL must not show it unread.
    for key in ("school_facts", "school_facts_published", "website_name", "social_links"):
        assert key not in school.attributes
    assert school.attributes["moe_town_code"] == "68134"
    assert (old_page.is_valid, own_page.is_valid) == (False, True)
    assert update.await_args.args[2:4] == (ValidationResult.VALID, "https://wedaschule.com/")


async def test_a_section_replace_keeps_the_pages_under_the_section(db_session):
    """574's fee pages under /anika/ are its own: only the group's other pages go."""
    from app.models import SourcePage
    from app.models.scrape_log import ScrapeType

    school = School(
        country_code="bg", city="sofia", name_i18n={"bg": "ЧДГ Аника"}, school_type="private",
        education_level="kindergarten", website_url="https://novigradini.com/", scrape_status="extracted",
    )
    db_session.add(school)
    await db_session.commit()
    group = SourcePage(
        school_id=school.id, scrape_type=ScrapeType.WEBSITE, source_url="https://novigradini.com/pbk/ceni/",
        content_hash="x", is_valid=True,
    )
    own = SourcePage(
        school_id=school.id, scrape_type=ScrapeType.WEBSITE, source_url="https://novigradini.com/anika/ceni/",
        content_hash="y", is_valid=True,
    )
    db_session.add_all([group, own])
    await db_session.commit()
    with patch.object(
        url_validator, "validate_school_url",
        AsyncMock(return_value=(ValidationResult.VALID, "https://novigradini.com/anika/", "match")),
    ), patch.object(url_validator, "_update_validation_result", AsyncMock()):
        await website_stage.run_validate_url(
            db_session, school.id, "bg", url="https://novigradini.com/anika/", apply=True, replace=True, section=True
        )
    await db_session.refresh(group)
    await db_session.refresh(own)
    assert (group.is_valid, own.is_valid) == (False, True)


async def test_a_given_site_keeps_its_facts_when_nothing_was_withheld(db_session):
    """Re-checking a school's accepted site keeps the facts read from it."""
    school = School(
        country_code="bg", city="sofia", name_i18n={"bg": "ЧДГ Веда"}, school_type="private",
        education_level="kindergarten", scrape_status="validated", website_url="https://wedaschule.com/",
        attributes={"school_facts": {"facts": []}, "social_links": {"facebook": "https://facebook.com/x"}},
    )
    db_session.add(school)
    await db_session.commit()

    with patch.object(
        url_validator, "validate_school_url",
        AsyncMock(return_value=(ValidationResult.VALID, "https://wedaschule.com/", "match")),
    ), patch.object(url_validator, "_update_validation_result", AsyncMock()):
        await website_stage.run_validate_url(db_session, school.id, "bg", url="wedaschule.com", apply=True)

    await db_session.refresh(school)
    assert "school_facts" in school.attributes and "social_links" in school.attributes


async def test_an_alias_counts_in_the_check_and_is_kept_with_the_site(db_session):
    """303 "Детски оазис" calls itself Kinderoase on its site."""
    school = School(
        country_code="bg", city="sofia", name_i18n={"bg": '"ЧАСТНА ДЕТСКА ГРАДИНА ДЕТСКИ ОАЗИС" ЕООД'},
        school_type="private", education_level="kindergarten", scrape_status="no_official_website",
        attributes={"name_aliases": ["Детски оазис"]},
    )
    db_session.add(school)
    await db_session.commit()

    validate = AsyncMock(return_value=(ValidationResult.VALID, "https://kinderoase.eu/", "match"))
    with patch.object(url_validator, "validate_school_url", validate), patch.object(
        url_validator, "_update_validation_result", AsyncMock()
    ):
        await website_stage.run_validate_url(
            db_session, school.id, "bg", url="https://kinderoase.eu/", apply=True, aliases=("Kinderoase", "детски ОАЗИС")
        )

    assert "Kinderoase" in validate.await_args.kwargs["school_aliases"]
    await db_session.refresh(school)
    assert school.attributes["name_aliases"] == ["Детски оазис", "Kinderoase"]
