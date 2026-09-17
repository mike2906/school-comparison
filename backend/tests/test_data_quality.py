"""Tests for the P1.6 data-quality scoreboard and PipelineRun lifecycle."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.models.pipeline_run import PipelineStatus
from app.models.pricing import PriceCategory, PricePeriod, PriceSource, Pricing
from app.models.school import School, SchoolLocation
from app.models.source_page import ScrapeType, SourcePage
from app.schemas.school import SchoolListResponse
from app.services.data_quality import _display_name_overrides, compute_quality_metrics
from app.services.pipeline_runs import (
    _aggregate_usage,
    checkpoint_pipeline_run,
    finalize_pipeline_run,
    heartbeat_pipeline_run,
    start_pipeline_run,
    terminalize_stale_pipeline_runs,
)
from app.utils.website_data import attributes_for_publication, website_data_is_publishable

pytestmark = pytest.mark.asyncio
PRICING_VERIFIED_AT = datetime(2026, 7, 16, 9, 0)


def _verified_context(confidence):
    return {
        "confidence": confidence,
        "human_verification": {
            "verified_by": "test-curator",
            "verified_at": "2026-07-16T09:00:00Z",
        },
    }


async def _evidence_page(db, school, url="https://example.com/fees"):
    """A valid source page: the evidence link the publish gate now requires."""
    page = SourcePage(
        school_id=school.id,
        scrape_type=ScrapeType.WEBSITE,
        source_url=url,
        content_hash=f"hash-{school.id}-{url}",
        raw_markdown="Такси",
        is_valid=True,
    )
    db.add(page)
    await db.flush()
    return page


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


async def test_display_name_scoreboard_counts_only_semantically_publishable_overrides():
    evidence = {
        "status": "corroborated",
        "signals": ["website_domain_alias_match", "repeated_on_page_identity"],
    }
    schools = [
        SimpleNamespace(
            attributes={
                "display_name_i18n": {"en": "Fusion School"},
                "display_name_evidence": evidence,
                "data_validation": {"_schema_version": 1, "status": "ok"},
            },
            scrape_status="extracted",
        ),
        SimpleNamespace(
            attributes={
                "display_name_i18n": {"en": "Our Values At Deni Diderot School"},
                "display_name_evidence": evidence,
                "data_validation": {"_schema_version": 1, "status": "ok"},
            },
            scrape_status="extracted",
        ),
    ]

    assert _display_name_overrides(schools)["overrides"] == 1


@pytest.fixture
async def quality_fixture(db_session):
    """Two in-scope schools exercising every metric, plus one out-of-scope."""
    # School A — clean: validation ok, corroborated override, exact geocode,
    # spot-check discrepancy, one gate-passing + one gate-failing price row.
    a = await _make_school(
        db_session,
        website_url="https://a.bg",
        scrape_status="extracted",
        attributes={
            "extracted": {"languages": [], "facilities": ["Library"]},
            "display_name_i18n": {"bg": "Бранд А"},
            "display_name_evidence": {
                "status": "corroborated",
                "signals": ["website_domain_alias_match", "repeated_on_page_identity"],
            },
            "data_validation": {
                "_schema_version": 1,
                "status": "ok",
                "spot_check": {"has_discrepancy": True},
            },
        },
    )
    db_session.add(
        SchoolLocation(school_id=a.id, address_i18n={"bg": "ул. А"}, lat=42.70, lng=23.32, geocode_meta={"precision": "exact"})
    )
    a_page = await _evidence_page(db_session, a, url="https://a.bg/fees")
    db_session.add(
        Pricing(
            school_id=a.id, category=PriceCategory.TUITION, period=PricePeriod.MONTHLY,
            amount=500, source=PriceSource.OFFICIAL, source_url="https://a.bg/fees",
            scraped_at=PRICING_VERIFIED_AT, source_page_id=a_page.id,
            pricing_context=_verified_context(0.9),
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
        scrape_status="extracted",
        attributes={
            "extracted": {"languages": [], "programs": ["STEM"]},
            "display_name_i18n": {"bg": "Бранд Б"},
            "data_validation": {
                "_schema_version": 1,
                "status": "needs_review",
                "spot_check": {"has_discrepancy": False},
            },
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
    assert m["validation_ok"] == {
        "ok": 1,
        "total": 2,
        "pct": 50.0,
        "with_report": 2,
        "coverage_pct": 100.0,
    }
    assert m["website_validation_coverage"] == {
        "eligible": 2,
        "with_report": 2,
        "coverage_pct": 100.0,
        "ok": 1,
        "ok_pct": 50.0,
        "published_without_report": 0,
    }
    assert m["duplicate_coordinate_groups"] == 1
    assert m["location_precision_exact"] == {
        "exact": 1,
        "geocoded": 2,
        "pct": 50.0,
        "with_precision": 2,
        "coverage_pct": 100.0,
    }
    assert m["display_name_overrides"] == {
        "overrides": 1,
        "total": 2,
        "pct": 50.0,
        "candidates": 2,
        "candidate_coverage_pct": 100.0,
        "conversion_pct": 50.0,
    }
    assert m["spot_check_discrepancy_rate"] == {"discrepancies": 1, "schools_checked": 2, "rate": 0.5}
    assert m["pricing_rows_failing_gates"] == {
        "publishable": 1,
        "failing": 2,
        "total": 3,
        "pct": pytest.approx(66.7),
    }


async def test_city_scope_excludes_other_cities(quality_fixture):
    m = await compute_quality_metrics(quality_fixture, country="bg", city="plovdiv")
    assert m["schools_in_scope"] == 1
    assert m["location_precision_exact"]["with_precision"] == 1
    assert m["display_name_overrides"] == {
        "overrides": 0,
        "total": 1,
        "pct": 0.0,
        "candidates": 0,
        "candidate_coverage_pct": 0.0,
        "conversion_pct": None,
    }
    assert m["duplicate_coordinate_groups"] == 0


async def test_missing_reports_and_precision_count_against_coverage(db_session):
    schools = [await _make_school(db_session) for _ in range(10)]
    schools[0].attributes = {"data_validation": {"status": "ok"}}
    schools[1].attributes = {"data_validation": {"status": "ok"}}
    db_session.add_all(
        [
            SchoolLocation(
                school_id=school.id,
                address_i18n={"bg": f"ул. {index}"},
                lat=42.7 + index / 1000,
                lng=23.3 + index / 1000,
                geocode_meta={"precision": "exact"} if index == 0 else {},
            )
            for index, school in enumerate(schools)
        ]
    )
    await db_session.commit()

    metrics = await compute_quality_metrics(db_session, country="bg", city="sofia")

    assert metrics["validation_ok"] == {
        "ok": 2,
        "total": 10,
        "pct": 20.0,
        "with_report": 2,
        "coverage_pct": 20.0,
    }
    assert metrics["location_precision_exact"] == {
        "exact": 1,
        "geocoded": 10,
        "pct": 10.0,
        "with_precision": 1,
        "coverage_pct": 10.0,
    }


async def test_website_validation_coverage_excludes_registry_only_and_withheld_data(db_session):
    publishable = await _make_school(
        db_session,
        scrape_status="extracted",
        website_url="https://publishable.bg",
        attributes={"extracted": {"programs": ["STEM"]}},
    )
    await _make_school(
        db_session,
        scrape_status="no_official_website",
        attributes={"extracted": {"programs": ["Legacy"]}},
    )
    await _make_school(
        db_session,
        scrape_status="extracted",
        website_url="https://withheld.bg",
        attributes={
            "website_data_withheld": True,
            "extracted": {"programs": ["Stored"]},
        },
    )
    await db_session.commit()

    metrics = await compute_quality_metrics(db_session, country="bg", city="sofia")

    assert publishable.id is not None
    assert metrics["website_validation_coverage"] == {
        "eligible": 0,
        "with_report": 0,
        "coverage_pct": None,
        "ok": 0,
        "ok_pct": None,
        "published_without_report": 0,
    }


async def test_pricing_failure_metric_matches_fail_closed_publication_gate(db_session):
    school = await _make_school(db_session)
    page = await _evidence_page(db_session, school)
    contexts = [
        _verified_context(0.9),
        {},
        _verified_context(True),
        _verified_context("0.9"),
        _verified_context(1.1),
        _verified_context(0.69),
    ]
    db_session.add_all(
        [
            Pricing(
                school_id=school.id,
                category=PriceCategory.TUITION,
                period=PricePeriod.MONTHLY,
                amount=500 + index,
                source=PriceSource.OFFICIAL,
                source_url="https://example.com/fees",
                scraped_at=PRICING_VERIFIED_AT,
                source_page_id=page.id,
                pricing_context=context,
            )
            for index, context in enumerate(contexts)
        ]
    )
    db_session.add(
        Pricing(
            school_id=school.id,
            category=PriceCategory.FOOD,
            period=PricePeriod.MONTHLY,
            amount=100,
            source=PriceSource.SCRAPED_WEBSITE,
            source_url="https://example.com/fees",
            source_page_id=page.id,
            pricing_context={"confidence": 0.99},
        )
    )
    await db_session.commit()

    metrics = await compute_quality_metrics(db_session, country="bg", city="sofia")

    # Two rows clear the gate: the confident curated row and the confident
    # school-website row, which no longer needs a curator to publish.
    assert metrics["pricing_rows_failing_gates"] == {
        "publishable": 2,
        "failing": 5,
        "total": 7,
        "pct": pytest.approx(71.4),
    }


async def test_scoreboard_publishable_pricing_matches_schema_serialization(db_session):
    school = await _make_school(db_session)
    verified_page = await _evidence_page(db_session, school, url="https://example.com/verified-fees")
    scraped_page = await _evidence_page(db_session, school, url="https://example.com/scraped-fees")
    rows = [
        Pricing(
            school_id=school.id,
            category=PriceCategory.TUITION,
            period=PricePeriod.YEARLY,
            amount=5000,
            source=PriceSource.OFFICIAL,
            source_url="https://example.com/verified-fees",
            scraped_at=PRICING_VERIFIED_AT,
            source_page_id=verified_page.id,
            pricing_context=_verified_context(1.0),
        ),
        Pricing(
            school_id=school.id,
            category=PriceCategory.FOOD,
            period=PricePeriod.MONTHLY,
            amount=100,
            source=PriceSource.OFFICIAL,
            source_url="https://example.com/verified-fees",
            scraped_at=PRICING_VERIFIED_AT,
            source_page_id=verified_page.id,
            pricing_context=_verified_context(0.5),
        ),
        Pricing(
            school_id=school.id,
            category=PriceCategory.TRANSPORT,
            period=PricePeriod.YEARLY,
            amount=900,
            source=PriceSource.SCRAPED_WEBSITE,
            source_url="https://example.com/scraped-fees",
            source_page_id=scraped_page.id,
            pricing_context={"confidence": 0.99},
        ),
    ]
    db_session.add_all(rows)
    await db_session.commit()

    public = SchoolListResponse.model_validate(
        {
            "id": school.id,
            "country_code": school.country_code,
            "name_i18n": school.name_i18n,
            "school_type": school.school_type,
            "education_level": school.education_level,
            "attributes": school.attributes,
            "pricing": rows,
        }
    )
    metrics = await compute_quality_metrics(db_session, country="bg", city="sofia")

    # Curated and school-website rows both publish; the low-confidence row does not.
    # Undated rows tie on year, so the deterministic order falls through to category
    # ("transport" before "tuition").
    assert [(row.source, row.category) for row in public.pricing] == [
        (PriceSource.SCRAPED_WEBSITE, PriceCategory.TRANSPORT),
        (PriceSource.OFFICIAL, PriceCategory.TUITION),
    ]
    assert metrics["pricing_rows_failing_gates"]["publishable"] == len(public.pricing)


async def test_scraped_pricing_never_counts_as_publishable_website_data(db_session):
    publishable = await _make_school(db_session, scrape_status="extracted", attributes={})
    withheld = await _make_school(db_session, scrape_status="extracted", attributes={})
    db_session.add_all(
        [
            Pricing(
                school_id=publishable.id,
                category=PriceCategory.TUITION,
                period=PricePeriod.MONTHLY,
                amount=500,
                source=PriceSource.SCRAPED_WEBSITE,
                source_url="https://example.com/fees",
                pricing_context={"confidence": 0.9},
            ),
            Pricing(
                school_id=withheld.id,
                category=PriceCategory.TUITION,
                period=PricePeriod.MONTHLY,
                amount=500,
                source=PriceSource.SCRAPED_WEBSITE,
                source_url=None,
                pricing_context={"confidence": "0.9"},
            ),
        ]
    )
    await db_session.commit()

    metrics = await compute_quality_metrics(db_session, country="bg", city="sofia")

    assert metrics["website_validation_coverage"] == {
        "eligible": 0,
        "with_report": 0,
        "coverage_pct": None,
        "ok": 0,
        "ok_pct": None,
        "published_without_report": 0,
    }


async def test_validation_coverage_tracks_launch_flags_and_public_projection(
    db_session, monkeypatch
):
    from app.config import get_settings

    await _make_school(
        db_session,
        scrape_status="summarized",
        summary_i18n={"bg": {"short": "Кратко", "long": "Дълго"}},
        attributes={"data_validation": {"_schema_version": 1, "status": "ok"}},
    )
    await _make_school(
        db_session,
        scrape_status="extracted",
        attributes={
            "data_validation": {"_schema_version": 1, "status": "needs_review"},
            "extracted": {
                "admission": {
                    "deadlines": ["30 юни"],
                    "application_steps": ["Internal-only child"],
                }
            }
        },
    )
    await _make_school(
        db_session,
        scrape_status="extracted",
        attributes={"extracted": {"contact": {"phone": "+359 2 000 0000"}}},
    )
    await db_session.commit()

    settings = get_settings()
    monkeypatch.setattr(settings, "publish_summaries", False)
    monkeypatch.setattr(settings, "publish_website_admission_fields", False)
    hidden = await compute_quality_metrics(db_session, country="bg", city="sofia")
    assert hidden["website_validation_coverage"]["eligible"] == 0

    monkeypatch.setattr(settings, "publish_summaries", True)
    summary_enabled = await compute_quality_metrics(db_session, country="bg", city="sofia")
    assert summary_enabled["website_validation_coverage"] == {
        "eligible": 1,
        "with_report": 1,
        "coverage_pct": 100.0,
        "ok": 1,
        "ok_pct": 100.0,
        "published_without_report": 0,
    }
    monkeypatch.setattr(settings, "publish_website_admission_fields", True)
    admissions_enabled = await compute_quality_metrics(db_session, country="bg", city="sofia")
    assert admissions_enabled["website_validation_coverage"] == {
        "eligible": 2,
        "with_report": 2,
        "coverage_pct": 100.0,
        "ok": 1,
        "ok_pct": 50.0,
        "published_without_report": 0,
    }


async def test_website_publication_requires_current_validation_report():
    attributes = {
        "has_canteen": True,
        "extracted": {"facilities": ["Website library"]},
    }

    assert website_data_is_publishable(attributes, "extracted") is False
    assert attributes_for_publication(attributes, "extracted") == {"has_canteen": True}

    attributes["data_validation"] = {"_schema_version": 1, "status": "ok"}
    assert website_data_is_publishable(attributes, "extracted") is True
    assert attributes_for_publication(attributes, "extracted")["extracted"] == {
        "facilities": ["Website library"]
    }


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

    summaries = [
        {
            "processed": 2,
            "succeeded": 1,
            "failed": 1,
            "skipped": 0,
            "input_tokens": 120,
            "output_tokens": 30,
            "token_cost_usd": 0.012345,
        },
        {
            "processed": 1,
            "succeeded": 1,
            "failed": 0,
            "skipped": 0,
            "input_tokens": 80,
            "output_tokens": 20,
            "token_cost_usd": 0.004321,
        },
    ]
    finalized = await finalize_pipeline_run(
        quality_fixture, run, country="bg", city="sofia", stage_summaries=summaries
    )

    assert finalized.status == PipelineStatus.PARTIAL  # some succeeded, some failed
    assert finalized.completed_at is not None
    assert finalized.schools_succeeded == 2
    assert finalized.schools_failed == 1
    assert finalized.total_llm_cost_usd == pytest.approx(0.016666)
    assert finalized.metrics["llm_usage"] == {
        "input_tokens": 200,
        "output_tokens": 50,
        "token_cost_usd": pytest.approx(0.016666),
    }
    assert finalized.metrics["validation_ok"] == {
        "ok": 1,
        "total": 2,
        "pct": 50.0,
        "with_report": 2,
        "coverage_pct": 100.0,
    }


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


async def test_pipeline_run_all_failures_not_recorded_completed(quality_fixture):
    """Finding 1 regression: a stage that fails every school must not read COMPLETED."""
    run = await start_pipeline_run(quality_fixture, country="bg", city="sofia", cli_stage="validate-urls")
    finalized = await finalize_pipeline_run(
        quality_fixture, run, country="bg", city="sofia",
        stage_summaries=[{"processed": 5, "succeeded": 0, "failed": 5, "skipped": 0}],
    )
    assert finalized.status == PipelineStatus.FAILED
    assert finalized.schools_processed == 5
    assert finalized.schools_failed == 5


async def test_navigate_summary_converts_results_list():
    """`navigate` keeps returning its list; the dispatch derives real counts from it."""
    from app.scrapers.cli import _navigate_summary

    summary = _navigate_summary(
        [
            {"school_id": 1, "success": True},
            {"school_id": 2, "success": False},
            {"school_id": 3, "success": True},
        ]
    )
    assert summary == {"processed": 3, "succeeded": 2, "failed": 1, "skipped": 0}
    assert _navigate_summary(None) == {"processed": 0, "succeeded": 0, "failed": 0, "skipped": 0}


async def test_recent_run_cost_prefers_usage_metrics_with_legacy_fallback():
    from app.scrapers.cli import _pipeline_run_cost_usd

    run = SimpleNamespace(
        metrics={"llm_usage": {"token_cost_usd": 0.123456}},
        total_llm_cost_usd=9.0,
    )
    assert _pipeline_run_cost_usd(run) == pytest.approx(0.123456)

    legacy = SimpleNamespace(metrics={"validation_ok": {}}, total_llm_cost_usd=0.25)
    assert _pipeline_run_cost_usd(legacy) == pytest.approx(0.25)


async def test_pipeline_usage_rejects_negative_and_non_finite_values():
    usage = _aggregate_usage(
        [
            {"input_tokens": -10, "output_tokens": "bad", "token_cost_usd": float("nan")},
            {"input_tokens": 25, "output_tokens": 5, "token_cost_usd": 0.01},
        ]
    )
    assert usage == {"input_tokens": 25, "output_tokens": 5, "token_cost_usd": 0.01}


async def test_live_pipeline_heartbeat_is_not_terminalized(db_session):
    run = await start_pipeline_run(db_session, country="bg", city="sofia", cli_stage="all")
    assert await heartbeat_pipeline_run(db_session, run.id) is True
    heartbeat = run.heartbeat_at

    recovered = await terminalize_stale_pipeline_runs(
        db_session,
        stale_before=heartbeat - timedelta(seconds=1),
        now=heartbeat + timedelta(seconds=30),
    )

    assert recovered == []
    assert run.status == PipelineStatus.RUNNING
    assert run.completed_at is None


async def test_stale_partial_run_uses_checkpoint_evidence(db_session):
    run = await start_pipeline_run(db_session, country="bg", city="sofia", cli_stage="all")
    await checkpoint_pipeline_run(
        db_session,
        run,
        completed_stage="navigate",
        stage_summaries=[{"processed": 3, "succeeded": 2, "failed": 1, "skipped": 0}],
    )
    run.heartbeat_at = datetime(2026, 7, 20, 8, 0, tzinfo=timezone.utc)
    await db_session.commit()

    recovered = await terminalize_stale_pipeline_runs(
        db_session,
        stale_before=datetime(2026, 7, 20, 8, 5, tzinfo=timezone.utc),
        now=datetime(2026, 7, 20, 8, 10, tzinfo=timezone.utc),
    )

    assert [item.id for item in recovered] == [run.id]
    assert run.status == PipelineStatus.PARTIAL
    assert run.last_completed_stage == "navigate"
    assert (run.schools_processed, run.schools_succeeded, run.schools_failed) == (3, 2, 1)
    assert "stale heartbeat" in run.terminalization_reason


async def test_stale_empty_run_is_failed(db_session):
    run = await start_pipeline_run(db_session, country="bg", city="sofia", cli_stage="extract")
    run.heartbeat_at = datetime(2026, 7, 20, 8, 0, tzinfo=timezone.utc)
    await db_session.commit()

    recovered = await terminalize_stale_pipeline_runs(
        db_session,
        stale_before=datetime(2026, 7, 20, 8, 5, tzinfo=timezone.utc),
        now=datetime(2026, 7, 20, 8, 10, tzinfo=timezone.utc),
    )

    assert [item.id for item in recovered] == [run.id]
    assert run.status == PipelineStatus.FAILED
    assert run.last_completed_stage is None
    assert run.schools_processed == run.schools_succeeded == run.schools_failed == 0


async def test_stale_terminalization_is_idempotent(db_session):
    run = await start_pipeline_run(db_session, country="bg", city="sofia", cli_stage="all")
    run.heartbeat_at = datetime(2026, 7, 20, 8, 0, tzinfo=timezone.utc)
    await db_session.commit()
    kwargs = {
        "stale_before": datetime(2026, 7, 20, 8, 5, tzinfo=timezone.utc),
        "now": datetime(2026, 7, 20, 8, 10, tzinfo=timezone.utc),
    }

    first = await terminalize_stale_pipeline_runs(db_session, **kwargs)
    completed_at = run.completed_at
    reason = run.terminalization_reason
    second = await terminalize_stale_pipeline_runs(db_session, **kwargs)

    assert [item.id for item in first] == [run.id]
    assert second == []
    assert run.completed_at == completed_at
    assert run.terminalization_reason == reason
