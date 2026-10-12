"""The ``all`` stage: every website stage for one school or one deterministic cohort."""

from __future__ import annotations

import builtins
import logging
from typing import Optional

import click
from rich.console import Console

from app.scrapers.stages import common as stage_common
from app.scrapers.stages import extract as extract_stage
from app.scrapers.stages import navigate as navigate_stage
from app.scrapers.stages import summarize as summarize_stage
from app.scrapers.stages import validate_data as validate_data_stage
from app.scrapers.stages import website as website_stage
from app.services.pipeline_runs import checkpoint_pipeline_run

logger = logging.getLogger(__name__)
console = Console()


async def run_all_stages(db, school_id: int, country: str):
    """Run all pipeline stages for a single school."""
    stages = ["discover-websites", "validate-urls", "navigate", "extract", "validate-data", "summarize"]

    for stage in stages:
        console.print(f"\n[cyan]Stage: {stage}[/cyan]")

        if stage == "discover-websites":
            await website_stage.run_discover_website(db, school_id, country)
            await website_stage.check_shared_sites(db, country=country, school_ids=[school_id])
        elif stage == "validate-urls":
            await website_stage.run_validate_url(db, school_id, country)
        elif stage == "navigate":
            await navigate_stage.run_navigate_school(db, school_id, country)
        elif stage == "extract":
            await extract_stage.run_extract_school(db, school_id, country)
        elif stage == "validate-data":
            await validate_data_stage.run_validate_data_school(db, school_id, country, run_spot_check=True)
        elif stage == "summarize":
            await summarize_stage.run_summarize_school(db, school_id, country)
        else:
            console.print(f"[yellow]{stage} not yet implemented[/yellow]")


async def select_all_stage_cohort(
    db,
    *,
    country: str,
    city: Optional[str],
    limit: Optional[int],
    include_navigated: bool,
    include_extracted: bool,
    force_validate: bool,
    requested_school_ids: Optional[list[int]] = None,
) -> list[int]:
    """Select one deterministic cohort for every stage in a batch ``all`` run."""
    from sqlalchemy import select

    from app.models import School

    query = (
        select(School.id)
        .where(
            School.country_code == country,
            School.website_url.isnot(None),
        )
        .order_by(School.id)
    )
    if city:
        query = query.where(School.city == city)
    if requested_school_ids is not None:
        query = query.where(School.id.in_(requested_school_ids))
    else:
        statuses = {
            "pending",
            "failed_validate",
            "validated",
            "navigated",
            "extraction_failed",
        }
        if include_navigated or include_extracted or force_validate:
            statuses.update({"extracted", "summarized"})
        query = query.where(School.scrape_status.in_(sorted(statuses)))
        if limit is not None:
            query = query.limit(limit)

    selected = [row[0] for row in (await db.execute(query)).all()]
    if requested_school_ids is not None:
        missing = sorted(set(requested_school_ids) - set(selected))
        if missing:
            raise click.UsageError(
                "Cohort IDs are outside the requested country/city scope or have no website URL: "
                + ", ".join(str(school_id) for school_id in missing)
            )
    return selected


async def run_all_stages_batch(
    db,
    *,
    country: str,
    city: Optional[str],
    limit: Optional[int],
    include_navigated: bool,
    include_extracted: bool,
    force_validate: bool,
    skip_summarize: bool = False,
    requested_school_ids: Optional[list[int]] = None,
    pipeline_run=None,
    stage_summaries: Optional[list[dict]] = None,
) -> list[dict]:
    """Run the batch website pipeline over one fixed school-ID cohort.

    The original cohort is recorded for reproducibility. Each subsequent stage is
    narrowed to schools that produced usable fresh output in the preceding stage.
    Summaries are appended as stages finish so a later exception cannot erase usage
    and outcome accounting from already-completed stages.
    """
    school_ids = await select_all_stage_cohort(
        db,
        country=country,
        city=city,
        limit=limit,
        include_navigated=include_navigated,
        include_extracted=include_extracted,
        force_validate=force_validate,
        requested_school_ids=requested_school_ids,
    )
    if not school_ids:
        console.print("[yellow]No schools matched the batch all-stage cohort[/yellow]")
        return []

    if pipeline_run is not None:
        config = dict(pipeline_run.config or {})
        config["cohort_school_ids"] = school_ids
        pipeline_run.config = config
        db.add(pipeline_run)
        await db.commit()

    console.print(
        f"[cyan]Selected fixed all-stage cohort: {len(school_ids)} schools "
        f"(IDs {school_ids[0]}–{school_ids[-1]})[/cyan]"
    )
    completed = stage_summaries if stage_summaries is not None else []

    url_summary = await website_stage.run_validate_urls_batch(
        db,
        country,
        city,
        None,
        school_ids=school_ids,
    )
    completed.append(url_summary)
    if pipeline_run is not None:
        await checkpoint_pipeline_run(
            db, pipeline_run, completed_stage="validate-urls", stage_summaries=completed
        )
    validated_url_ids = builtins.list(url_summary.get("validated_school_ids") or [])

    navigation_results = await navigate_stage.run_navigate_batch(
        db,
        country,
        city or "",
        None,
        include_navigated=include_navigated,
        school_ids=validated_url_ids,
        skip_timed_out_chunks=True,
    )
    completed.append(stage_common.navigate_summary(navigation_results))
    if pipeline_run is not None:
        await checkpoint_pipeline_run(
            db, pipeline_run, completed_stage="navigate", stage_summaries=completed
        )
    navigated_ids = sorted(
        int(result["school_id"])
        for result in (navigation_results or [])
        if result and result.get("success") and result.get("school_id") in school_ids
    )

    extract_summary = await extract_stage.run_extract_batch(
        db,
        country,
        city or "",
        None,
        include_extracted=include_extracted,
        school_ids=navigated_ids,
    )
    completed.append(extract_summary)
    if pipeline_run is not None:
        await checkpoint_pipeline_run(
            db, pipeline_run, completed_stage="extract", stage_summaries=completed
        )
    extracted_ids = builtins.list(extract_summary.get("ready_school_ids") or [])

    validation_summary = await validate_data_stage.run_validate_data_batch(
        db,
        country,
        city,
        None,
        force_validate=force_validate,
        school_ids=extracted_ids,
    )
    completed.append(validation_summary)
    if pipeline_run is not None:
        await checkpoint_pipeline_run(
            db, pipeline_run, completed_stage="validate-data", stage_summaries=completed
        )
    validated_ids = builtins.list(validation_summary.get("validated_school_ids") or [])

    if not skip_summarize:
        completed.append(
            await summarize_stage.run_summarize_batch(
                db,
                country,
                city,
                None,
                school_ids=validated_ids,
            )
        )
        if pipeline_run is not None:
            await checkpoint_pipeline_run(
                db, pipeline_run, completed_stage="summarize", stage_summaries=completed
            )
    return completed
