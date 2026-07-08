"""Tests for scraper CLI batch helpers."""

from __future__ import annotations

import asyncio
import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.models import School, SchoolLocation, SchoolLocationAgeGroupShift, SourcePage
from app.models.scrape_log import ScrapeType
from app.scrapers import cli as scraper_cli
from app.scrapers.url_validator import ValidationResult
from app.utils.transliteration import transliterate_address, transliterate_bulgarian


@pytest.mark.asyncio
async def test_load_moe_lookup_maps_tolerates_non_json_response():
    class MockResponse:
        def raise_for_status(self):
            return None

        def json(self):
            raise ValueError("not json")

    class MockClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, *args, **kwargs):
            return MockResponse()

    with patch("httpx.AsyncClient", return_value=MockClient()):
        assert await scraper_cli._load_moe_lookup_maps() == ({}, {}, {})


def test_select_brand_aliases_for_school_accepts_host_aligned_brand():
    school = School(
        name_i18n={"bg": "Тест"},
        country_code="bg",
        school_type="private",
        education_level="kindergarten",
        website_url="https://britanica-parkschool.bg/",
    )

    aliases = scraper_cli._select_brand_aliases_for_school(
        {"bg": "BRITANICA Park School", "en": "BRITANICA Park School"},
        "https://britanica-parkschool.bg/",
        school,
    )

    assert aliases == ["BRITANICA Park School"]


def test_out_of_bounds_repair_address_candidates_prefer_extracted_contact():
    school = School(
        name_i18n={"bg": "Тест"},
        country_code="bg",
        school_type="private",
        education_level="kindergarten",
        city="sofia",
        attributes={
            "extracted": {
                "contact": {
                    "address": "ул. Гео Милев 158",
                    "addresses": ["ул. 6 септември №16, София, България, ПК 1000"],
                }
            }
        },
    )
    location = SchoolLocation(
        school_id=1,
        address_i18n={"bg": 'ж. к. Слатина, ул. "Гео Милев" № 158, ВТУ "Т. Каблешков", корпус 6'},
    )

    candidates = scraper_cli._out_of_bounds_repair_address_candidates(school, location)

    assert candidates[0] == "ул. Гео Милев 158"
    assert "ул. 6 септември №16, София, България" in candidates
    assert candidates[-1] == 'ж. к. Слатина, ул. "Гео Милев" № 158, ВТУ "Т. Каблешков", корпус 6'


def test_select_brand_aliases_for_school_rejects_noisy_aliases():
    school = School(
        name_i18n={"bg": "Тест"},
        country_code="bg",
        school_type="private",
        education_level="primary",
        website_url="https://espa.bg/",
    )

    aliases = scraper_cli._select_brand_aliases_for_school(
        {"bg": "ЕСПА", "en": "Google Reference School"},
        "https://espa.bg/",
        school,
    )

    assert aliases == ["ЕСПА"]


def test_select_brand_aliases_for_school_skips_state_schools():
    school = School(
        name_i18n={"bg": "142 ОСНОВНО УЧИЛИЩЕ"},
        country_code="bg",
        school_type="state",
        education_level="primary",
        website_url="https://142ou.com/",
    )

    aliases = scraper_cli._select_brand_aliases_for_school(
        {"bg": "142 ОУ", "en": "142 School"},
        "https://142ou.com/",
        school,
    )

    assert aliases == []


def test_infer_age_group_evidence_from_source_pages_detects_all_through_school_signals():
    pages = [
        SourcePage(
            school_id=1,
            scrape_type=ScrapeType.WEBSITE,
            source_url="https://21su.bg/priem/predutchilishtni-grupi",
            content_hash="a" * 64,
            raw_markdown="## Предучилищни групи\nКритерии за прием",
        ),
        SourcePage(
            school_id=1,
            scrape_type=ScrapeType.WEBSITE,
            source_url="https://21su.bg/priem/parvi-klas",
            content_hash="b" * 64,
            raw_markdown="## Първи клас\nПодаване на заявления",
        ),
        SourcePage(
            school_id=1,
            scrape_type=ScrapeType.WEBSITE,
            source_url="https://21su.bg/obuchenie/klasni-rakovoditeli",
            content_hash="c" * 64,
            raw_markdown="""
            # Класни ръководители
            3. група | Вергиния Николова
            4. група | Елка Вълкова
            1 а | Дорина Христова
            5 а | Стефани Витанова
            8 а | Андреа Иванова
            """,
        ),
    ]

    evidence = scraper_cli._infer_age_group_evidence_from_source_pages(pages)

    assert set(evidence) == {"preschool", "grade_1_4", "grade_5_7", "grade_8_12"}


