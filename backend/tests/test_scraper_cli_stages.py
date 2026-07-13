"""Tests for scraper CLI stage helpers (canonical)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.models import PipelineRun, PipelineStage, School
from app.scrapers import cli as scraper_cli


@pytest.mark.asyncio
async def test_stage_choices_use_canonical_names():
    run_command = scraper_cli.run
    stage_param = next(param for param in run_command.params if param.name == "stage")
    choices = set(stage_param.type.choices)

    assert "nvo" in choices
    assert "navigate" in choices
    assert "extract" in choices
    assert "all" in choices
    assert "navigate-v2" not in choices
    assert "extract-v2" not in choices
    assert "all-v2" not in choices


def test_read_cohort_file_supports_comments_commas_and_deterministic_order(tmp_path):
    cohort_file = tmp_path / "pilot.txt"
    cohort_file.write_text("# representative pilot\n23, 7\n15 # private school\n", encoding="utf-8")

    assert scraper_cli._read_cohort_file(cohort_file) == [7, 15, 23]


@pytest.mark.parametrize("contents", ["", "7, seven", "0", "7\n7"])
def test_read_cohort_file_rejects_invalid_or_ambiguous_ids(tmp_path, contents):
    cohort_file = tmp_path / "pilot.txt"
    cohort_file.write_text(contents, encoding="utf-8")

    with pytest.raises(scraper_cli.click.UsageError):
        scraper_cli._read_cohort_file(cohort_file)


@pytest.mark.asyncio
async def test_validate_batch_aggregates_spot_check_usage(db_session):
    school = School(
        name_i18n={"bg": "Usage school"},
        country_code="bg",
        city="sofia",
        school_type="private",
        education_level="primary",
        scrape_status="extracted",
        attributes={"extracted": {"programs": ["Primary"]}},
    )
    db_session.add(school)
    await db_session.commit()

    class SessionContext:
        async def __aenter__(self):
            return db_session

        async def __aexit__(self, *_args):
            return False

    settings = SimpleNamespace(
        validation_batch_concurrency=1,
        spot_check_sample_size=-1,
        spot_check_discrepancy_threshold=0.15,
    )
    with (
        patch("app.config.get_settings", return_value=settings),
        patch("app.database.async_session_maker", side_effect=lambda: SessionContext()),
        patch(
            "app.scrapers.validator.validate_school_data",
            new=AsyncMock(return_value={"status": "ok"}),
        ),
        patch(
            "app.scrapers.validator.run_spot_check_for_school",
            new=AsyncMock(
                return_value={
                    "status": "checked",
                    "has_discrepancy": False,
                    "kind_counts": {},
                    "input_tokens": 500,
                    "output_tokens": 75,
                    "token_cost_usd": 0.0042,
                }
            ),
        ),
    ):
        summary = await scraper_cli._run_validate_data_batch(
            db_session,
            country="bg",
            city="sofia",
            limit=None,
            force_validate=True,
            school_ids=[school.id],
        )

    assert summary["input_tokens"] == 500
    assert summary["output_tokens"] == 75
    assert summary["token_cost_usd"] == 0.0042


@pytest.mark.asyncio
async def test_run_discover_batch_counts_crashed_adapter_as_failed(db_session):
    class _BoomAdapter:
        ADAPTER_NAME = "boom"

        def __init__(self, db):
            self.db = db

        async def run(self, limit=None, sample_ratio=1.0):
            raise RuntimeError("kaboom")

    with patch(
        "app.scrapers.sources.get_adapters_for_country",
        return_value=[_BoomAdapter],
    ):
        summary = await scraper_cli._run_discover_batch(db_session, "bg", "sofia", None, 1.0)

    # A crashed adapter must not record COMPLETED with all-zero counts.
    assert summary["failed"] == 1
    assert summary["succeeded"] == 0
    assert summary["processed"] == 1


@pytest.mark.asyncio
async def test_run_all_stages_routes_to_canonical_handlers(db_session):
    school = School(
        name_i18n={"bg": "Тест"},
        country_code="bg",
        school_type="state",
        education_level="primary",
        city="sofia",
        website_url="https://school.bg",
        scrape_status="validated",
    )
    db_session.add(school)
    await db_session.commit()

    with (
        patch.object(scraper_cli, "_run_discover_website", new=AsyncMock()) as discover_mock,
        patch.object(scraper_cli, "_run_validate_url", new=AsyncMock()) as validate_mock,
        patch.object(scraper_cli, "_run_navigate_school", new=AsyncMock()) as nav_mock,
        patch.object(scraper_cli, "_run_extract_school", new=AsyncMock()) as extract_mock,
        patch.object(scraper_cli, "_run_validate_data_school", new=AsyncMock()) as validate_data_mock,
        patch.object(scraper_cli, "_run_summarize_school", new=AsyncMock()) as summarize_mock,
    ):
        await scraper_cli._run_all_stages(db_session, school.id, "bg")

    discover_mock.assert_awaited_once()
    validate_mock.assert_awaited_once()
    nav_mock.assert_awaited_once()
    extract_mock.assert_awaited_once()
    validate_data_mock.assert_awaited_once()
    summarize_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_select_all_stage_cohort_is_deterministic_and_includes_refresh_rows(db_session):
    schools = [
        School(
            name_i18n={"bg": "Summarized"},
            country_code="bg",
            school_type="state",
            education_level="primary",
            city="sofia",
            website_url="https://summarized.example",
            scrape_status="summarized",
        ),
        School(
            name_i18n={"bg": "Pending"},
            country_code="bg",
            school_type="state",
            education_level="primary",
            city="sofia",
            website_url="https://pending.example",
            scrape_status="pending",
        ),
        School(
            name_i18n={"bg": "Extracted"},
            country_code="bg",
            school_type="state",
            education_level="primary",
            city="sofia",
            website_url="https://extracted.example",
            scrape_status="extracted",
        ),
        School(
            name_i18n={"bg": "Other city"},
            country_code="bg",
            school_type="state",
            education_level="primary",
            city="plovdiv",
            website_url="https://plovdiv.example",
            scrape_status="pending",
        ),
    ]
    db_session.add_all(schools)
    await db_session.commit()

    ordinary_ids = await scraper_cli._select_all_stage_cohort(
        db_session,
        country="bg",
        city="sofia",
        limit=2,
        include_navigated=False,
        include_extracted=False,
        force_validate=False,
    )
    refresh_ids = await scraper_cli._select_all_stage_cohort(
        db_session,
        country="bg",
        city="sofia",
        limit=2,
        include_navigated=True,
        include_extracted=True,
        force_validate=True,
    )
    explicit_ids = await scraper_cli._select_all_stage_cohort(
        db_session,
        country="bg",
        city="sofia",
        limit=None,
        include_navigated=True,
        include_extracted=True,
        force_validate=True,
        requested_school_ids=[schools[2].id, schools[0].id],
    )

    assert ordinary_ids == [schools[1].id]
    assert refresh_ids == [schools[0].id, schools[1].id]
    assert explicit_ids == [schools[0].id, schools[2].id]

    with pytest.raises(scraper_cli.click.UsageError, match="outside the requested"):
        await scraper_cli._select_all_stage_cohort(
            db_session,
            country="bg",
            city="sofia",
            limit=None,
            include_navigated=True,
            include_extracted=True,
            force_validate=True,
            requested_school_ids=[schools[3].id],
        )


@pytest.mark.asyncio
async def test_run_all_stages_batch_propagates_one_cohort_to_every_stage(db_session):
    cohort = [17, 23]
    summary = {"processed": 2, "succeeded": 2, "failed": 0, "skipped": 0}
    url_summary = {**summary, "validated_school_ids": cohort}
    extract_summary = {**summary, "ready_school_ids": cohort}
    validation_summary = {**summary, "validated_school_ids": cohort}
    pipeline_run = PipelineRun(
        id="fixed-cohort-test",
        country_code="bg",
        city="sofia",
        stage=PipelineStage.FULL,
        config={"cli_stage": "all"},
    )
    db_session.add(pipeline_run)
    await db_session.commit()

    with (
        patch.object(scraper_cli, "_select_all_stage_cohort", new=AsyncMock(return_value=cohort)),
        patch.object(scraper_cli, "_run_validate_urls_batch", new=AsyncMock(return_value=url_summary)) as urls_mock,
        patch.object(
            scraper_cli,
            "_run_navigate_batch",
            new=AsyncMock(return_value=[{"school_id": school_id, "success": True} for school_id in cohort]),
        ) as navigate_mock,
        patch.object(
            scraper_cli,
            "_run_extract_batch",
            new=AsyncMock(return_value=extract_summary),
        ) as extract_mock,
        patch.object(
            scraper_cli,
            "_run_validate_data_batch",
            new=AsyncMock(return_value=validation_summary),
        ) as validate_mock,
        patch.object(scraper_cli, "_run_summarize_batch", new=AsyncMock(return_value=summary)) as summarize_mock,
    ):
        results = await scraper_cli._run_all_stages_batch(
            db_session,
            country="bg",
            city="sofia",
            limit=2,
            include_navigated=True,
            include_extracted=True,
            force_validate=True,
            requested_school_ids=cohort,
            pipeline_run=pipeline_run,
        )

    assert len(results) == 5
    for mock in (urls_mock, navigate_mock, extract_mock, validate_mock, summarize_mock):
        assert mock.await_args.kwargs["school_ids"] == cohort
    assert urls_mock.await_args.args[3] is None
    assert navigate_mock.await_args.kwargs["include_navigated"] is True
    assert extract_mock.await_args.kwargs["include_extracted"] is True
    assert validate_mock.await_args.kwargs["force_validate"] is True
    assert pipeline_run.config["cohort_school_ids"] == cohort


@pytest.mark.asyncio
async def test_run_all_stages_batch_narrows_follow_on_stages_to_fresh_successes(db_session):
    cohort = [17, 23, 42]
    count_summary = {"processed": 3, "succeeded": 2, "failed": 1, "skipped": 0}
    url_summary = {**count_summary, "validated_school_ids": [17, 42]}
    navigation_results = [
        {"school_id": 17, "success": True},
        {"school_id": 23, "success": False},
        {"school_id": 42, "success": True},
    ]
    extract_summary = {
        "processed": 2,
        "succeeded": 1,
        "failed": 1,
        "skipped": 0,
        "ready_school_ids": [42],
    }
    validation_summary = {
        "processed": 1,
        "succeeded": 1,
        "failed": 0,
        "skipped": 0,
        "validated_school_ids": [42],
    }

    with (
        patch.object(scraper_cli, "_select_all_stage_cohort", new=AsyncMock(return_value=cohort)),
        patch.object(scraper_cli, "_run_validate_urls_batch", new=AsyncMock(return_value=url_summary)) as urls_mock,
        patch.object(
            scraper_cli,
            "_run_navigate_batch",
            new=AsyncMock(return_value=navigation_results),
        ) as navigate_mock,
        patch.object(scraper_cli, "_run_extract_batch", new=AsyncMock(return_value=extract_summary)) as extract_mock,
        patch.object(
            scraper_cli,
            "_run_validate_data_batch",
            new=AsyncMock(return_value=validation_summary),
        ) as validate_mock,
        patch.object(scraper_cli, "_run_summarize_batch", new=AsyncMock(return_value=count_summary)) as summarize_mock,
    ):
        await scraper_cli._run_all_stages_batch(
            db_session,
            country="bg",
            city="sofia",
            limit=None,
            include_navigated=True,
            include_extracted=True,
            force_validate=True,
        )

    assert urls_mock.await_args.kwargs["school_ids"] == cohort
    assert navigate_mock.await_args.kwargs["school_ids"] == [17, 42]
    assert extract_mock.await_args.kwargs["school_ids"] == [17, 42]
    assert validate_mock.await_args.kwargs["school_ids"] == [42]
    assert summarize_mock.await_args.kwargs["school_ids"] == [42]


@pytest.mark.asyncio
async def test_run_all_stages_batch_preserves_completed_summaries_when_later_stage_raises(db_session):
    cohort = [17]
    completed: list[dict] = []
    extraction = {
        "processed": 1,
        "succeeded": 1,
        "failed": 0,
        "skipped": 0,
        "input_tokens": 120,
        "output_tokens": 30,
        "token_cost_usd": 0.01,
        "ready_school_ids": [17],
    }

    with (
        patch.object(scraper_cli, "_select_all_stage_cohort", new=AsyncMock(return_value=cohort)),
        patch.object(
            scraper_cli,
            "_run_validate_urls_batch",
            new=AsyncMock(
                return_value={
                    "processed": 1,
                    "succeeded": 1,
                    "failed": 0,
                    "skipped": 0,
                    "validated_school_ids": [17],
                }
            ),
        ),
        patch.object(
            scraper_cli,
            "_run_navigate_batch",
            new=AsyncMock(return_value=[{"school_id": 17, "success": True}]),
        ),
        patch.object(scraper_cli, "_run_extract_batch", new=AsyncMock(return_value=extraction)),
        patch.object(
            scraper_cli,
            "_run_validate_data_batch",
            new=AsyncMock(side_effect=RuntimeError("validation crashed")),
        ),
    ):
        with pytest.raises(RuntimeError, match="validation crashed"):
            await scraper_cli._run_all_stages_batch(
                db_session,
                country="bg",
                city="sofia",
                limit=None,
                include_navigated=True,
                include_extracted=True,
                force_validate=True,
                stage_summaries=completed,
            )

    assert len(completed) == 3
    assert completed[-1]["token_cost_usd"] == pytest.approx(0.01)


@pytest.mark.asyncio
async def test_empty_explicit_cohort_never_falls_back_to_status_selection(db_session):
    school = School(
        name_i18n={"bg": "Не трябва да се обработи"},
        country_code="bg",
        school_type="state",
        education_level="primary",
        city="sofia",
        website_url="https://school.example",
        scrape_status="validated",
    )
    db_session.add(school)
    await db_session.commit()

    with patch("app.scrapers.navigator.navigate_schools_batch", new=AsyncMock()) as navigate_mock:
        navigation_results = await scraper_cli._run_navigate_batch(
            db_session, "bg", "sofia", None, school_ids=[]
        )
    extraction_summary = await scraper_cli._run_extract_batch(
        db_session, "bg", "sofia", None, school_ids=[]
    )

    assert navigation_results is None
    navigate_mock.assert_not_awaited()
    assert extraction_summary["processed"] == 0


@pytest.mark.asyncio
async def test_run_sync_routes_nvo_stage_to_import_helper(db_session):
    school = School(
        name_i18n={"bg": "НВО тест"},
        country_code="bg",
        school_type="state",
        education_level="primary",
        city="sofia",
    )
    db_session.add(school)
    await db_session.commit()

    with patch.object(scraper_cli, "_run_nvo_import", new=AsyncMock()) as nvo_mock:
        await scraper_cli._run_sync(
            school_name=None,
            school_id=school.id,
            stage="nvo",
            city="sofia",
            country="bg",
            limit=None,
            year=2025,
            history_years=5,
            exam_types=["nvo_4"],
            sample_ratio=0.0,
            include_navigated=False,
            include_extracted=False,
            force_validate=False,
        )

    nvo_mock.assert_awaited_once()
    kwargs = nvo_mock.await_args.kwargs
    assert kwargs["country"] == "bg"
    assert kwargs["city"] == "sofia"
    assert kwargs["year"] == 2025
    assert kwargs["history_years"] == 5
    assert kwargs["exam_types"] == ["nvo_4"]
    assert kwargs["school_ids"] == [school.id]


@pytest.mark.asyncio
async def test_discover_websites_batch_includes_missing_website_rows_beyond_failed_validate(db_session):
    eligible_missing = School(
        name_i18n={"bg": "Липсващ сайт"},
        country_code="bg",
        school_type="private",
        education_level="kindergarten",
        city="sofia",
        website_url=None,
        scrape_status="extraction_failed",
    )
    eligible_pending = School(
        name_i18n={"bg": "Чакащ сайт"},
        country_code="bg",
        school_type="state",
        education_level="primary",
        city="sofia",
        website_url=None,
        scrape_status="pending",
    )
    terminal_no_site = School(
        name_i18n={"bg": "Без сайт"},
        country_code="bg",
        school_type="state",
        education_level="primary",
        city="sofia",
        website_url=None,
        scrape_status="no_official_website",
    )
    extraction_failed_with_site = School(
        name_i18n={"bg": "Екстракция със сайт"},
        country_code="bg",
        school_type="private",
        education_level="kindergarten",
        city="sofia",
        website_url="https://existing-school.bg",
        scrape_status="extraction_failed",
    )
    validated = School(
        name_i18n={"bg": "Валидиран"},
        country_code="bg",
        school_type="state",
        education_level="primary",
        city="sofia",
        website_url="https://school.bg",
        scrape_status="validated",
    )
    db_session.add_all([eligible_missing, eligible_pending, terminal_no_site, extraction_failed_with_site, validated])
    await db_session.commit()

    with patch.object(scraper_cli, "_run_discover_website", new=AsyncMock(return_value={"found": False})) as discover_mock:
        await scraper_cli._run_discover_websites_batch(
            db=db_session,
            country="bg",
            city="sofia",
            limit=None,
        )

    called_ids = {call.args[1] for call in discover_mock.await_args_list}
    assert eligible_missing.id in called_ids
    assert eligible_pending.id in called_ids
    assert terminal_no_site.id not in called_ids
    assert extraction_failed_with_site.id not in called_ids
    assert validated.id not in called_ids


@pytest.mark.asyncio
async def test_run_navigate_batch_rolls_back_and_continues_after_school_error(db_session):
    for idx in range(2):
        db_session.add(
            School(
                name_i18n={"bg": f"Навигация {idx}"},
                country_code="bg",
                school_type="state",
                education_level="primary",
                city="sofia",
                website_url=f"https://n{idx}.school.bg",
                scrape_status="validated",
            )
        )
    await db_session.commit()

    run_mock = AsyncMock(side_effect=[RuntimeError("boom"), {"success": True}])
    rollback_spy = AsyncMock(wraps=db_session.rollback)
    settings = SimpleNamespace(nav_school_timeout_seconds=0, nav_batch_concurrency=3)

    with (
        patch("app.config.get_settings", return_value=settings),
        patch("app.scrapers.navigator.navigate_schools_batch", new=AsyncMock(side_effect=RuntimeError("boom"))),
        patch.object(scraper_cli, "_run_navigate_school", new=run_mock),
        patch.object(db_session, "rollback", new=rollback_spy),
    ):
        await scraper_cli._run_navigate_batch(
            db=db_session,
            country="bg",
            city="sofia",
            limit=None,
            include_navigated=False,
        )

    assert run_mock.await_count == 2
    assert rollback_spy.await_count == 2


@pytest.mark.asyncio
async def test_run_navigate_batch_uses_batch_crawler_results(db_session):
    schools = []
    for idx in range(2):
        school = School(
            name_i18n={"bg": f"Навигация {idx}"},
            country_code="bg",
            school_type="state",
            education_level="primary",
            city="sofia",
            website_url=f"https://n{idx}.school.bg",
            scrape_status="validated",
        )
        db_session.add(school)
        schools.append(school)
    await db_session.commit()

    settings = SimpleNamespace(nav_school_timeout_seconds=0, nav_batch_concurrency=2)
    batch_results = [
        {"school_id": schools[0].id, "success": True},
        {"school_id": schools[1].id, "success": False},
    ]

    with (
        patch("app.config.get_settings", return_value=settings),
        patch("app.scrapers.navigator.navigate_schools_batch", new=AsyncMock(return_value=batch_results)) as batch_mock,
        patch.object(scraper_cli, "_run_navigate_school", new=AsyncMock()) as single_mock,
    ):
        await scraper_cli._run_navigate_batch(
            db=db_session,
            country="bg",
            city="sofia",
            limit=None,
            include_navigated=False,
        )

    batch_mock.assert_awaited_once()
    single_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_navigate_batch_skips_timed_out_explicit_chunks(db_session):
    schools = []
    for idx in range(3):
        school = School(
            name_i18n={"bg": f"Навигация {idx}"},
            country_code="bg",
            school_type="state",
            education_level="primary",
            city="sofia",
            website_url=f"https://n{idx}.school.bg",
            scrape_status="validated",
        )
        db_session.add(school)
        schools.append(school)
    await db_session.commit()
    school_ids = [school.id for school in schools]

    settings = SimpleNamespace(nav_school_timeout_seconds=10, nav_batch_concurrency=2)
    batch_mock = AsyncMock(
        side_effect=[
            asyncio.TimeoutError(),
            [{"school_id": schools[2].id, "success": True}],
        ]
    )
    rollback_spy = AsyncMock(wraps=db_session.rollback)

    with (
        patch("app.config.get_settings", return_value=settings),
        patch("app.scrapers.navigator.navigate_schools_batch", new=batch_mock),
        patch.object(db_session, "rollback", new=rollback_spy),
        patch.object(scraper_cli, "_run_navigate_school", new=AsyncMock()) as single_mock,
    ):
        results = await scraper_cli._run_navigate_batch(
            db=db_session,
            country="bg",
            city="sofia",
            limit=None,
            include_navigated=True,
            school_ids=school_ids,
            skip_timed_out_chunks=True,
        )

    assert [result["school_id"] for result in results] == school_ids
    assert [result["success"] for result in results] == [False, False, True]
    assert rollback_spy.await_count == 1
    single_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_extract_batch_rolls_back_and_continues_after_school_error(db_session):
    for idx in range(2):
        db_session.add(
            School(
                name_i18n={"bg": f"Екстракция {idx}"},
                country_code="bg",
                school_type="private",
                education_level="primary",
                city="sofia",
                website_url=f"https://e{idx}.school.bg",
                scrape_status="navigated",
            )
        )
    await db_session.commit()

    run_mock = AsyncMock(side_effect=[RuntimeError("boom"), {"status": "extracted"}])
    rollback_spy = AsyncMock(wraps=db_session.rollback)
    settings = SimpleNamespace(extraction_school_timeout_seconds=0, extraction_batch_concurrency=1)

    with (
        patch("app.config.get_settings", return_value=settings),
        patch.object(scraper_cli, "_run_extract_school", new=run_mock),
        patch.object(db_session, "rollback", new=rollback_spy),
    ):
        await scraper_cli._run_extract_batch(
            db=db_session,
            country="bg",
            city="sofia",
            limit=None,
            include_extracted=False,
        )

    assert run_mock.await_count == 2
    assert rollback_spy.await_count == 1


@pytest.mark.asyncio
async def test_run_extract_batch_with_explicit_school_ids_ignores_status_filter(db_session):
    school = School(
        name_i18n={"bg": "Изрично училище"},
        country_code="bg",
        school_type="private",
        education_level="primary",
        city="sofia",
        website_url="https://explicit.school.bg",
        scrape_status="pending",
    )
    db_session.add(school)
    await db_session.commit()

    settings = SimpleNamespace(extraction_school_timeout_seconds=0, extraction_batch_concurrency=1)
    run_mock = AsyncMock(
        return_value={
            "status": "extracted",
            "input_tokens": 123,
            "output_tokens": 45,
            "token_cost_usd": 0.006789,
        }
    )

    with (
        patch("app.config.get_settings", return_value=settings),
        patch.object(scraper_cli, "_run_extract_school", new=run_mock),
    ):
        summary = await scraper_cli._run_extract_batch(
            db=db_session,
            country="bg",
            city="sofia",
            limit=None,
            include_extracted=True,
            school_ids=[school.id],
        )

    run_mock.assert_awaited_once_with(db_session, school.id, "bg")
    assert summary == {
        "processed": 1,
        "succeeded": 1,
        "failed": 0,
        "skipped": 0,
        "input_tokens": 123,
        "output_tokens": 45,
        "token_cost_usd": pytest.approx(0.006789),
        "ready_school_ids": [school.id],
    }


@pytest.mark.asyncio
async def test_run_summarize_batch_aggregates_usage_for_all_results(db_session):
    class SessionCtx:
        async def __aenter__(self):
            return db_session

        async def __aexit__(self, exc_type, exc, tb):
            return False

    settings = SimpleNamespace(summarization_batch_concurrency=1)
    summarize_mock = AsyncMock(
        side_effect=[
            {
                "status": "summarized",
                "input_tokens": 70,
                "output_tokens": 20,
                "token_cost_usd": 0.004,
            },
            {
                "status": "summary_failed",
                "input_tokens": 30,
                "output_tokens": 5,
                "token_cost_usd": 0.001,
            },
        ]
    )

    with (
        patch("app.config.get_settings", return_value=settings),
        patch("app.database.async_session_maker", return_value=SessionCtx()),
        patch("app.scrapers.summarizer.summarize_school", new=summarize_mock),
    ):
        summary = await scraper_cli._run_summarize_batch(
            db=db_session,
            country="bg",
            city="sofia",
            limit=None,
            school_ids=[101, 102],
        )

    assert summary == {
        "processed": 2,
        "succeeded": 1,
        "failed": 1,
        "skipped": 0,
        "input_tokens": 100,
        "output_tokens": 25,
        "token_cost_usd": pytest.approx(0.005),
    }
