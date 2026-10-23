"""Private schools with no website get one searched for (find-websites, 2026-10-23: 33 of 174 had none)."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from app.models import School
from app.scrapers import url_validator
from app.scrapers.stages import find_websites
from app.scrapers.url_validator import URLValidator, ValidationResult
from app.scrapers.website_discovery import WebsiteDiscoverer

NOW = datetime(2026, 11, 1, 2, 20, tzinfo=timezone.utc)


def _school(**extra):
    values = dict(
        country_code="bg", city="sofia", name_i18n={"bg": '"ЧДГ "ЯН БИБИЯН" - МЕГА-Х" ООД'}, school_type="private",
        education_level="kindergarten", scrape_status="no_official_website",
    )
    return School(**{**values, **extra})


async def _run(db_session, verdicts, candidates=("https://janbibijan.com/",), **options):
    validate = AsyncMock(side_effect=verdicts)
    store = AsyncMock()
    with patch.object(WebsiteDiscoverer, "_search_candidates", AsyncMock(return_value=list(candidates))), patch.object(
        URLValidator, "validate_url", validate
    ), patch.object(url_validator, "_update_validation_result", store), patch(
        "app.scrapers.stages.website.check_shared_sites", AsyncMock()
    ):
        report = await find_websites.run_find_websites(db_session, now=NOW, **options)
    return report, validate, store


async def test_a_dry_run_searches_and_writes_nothing(db_session):
    school = _school()
    db_session.add(school)
    await db_session.commit()
    report, validate, store = await _run(db_session, [(ValidationResult.VALID, "https://janbibijan.com/", "match")])
    assert report["found"] == 1 and report["schools"][0]["found"] == "https://janbibijan.com/"
    assert validate.await_args.kwargs["use_llm_fallback"] is False  # a run costs nothing
    store.assert_not_awaited()
    await db_session.refresh(school)
    assert school.website_url is None and find_websites.SEARCHED_AT_KEY not in (school.attributes or {})


async def test_apply_stores_the_first_valid_candidate_and_records_the_search(db_session):
    school = _school(attributes={"website_data_withheld": True})
    db_session.add(school)
    await db_session.commit()
    report, validate, store = await _run(
        db_session,
        [(ValidationResult.INVALID, None, "Directory-like listing"), (ValidationResult.VALID, None, "match")],
        candidates=("https://yanbibiyan-mega-x.com/", "https://janbibijan.com/"),
        apply=True,
    )
    tried = [entry["result"] for entry in report["schools"][0]["tried"]]
    assert tried[-1] == "valid" and report["found"] == 1
    assert store.await_args.args[2:4] == (ValidationResult.VALID, report["schools"][0]["found"])
    await db_session.refresh(school)
    assert school.attributes[find_websites.SEARCHED_AT_KEY] == NOW.isoformat()
    assert school.attributes["website_candidate_method"] == "search"
    assert "website_data_withheld" not in school.attributes  # it starts clean (store_given_site)


async def test_nothing_valid_stores_nothing_but_records_the_search(db_session):
    school = _school()
    db_session.add(school)
    await db_session.commit()
    report, _, store = await _run(db_session, [(ValidationResult.AMBIGUOUS, None, "only 1 keyword")], apply=True)
    assert report["found"] == 0
    store.assert_not_awaited()
    await db_session.refresh(school)
    assert school.website_url is None and school.attributes[find_websites.SEARCHED_AT_KEY] == NOW.isoformat()


async def test_a_school_searched_lately_waits_and_state_schools_are_left_out(db_session):
    recent = _school(attributes={find_websites.SEARCHED_AT_KEY: (NOW - timedelta(days=30)).isoformat()})
    old = _school(attributes={find_websites.SEARCHED_AT_KEY: (NOW - timedelta(days=120)).isoformat()})
    state = _school(school_type="state", name_i18n={"bg": "ДГ 59"})
    has_site = _school(website_url="https://janbibijan.com/", scrape_status="validated")
    db_session.add_all([recent, old, state, has_site])
    await db_session.commit()
    report, _, _ = await _run(db_session, [(ValidationResult.INVALID, None, "no")] * 3)
    assert report["schools_without_site"] == 2  # the private ones without a site
    assert [school["school_id"] for school in report["schools"]] == [old.id]
    # Named ids are searched whatever their type or last search.
    report, _, _ = await _run(db_session, [(ValidationResult.INVALID, None, "no")] * 3, school_ids=[recent.id, state.id])
    assert sorted(school["school_id"] for school in report["schools"]) == sorted([recent.id, state.id])


async def test_one_schools_failure_does_not_stop_the_run(db_session):
    first, second = _school(), _school()
    db_session.add_all([first, second])
    await db_session.commit()
    report, _, _ = await _run(
        db_session,
        [RuntimeError("site broke"), (ValidationResult.VALID, None, "match")],
        apply=True,
    )
    assert [("error" in school, bool(school.get("found"))) for school in report["schools"]] == [(True, False), (False, True)]
    await db_session.refresh(first)
    assert find_websites.SEARCHED_AT_KEY not in (first.attributes or {})  # searched again next time