def test_should_refresh_age_group_navigation_for_upper_secondary_missing_primary_stages():
    school = School(
        name_i18n={"bg": '21 СРЕДНО УЧИЛИЩЕ "ХРИСТО БОТЕВ"'},
        country_code="bg",
        school_type="state",
        education_level="upper_secondary",
        city="sofia",
        website_url="https://21su.bg",
    )
    school.locations = [
        SchoolLocation(
            school_id=1,
            is_primary=True,
            age_group_shifts=[
                SchoolLocationAgeGroupShift(location_id=1, age_group="grade_5_7"),
                SchoolLocationAgeGroupShift(location_id=1, age_group="grade_8_12"),
            ],
        )
    ]

    assert scraper_cli._should_refresh_age_group_navigation(school) is True


def test_should_refresh_age_group_navigation_skips_complete_all_through_school():
    school = School(
        name_i18n={"bg": '96. Средно училище "Лев Николаевич Толстой"'},
        country_code="bg",
        school_type="state",
        education_level="upper_secondary",
        city="sofia",
        website_url="https://96sou.com",
    )
    school.locations = [
        SchoolLocation(
            school_id=1,
            is_primary=True,
            age_group_shifts=[
                SchoolLocationAgeGroupShift(location_id=1, age_group="preschool"),
                SchoolLocationAgeGroupShift(location_id=1, age_group="grade_1_4"),
                SchoolLocationAgeGroupShift(location_id=1, age_group="grade_5_7"),
                SchoolLocationAgeGroupShift(location_id=1, age_group="grade_8_12"),
            ],
        )
    ]

    assert scraper_cli._should_refresh_age_group_navigation(school) is False


def test_allowed_age_group_repairs_for_kindergarten_excludes_school_grades():
    school = School(
        name_i18n={"bg": '"ЧАСТНА ДЕТСКА ГРАДИНА МЕРИДИАН 22" ЕООД'},
        country_code="bg",
        school_type="private",
        education_level="kindergarten",
        city="sofia",
        website_url="https://meridian22.bg",
    )

    assert scraper_cli._allowed_age_group_repairs_for_school(school) == {"preschool"}


def test_allowed_age_group_repairs_for_lower_secondary_excludes_grade_8_12():
    school = School(
        name_i18n={"bg": '124 ОСНОВНО УЧИЛИЩЕ "ВАСИЛ ЛЕВСКИ"'},
        country_code="bg",
        school_type="state",
        education_level="lower_secondary",
        city="sofia",
        website_url="https://124-ou.com",
    )

    assert scraper_cli._allowed_age_group_repairs_for_school(school) == {
        "preschool",
        "grade_1_4",
        "grade_5_7",
    }


