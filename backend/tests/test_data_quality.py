"""Tests for the P1.6 data-quality scoreboard and PipelineRun lifecycle."""

import pytest

from app.models.pipeline_run import PipelineStatus
from app.models.pricing import PriceCategory, PricePeriod, PriceSource, Pricing
from app.models.school import School, SchoolLocation
from app.services.data_quality import compute_quality_metrics
from app.services.pipeline_runs import finalize_pipeline_run, start_pipeline_run

pytestmark = pytest.mark.asyncio


async def _make_school(db, **kwargs):
    school = School(
        country_code=kwargs.pop("country_code", "bg"),
        city=kwargs.pop("city", "sofia"),
        school_type=kwargs.pop("school_type", "private"),
        education_level="primary",
        name_i18n={"bg": "Училище", "en": "School"},
        **kwargs,
    )
    db.add(school)
    await db.flush()
    return school


@pytest.fixture
async def quality_fixture(db_session):
    """Two in-scope schools exercising every metric, plus one out-of-scope."""
    # School A — clean: validation ok, corroborated override, exact geocode,
    # spot-check discrepancy, one gate-passing + one gate-failing price row.
    a = await _make_school(
        db_session,
        website_url="https://a.bg",
        attributes={
            "extracted": {"languages": []},
            "display_name_i18n": {"bg": "Бранд А"},
            "display_name_evidence": {"status": "corroborated", "signals": ["x", "y"]},
            "data_validation": {"status": "ok", "spot_check": {"has_discrepancy": True}},
        },
    )
    db_session.add(
        SchoolLocation(school_id=a.id, address_i18n={"bg": "ул. А"}, lat=42.70, lng=23.32, geocode_meta={"precision": "exact"})
    )
    db_session.add(
        Pricing(
            school_id=a.id, category=PriceCategory.TUITION, period=PricePeriod.MONTHLY,
            amount=500, source=PriceSource.SCRAPED_WEBSITE, source_url="https://a.bg/fees",
            pricing_context={"confidence": 0.9},
        )
    )
    db_session.add(
        Pricing(
            school_id=a.id, category=PriceCategory.FOOD, period=PricePeriod.MONTHLY,
            amount=100, source=PriceSource.SCRAPED_WEBSITE, source_url=None,
            pricing_context={"confidence": 0.95},
        )
    )

    # School B — needs_review, display-name candidate without corroboration,
    # approximate geocode sharing A's coordinates, spot-check clean, low-confidence price.
    b = await _make_school(
        db_session,
        website_url="https://b.bg",
        attributes={
            "extracted": {"languages": []},
            "display_name_i18n": {"bg": "Бранд Б"},
            "data_validation": {"status": "needs_review", "spot_check": {"has_discrepancy": False}},
        },
    )
    db_session.add(
        SchoolLocation(school_id=b.id, address_i18n={"bg": "ул. Б"}, lat=42.70, lng=23.32, geocode_meta={"precision": "approximate"})
    )
    db_session.add(
        Pricing(
            school_id=b.id, category=PriceCategory.TUITION, period=PricePeriod.MONTHLY,
            amount=600, source=PriceSource.SCRAPED_WEBSITE, source_url="https://b.bg/fees",
            pricing_context={"confidence": 0.5},
        )
    )

    # Out-of-scope school (different city) — must not affect Sofia metrics.
    other = await _make_school(db_session, city="plovdiv", attributes={"data_validation": {"status": "ok"}})
    db_session.add(SchoolLocation(school_id=other.id, address_i18n={"bg": "ул. В"}, lat=1.0, lng=1.0, geocode_meta={"precision": "exact"}))

    await db_session.commit()
    return db_session


async def test_metrics_cover_all_six(quality_fixture):
    m = await compute_quality_metrics(quality_fixture, country="bg", city="sofia")

    assert m["schools_in_scope"] == 2
    assert m["validation_ok"] == {"ok": 1, "total": 2, "pct": 50.0}
    assert m["duplicate_coordinate_groups"] == 1
    assert m["location_precision_exact"] == {"exact": 1, "with_precision": 2, "pct": 50.0}
    assert m["display_name_overrides"] == {"overrides": 1, "candidates": 2, "pct": 50.0}
    assert m["spot_check_discrepancy_rate"] == {"discrepancies": 1, "schools_checked": 2, "rate": 0.5}
    assert m["pricing_rows_failing_gates"] == {"failing": 2, "total": 3, "pct": pytest.approx(66.7)}


async def test_city_scope_excludes_other_cities(quality_fixture):
    m = await compute_quality_metrics(quality_fixture, country="bg", city="plovdiv")
    assert m["schools_in_scope"] == 1
    assert m["location_precision_exact"]["with_precision"] == 1
    assert m["duplicate_coordinate_groups"] == 0


async def test_empty_scope_returns_none_ratios(db_session):
    m = await compute_quality_metrics(db_session, country="bg", city="sofia")
    assert m["schools_in_scope"] == 0
    assert m["validation_ok"]["pct"] is None
    assert m["spot_check_discrepancy_rate"]["rate"] is None
    assert m["duplicate_coordinate_groups"] == 0


async def test_pipeline_run_lifecycle_writes_metrics(quality_fixture):
    run = await start_pipeline_run(
        quality_fixture, country="bg", city="sofia", cli_stage="extract", config={"limit": 5}
    )
    assert run.status == PipelineStatus.RUNNING
    assert run.config["cli_stage"] == "extract"

    summaries = [{"processed": 2, "succeeded": 1, "failed": 1, "skipped": 0}]
    finalized = await finalize_pipeline_run(
        quality_fixture, run, country="bg", city="sofia", stage_summaries=summaries
    )

    assert finalized.status == PipelineStatus.PARTIAL  # some succeeded, some failed
    assert finalized.completed_at is not None
    assert finalized.schools_succeeded == 1
    assert finalized.schools_failed == 1
    assert finalized.metrics["validation_ok"] == {"ok": 1, "total": 2, "pct": 50.0}


async def test_pipeline_run_status_completed_when_no_failures(quality_fixture):
    run = await start_pipeline_run(quality_fixture, country="bg", city="sofia", cli_stage="all")
    finalized = await finalize_pipeline_run(
        quality_fixture, run, country="bg", city="sofia",
        stage_summaries=[{"processed": 3, "succeeded": 3, "failed": 0, "skipped": 0}, ["ignored-list"]],
    )
    assert finalized.status == PipelineStatus.COMPLETED
    assert finalized.schools_succeeded == 3


async def test_pipeline_run_status_failed_on_error(quality_fixture):
    run = await start_pipeline_run(quality_fixture, country="bg", city="sofia", cli_stage="extract")
    finalized = await finalize_pipeline_run(
        quality_fixture, run, country="bg", city="sofia",
        stage_summaries=[], error_summary="boom",
    )
    assert finalized.status == PipelineStatus.FAILED
    assert finalized.error_summary == "boom"