@pytest.mark.asyncio
async def test_repair_age_groups_command_adds_missing_groups_from_source_pages(db_session):
    school = School(
        name_i18n={"bg": '21 СРЕДНО УЧИЛИЩЕ "ХРИСТО БОТЕВ"'},
        country_code="bg",
        school_type="state",
        education_level="upper_secondary",
        city="sofia",
        website_url="https://21su.bg",
        scrape_status="navigated",
    )
    db_session.add(school)
    await db_session.flush()

    location = SchoolLocation(
        school_id=school.id,
        address_i18n={"bg": "ул. Люботрън 12"},
        lat=42.66915,
        lng=23.31543,
        is_primary=True,
    )
    db_session.add(location)
    await db_session.flush()
    db_session.add_all(
        [
            SchoolLocationAgeGroupShift(location_id=location.id, age_group="grade_5_7"),
            SchoolLocationAgeGroupShift(location_id=location.id, age_group="grade_8_12"),
        ]
    )
    db_session.add_all(
        [
            SourcePage(
                school_id=school.id,
                scrape_type=ScrapeType.WEBSITE,
                source_url="https://21su.bg/priem/predutchilishtni-grupi",
                content_hash="d" * 64,
                raw_markdown="## Предучилищни групи\nКритерии за прием",
            ),
            SourcePage(
                school_id=school.id,
                scrape_type=ScrapeType.WEBSITE,
                source_url="https://21su.bg/priem/parvi-klas",
                content_hash="e" * 64,
                raw_markdown="## Първи клас\nПодаване на заявления",
            ),
        ]
    )
    await db_session.commit()

    class SessionCtx:
        async def __aenter__(self):
            return db_session

        async def __aexit__(self, exc_type, exc, tb):
            return False

    with patch("app.database.async_session_maker", return_value=SessionCtx()):
        await scraper_cli._repair_age_groups_command(
            school_name=None,
            school_id=school.id,
            city="sofia",
            country="bg",
            limit=None,
            dry_run=False,
            refresh_navigation=False,
        )

    refreshed = (
        await db_session.get(SchoolLocation, location.id)
    )
    age_groups = {
        row.age_group
        for row in (await db_session.execute(
            select(SchoolLocationAgeGroupShift).where(
                SchoolLocationAgeGroupShift.location_id == location.id
            )
        )).scalars().all()
    }
    assert refreshed is not None
    assert age_groups == {"preschool", "grade_1_4", "grade_5_7", "grade_8_12"}


@pytest.mark.asyncio
async def test_repair_age_groups_command_ignores_disallowed_kindergarten_school_grades(db_session):
    school = School(
        name_i18n={"bg": '"ЧАСТНА ДЕТСКА ГРАДИНА МЕРИДИАН 22" ЕООД'},
        country_code="bg",
        school_type="private",
        education_level="kindergarten",
        city="sofia",
        website_url="https://meridian22.bg",
        scrape_status="navigated",
    )
    db_session.add(school)
    await db_session.flush()

    location = SchoolLocation(
        school_id=school.id,
        address_i18n={"bg": "ул. Примерна 1"},
        is_primary=True,
    )
    db_session.add(location)
    await db_session.flush()
    db_session.add(
        SchoolLocationAgeGroupShift(location_id=location.id, age_group="preschool")
    )
    db_session.add(
        SourcePage(
            school_id=school.id,
            scrape_type=ScrapeType.WEBSITE,
            source_url="https://meridian22.bg/priem/parvi-klas",
            content_hash="f" * 64,
            raw_markdown="## Първи клас\nПодаване на заявления",
        )
    )
    await db_session.commit()

    class SessionCtx:
        async def __aenter__(self):
            return db_session

        async def __aexit__(self, exc_type, exc, tb):
            return False

    with patch("app.database.async_session_maker", return_value=SessionCtx()):
        await scraper_cli._repair_age_groups_command(
            school_name=None,
            school_id=school.id,
            city="sofia",
            country="bg",
            limit=None,
            dry_run=False,
            refresh_navigation=False,
        )

    age_groups = {
        row.age_group
        for row in (
            await db_session.execute(
                select(SchoolLocationAgeGroupShift).where(
                    SchoolLocationAgeGroupShift.location_id == location.id
                )
            )
        ).scalars().all()
    }
    assert age_groups == {"preschool"}


@pytest.mark.asyncio
async def test_repair_age_groups_command_ignores_disallowed_lower_secondary_grade_8_12(db_session):
    school = School(
        name_i18n={"bg": '124 ОСНОВНО УЧИЛИЩЕ "ВАСИЛ ЛЕВСКИ"'},
        country_code="bg",
        school_type="state",
        education_level="lower_secondary",
        city="sofia",
        website_url="https://124-ou.com",
        scrape_status="navigated",
    )
    db_session.add(school)
    await db_session.flush()

    location = SchoolLocation(
        school_id=school.id,
        address_i18n={"bg": "ул. Примерна 2"},
        is_primary=True,
    )
    db_session.add(location)
    await db_session.flush()
    db_session.add_all(
        [
            SchoolLocationAgeGroupShift(location_id=location.id, age_group="grade_1_4"),
            SchoolLocationAgeGroupShift(location_id=location.id, age_group="grade_5_7"),
        ]
    )
    db_session.add(
        SourcePage(
            school_id=school.id,
            scrape_type=ScrapeType.WEBSITE,
            source_url="https://124-ou.com/priem/sled-sedmi-klas",
            content_hash="g" * 64,
            raw_markdown="## След 7 клас\nБалообразуване",
        )
    )
    await db_session.commit()

    class SessionCtx:
        async def __aenter__(self):
            return db_session

        async def __aexit__(self, exc_type, exc, tb):
            return False

    with patch("app.database.async_session_maker", return_value=SessionCtx()):
        await scraper_cli._repair_age_groups_command(
            school_name=None,
            school_id=school.id,
            city="sofia",
            country="bg",
            limit=None,
            dry_run=False,
            refresh_navigation=False,
        )

    age_groups = {
        row.age_group
        for row in (
            await db_session.execute(
                select(SchoolLocationAgeGroupShift).where(
                    SchoolLocationAgeGroupShift.location_id == location.id
                )
            )
        ).scalars().all()
    }
    assert age_groups == {"grade_1_4", "grade_5_7"}


@pytest.mark.asyncio
async def test_repair_age_groups_refresh_navigation_only_targets_likely_candidates(db_session):
    candidate = School(
        name_i18n={"bg": '21 СРЕДНО УЧИЛИЩЕ "ХРИСТО БОТЕВ"'},
        country_code="bg",
        school_type="state",
        education_level="upper_secondary",
        city="sofia",
        website_url="https://21su.bg",
        scrape_status="navigated",
    )
    complete = School(
        name_i18n={"bg": '96. Средно училище "Лев Николаевич Толстой"'},
        country_code="bg",
        school_type="state",
        education_level="upper_secondary",
        city="sofia",
        website_url="https://96sou.com",
        scrape_status="navigated",
    )
    db_session.add_all([candidate, complete])
    await db_session.flush()

    candidate_location = SchoolLocation(
        school_id=candidate.id,
        address_i18n={"bg": "ул. Люботрън 12"},
        is_primary=True,
    )
    complete_location = SchoolLocation(
        school_id=complete.id,
        address_i18n={"bg": "ул. Толстой 1"},
        is_primary=True,
    )
    db_session.add_all([candidate_location, complete_location])
    await db_session.flush()

    db_session.add_all(
        [
            SchoolLocationAgeGroupShift(location_id=candidate_location.id, age_group="grade_5_7"),
            SchoolLocationAgeGroupShift(location_id=candidate_location.id, age_group="grade_8_12"),
            SchoolLocationAgeGroupShift(location_id=complete_location.id, age_group="preschool"),
            SchoolLocationAgeGroupShift(location_id=complete_location.id, age_group="grade_1_4"),
            SchoolLocationAgeGroupShift(location_id=complete_location.id, age_group="grade_5_7"),
            SchoolLocationAgeGroupShift(location_id=complete_location.id, age_group="grade_8_12"),
        ]
    )
    await db_session.commit()

    class SessionCtx:
        async def __aenter__(self):
            return db_session

        async def __aexit__(self, exc_type, exc, tb):
            return False

    navigate_mock = AsyncMock()

    with (
        patch("app.database.async_session_maker", return_value=SessionCtx()),
        patch.object(scraper_cli, "_run_navigate_batch", new=navigate_mock),
    ):
        await scraper_cli._repair_age_groups_command(
            school_name=None,
            school_id=None,
            city="sofia",
            country="bg",
            limit=None,
            dry_run=False,
            refresh_navigation=True,
        )

    navigate_mock.assert_awaited_once()
    assert navigate_mock.await_args.kwargs["school_ids"] == [candidate.id]


@pytest.mark.asyncio
async def test_run_recover_failed_school_delegates_to_discoverer_public_api(db_session):
    school = School(
        name_i18n={"bg": "Тест училище"},
        country_code="bg",
        school_type="state",
        education_level="primary",
        city="sofia",
        website_url="https://broken.bg",
        scrape_status="failed_validate",
    )
    db_session.add(school)
    await db_session.commit()

    expected = {
        "school_id": school.id,
        "status": "validated",
        "validation_result": "valid",
        "attempts": 1,
    }
    with patch(
        "app.scrapers.website_discovery.WebsiteDiscoverer.recover_failed_school",
        new=AsyncMock(return_value=expected),
    ) as recover_mock:
        out = await scraper_cli._run_recover_failed_school(db_session, school.id, "bg")

    assert out == expected
    recover_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_validate_urls_batch_caps_configured_concurrency(db_session):
    school = School(
        name_i18n={"bg": "Тест училище"},
        country_code="bg",
        school_type="state",
        education_level="primary",
        city="sofia",
        website_url="https://test-school.bg",
        scrape_status="pending",
    )
    db_session.add(school)
    await db_session.commit()

    settings = SimpleNamespace(url_validation_concurrency=50, url_validation_max_concurrency=3)
    captured_semaphore_values: list[int] = []
    real_semaphore = asyncio.Semaphore

    def semaphore_spy(value: int):
        captured_semaphore_values.append(value)
        return real_semaphore(value)

    with (
        patch("app.config.get_settings", return_value=settings),
        patch(
            "app.scrapers.url_validator.validate_school_url",
            new=AsyncMock(return_value=(ValidationResult.VALID, "https://test-school.bg", "ok")),
        ),
        patch("app.scrapers.cli.asyncio.Semaphore", side_effect=semaphore_spy),
    ):
        await scraper_cli._run_validate_urls_batch(
            db=db_session,
            country="bg",
            city="sofia",
            limit=None,
            statuses=["pending"],
        )

    assert captured_semaphore_values[0] == 3


@pytest.mark.asyncio
async def test_run_recover_failed_urls_batch_uses_isolated_session_helper(db_session):
    for idx in range(3):
        db_session.add(
            School(
                name_i18n={"bg": f"Тест {idx}"},
                country_code="bg",
                school_type="state",
                education_level="primary",
                city="sofia",
                website_url="https://broken.bg",
                scrape_status="failed_validate",
            )
        )
    await db_session.commit()

    settings = SimpleNamespace(url_recovery_concurrency=20, url_validation_max_concurrency=4)
    recover_mock = AsyncMock(
        side_effect=[
            {"status": "validated"},
            {"terminal": True, "status": "no_official_website"},
            {"status": "failed_validate"},
        ]
    )
    real_semaphore = asyncio.Semaphore
    captured_semaphore_values: list[int] = []

    def semaphore_spy(value: int):
        captured_semaphore_values.append(value)
        return real_semaphore(value)

    with (
        patch("app.config.get_settings", return_value=settings),
        patch("app.scrapers.cli._recover_failed_school_with_new_session", new=recover_mock),
        patch("app.scrapers.cli.asyncio.Semaphore", side_effect=semaphore_spy),
    ):
        await scraper_cli._run_recover_failed_urls_batch(
            db=db_session,
            country="bg",
            city="sofia",
            limit=None,
        )

    assert recover_mock.await_count == 3
    assert captured_semaphore_values[0] == 4


@pytest.mark.asyncio
async def test_repair_i18n_command_clears_synthetic_values_and_reextracts_eligible_schools(db_session):
    bg_name = "Частно основно училище Фюжън ЕООД"
    bg_address = "ул. Иван Вазов 15, София"
    school = School(
        name_i18n={"bg": bg_name, "en": transliterate_bulgarian(bg_name)},
        country_code="bg",
        school_type="private",
        education_level="primary",
        city="sofia",
        website_url="https://fusion.example",
        scrape_status="extracted",
    )
    db_session.add(school)
    await db_session.flush()

    location = SchoolLocation(
        school_id=school.id,
        address_i18n={"bg": bg_address, "en": transliterate_address(bg_address)},
        is_primary=True,
    )
    db_session.add(location)
    db_session.add(
        SourcePage(
            school_id=school.id,
            source_url="https://fusion.example/about",
            page_category="about",
            scrape_type=ScrapeType.WEBSITE,
            is_valid=True,
            raw_markdown="Fusion School",
            content_hash="hash-fusion",
            last_scraped_at=datetime.datetime.now(datetime.UTC),
        )
    )
    await db_session.commit()

    class SessionCtx:
        async def __aenter__(self):
            return db_session

        async def __aexit__(self, exc_type, exc, tb):
            return False

    extract_batch_mock = AsyncMock()

    with (
        patch("app.database.async_session_maker", return_value=SessionCtx()),
        patch.object(scraper_cli, "_run_extract_batch", new=extract_batch_mock),
    ):
        await scraper_cli._repair_i18n_command(
            school_name=None,
            school_id=None,
            city="sofia",
            country="bg",
            limit=None,
            dry_run=False,
            reextract=True,
        )

    await db_session.refresh(school)
    await db_session.refresh(location)
    assert school.name_i18n == {"bg": bg_name}
    assert location.address_i18n == {"bg": bg_address}
    extract_batch_mock.assert_awaited_once()
    assert extract_batch_mock.await_args.kwargs["school_ids"] == [school.id]


@pytest.mark.asyncio
async def test_location_repair_reasons_for_school_detects_office_like_and_missing_coords():
    school = School(
        id=537,
        name_i18n={"bg": 'ЧОУ "Д-р Петър Берон"'},
        country_code="bg",
        school_type="private",
        education_level="primary",
        city="sofia",
        website_url="https://pberon.example",
    )
    school.locations = [
        SchoolLocation(
            school_id=537,
            address_i18n={"bg": 'бул. "Джеймс Баучер" № 116, ет. 1, ап. 4'},
            is_primary=True,
            location_tags=["coords_source=geojson"],
        )
    ]

    reasons = scraper_cli._location_repair_reasons_for_school(school)

    assert reasons == ["office-like-address", "missing-coords", "geojson-coords"]


@pytest.mark.asyncio
async def test_repair_locations_command_targets_candidates_and_runs_navigate_and_extract(db_session):
    candidate_school = School(
        name_i18n={"bg": 'ЧОУ "Д-р Петър Берон"'},
        country_code="bg",
        school_type="private",
        education_level="primary",
        city="sofia",
        website_url="https://pberon.example",
        scrape_status="extracted",
    )
    healthy_school = School(
        name_i18n={"bg": "Fusion Academy"},
        country_code="bg",
        school_type="private",
        education_level="primary",
        city="sofia",
        website_url="https://fusion.example",
        scrape_status="extracted",
    )
    db_session.add_all([candidate_school, healthy_school])
    await db_session.flush()

    db_session.add_all(
        [
            SchoolLocation(
                school_id=candidate_school.id,
                address_i18n={"bg": 'бул. "Джеймс Баучер" № 116, ет. 1, ап. 4'},
                is_primary=True,
                location_tags=["coords_source=geojson"],
            ),
            SchoolLocation(
                school_id=healthy_school.id,
                address_i18n={"bg": "гр. София, ул. Иван Вазов 15"},
                lat=42.69,
                lng=23.32,
                is_primary=True,
                location_tags=["coords_source=website_map_link"],
            ),
        ]
    )
    await db_session.commit()

    class SessionCtx:
        async def __aenter__(self):
            return db_session

        async def __aexit__(self, exc_type, exc, tb):
            return False

    navigate_batch_mock = AsyncMock(return_value=[{"school_id": candidate_school.id, "success": True}])
    extract_batch_mock = AsyncMock()

    with (
        patch("app.database.async_session_maker", return_value=SessionCtx()),
        patch.object(scraper_cli, "_run_navigate_batch", new=navigate_batch_mock),
        patch.object(scraper_cli, "_run_extract_batch", new=extract_batch_mock),
    ):
        await scraper_cli._repair_locations_command(
            school_name=None,
            school_id=None,
            city="sofia",
            country="bg",
            limit=None,
            include_state=False,
            dry_run=False,
            run_extract=True,
        )

    navigate_batch_mock.assert_awaited_once()
    extract_batch_mock.assert_awaited_once()
    assert navigate_batch_mock.await_args.kwargs["school_ids"] == [candidate_school.id]
    assert navigate_batch_mock.await_args.kwargs["skip_timed_out_chunks"] is True
    assert extract_batch_mock.await_args.kwargs["school_ids"] == [candidate_school.id]
