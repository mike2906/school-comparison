"""Run one pipeline stage for a school or a batch, inside a tracked pipeline run."""

from __future__ import annotations

import logging
from contextlib import nullcontext
from typing import Optional

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from app.scrapers.stages import all_stages
from app.scrapers.stages import common as stage_common
from app.scrapers.stages import exams as exams_stage
from app.scrapers.stages import extract as extract_stage
from app.scrapers.stages import navigate as navigate_stage
from app.scrapers.stages import summarize as summarize_stage
from app.scrapers.stages import validate_data as validate_data_stage
from app.scrapers.stages import website as website_stage
from app.services.pipeline_runs import (
    finalize_pipeline_run,
    pipeline_run_heartbeat,
    stage_is_tracked,
    start_pipeline_run,
)
from app.services.provider_costs import provider_cost_scope

logger = logging.getLogger(__name__)
console = Console()


BILLABLE_STAGES = frozenset(
    {
        "recover-failed-urls",
        "validate-urls",
        "extract",
        "validate-data",
        "summarize",
        "all",
    }
)


async def run_sync(
    school_name,
    school_id,
    stage,
    city,
    country,
    limit,
    year,
    history_years,
    exam_types: list[str],
    sample_ratio,
    include_navigated: bool,
    include_extracted: bool,
    force_validate: bool,
    skip_summarize: bool = False,
    cohort_ids: Optional[list[int]] = None,
    provider_cost_cap_usd: Optional[float] = None,
):
    """Run pipeline stage synchronously."""
    from app.config import get_settings
    from app.database import async_session_maker

    if (
        stage in BILLABLE_STAGES
        and provider_cost_cap_usd is None
    ):
        raise ValueError("provider_cost_cap_usd is required for billable stages")

    async with async_session_maker() as db:
        if school_name or school_id:
            # Single school mode
            if school_name:
                school_id = await stage_common.find_school_by_name(db, school_name, country)
                if not school_id:
                    console.print(f"[red]School not found: {school_name}[/red]")
                    return

            console.print(f"[cyan]Running stage '{stage}' for school ID {school_id}[/cyan]")
            single_run = None
            if stage_is_tracked(stage):
                single_run = await start_pipeline_run(
                    db,
                    country=country,
                    city=city,
                    cli_stage=stage,
                    config={
                        "school_id": school_id,
                        "provider_cost_cap_usd": provider_cost_cap_usd,
                    },
                )
            heartbeat_context = (
                pipeline_run_heartbeat(
                    single_run.id,
                    session_factory=async_session_maker,
                    interval_seconds=float(get_settings().pipeline_heartbeat_interval_seconds),
                )
                if single_run is not None
                else nullcontext()
            )
            cost_context = (
                provider_cost_scope(
                    pipeline_run_id=single_run.id,
                    stage=stage,
                    cap_usd=provider_cost_cap_usd,
                    request_reserve_usd=float(get_settings().provider_request_reserve_usd),
                )
                if single_run is not None and provider_cost_cap_usd is not None
                else nullcontext()
            )
            single_error: Optional[str] = None
            with cost_context:
                async with heartbeat_context:
                    with Progress(
                        SpinnerColumn(),
                        TextColumn("[progress.description]{task.description}"),
                        console=console,
                    ) as progress:
                        task = progress.add_task(f"Processing school {school_id}...", total=None)
                        try:
                            if stage == "discover":
                                console.print("[yellow]Discover stage runs in batch mode only[/yellow]")
                            elif stage == "discover-websites":
                                await website_stage.run_discover_website(db, school_id, country)
                                await website_stage.check_shared_sites(db, country=country, school_ids=[school_id])
                            elif stage == "recover-failed-urls":
                                await website_stage.run_recover_failed_school(db, school_id, country)
                                await website_stage.check_shared_sites(db, country=country, school_ids=[school_id])
                            elif stage == "validate-urls":
                                await website_stage.run_validate_url(db, school_id, country)
                            elif stage == "navigate":
                                await navigate_stage.run_navigate_school(db, school_id, country)
                            elif stage == "extract":
                                await extract_stage.run_extract_school(db, school_id, country)
                            elif stage == "validate-data":
                                await validate_data_stage.run_validate_data_school(
                                    db, school_id, country, run_spot_check=True
                                )
                            elif stage == "summarize":
                                await summarize_stage.run_summarize_school(db, school_id, country)
                            elif stage == "nvo":
                                await exams_stage.run_nvo_import(
                                    db,
                                    country=country,
                                    city=city,
                                    year=year,
                                    history_years=history_years,
                                    exam_types=exam_types,
                                    school_ids=[school_id],
                                )
                            elif stage == "dzi":
                                await exams_stage.run_dzi_import(
                                    db,
                                    country=country,
                                    city=city,
                                    year=year,
                                    history_years=history_years,
                                    school_ids=[school_id],
                                )
                            elif stage == "all":
                                await all_stages.run_all_stages(db, school_id, country)

                            progress.update(task, completed=True)
                            console.print(f"[green]✓ Completed stage '{stage}' for school {school_id}[/green]")
                        except Exception as exc:
                            single_error = str(exc)
                            console.print(f"[red]✗ Error: {single_error}[/red]")
                            logger.exception("Stage execution failed")
                            if db.in_transaction():
                                await db.rollback()
                        finally:
                            if single_run is not None:
                                await finalize_pipeline_run(
                                    db,
                                    single_run,
                                    country=country,
                                    city=city,
                                    stage_summaries=[
                                        stage_common.stage_summary(
                                            processed=1,
                                            succeeded=0 if single_error else 1,
                                            failed=1 if single_error else 0,
                                        )
                                    ],
                                    error_summary=single_error,
                                )
                                console.print(
                                    f"[dim]Recorded pipeline run {single_run.id} "
                                    f"({single_run.status.value}).[/dim]"
                                )

        else:
            # Batch mode
            console.print(f"[cyan]Running stage '{stage}' in batch mode[/cyan]")
            console.print(f"  City: {city}")
            console.print(f"  Limit: {limit or 'all'}")

            # Record a PipelineRun (+ quality-metrics snapshot) around tracked stages.
            run = None
            if stage_is_tracked(stage):
                run = await start_pipeline_run(
                    db,
                    country=country,
                    city=city,
                    cli_stage=stage,
                    config={
                        "limit": limit,
                        "include_navigated": include_navigated,
                        "include_extracted": include_extracted,
                        "force_validate": force_validate,
                        "skip_summarize": skip_summarize,
                        "explicit_cohort": cohort_ids is not None,
                        "provider_cost_cap_usd": provider_cost_cap_usd,
                    },
                )

            stage_summaries: list = []
            run_error: Optional[str] = None
            raised: Optional[BaseException] = None
            heartbeat_context = (
                pipeline_run_heartbeat(
                    run.id,
                    session_factory=async_session_maker,
                    interval_seconds=float(get_settings().pipeline_heartbeat_interval_seconds),
                )
                if run is not None
                else nullcontext()
            )
            cost_context = (
                provider_cost_scope(
                    pipeline_run_id=run.id,
                    stage=stage,
                    cap_usd=provider_cost_cap_usd,
                    request_reserve_usd=float(get_settings().provider_request_reserve_usd),
                )
                if run is not None and provider_cost_cap_usd is not None
                else nullcontext()
            )
            with cost_context:
                async with heartbeat_context:
                    try:
                        if stage == "discover":
                            stage_summaries.append(
                                await website_stage.run_discover_batch(db, country, city, limit, sample_ratio)
                            )
                            # A crashed adapter is counted, not raised; scheduled imports need the exit code.
                            if stage_summaries[-1].get("failed"):
                                raise RuntimeError(f"{stage_summaries[-1]['failed']} discovery adapter(s) failed")
                        elif stage == "discover-websites":
                            stage_summaries.append(await website_stage.run_discover_websites_batch(db, country, city, limit))
                        elif stage == "recover-failed-urls":
                            stage_summaries.append(await website_stage.run_recover_failed_urls_batch(db, country, city, limit))
                        elif stage == "validate-urls":
                            stage_summaries.append(await website_stage.run_validate_urls_batch(db, country, city, limit))
                        elif stage == "navigate":
                            stage_summaries.append(
                                stage_common.navigate_summary(
                                    await navigate_stage.run_navigate_batch(
                                        db, country, city, limit, include_navigated=include_navigated
                                    )
                                )
                            )
                        elif stage == "extract":
                            stage_summaries.append(
                                await extract_stage.run_extract_batch(
                                    db, country, city, limit, include_extracted=include_extracted
                                )
                            )
                        elif stage == "validate-data":
                            stage_summaries.append(
                                await validate_data_stage.run_validate_data_batch(
                                    db, country, city, limit, force_validate=force_validate
                                )
                            )
                        elif stage == "summarize":
                            stage_summaries.append(await summarize_stage.run_summarize_batch(db, country, city, limit))
                        elif stage == "nvo":
                            await exams_stage.run_nvo_import(
                                db,
                                country=country,
                                city=city,
                                year=year,
                                history_years=history_years,
                                exam_types=exam_types,
                                school_ids=None,
                            )
                        elif stage == "dzi":
                            await exams_stage.run_dzi_import(
                                db,
                                country=country,
                                city=city,
                                year=year,
                                history_years=history_years,
                                school_ids=None,
                            )
                        elif stage == "all":
                            await all_stages.run_all_stages_batch(
                                db,
                                country=country,
                                city=city,
                                limit=limit,
                                include_navigated=include_navigated,
                                include_extracted=include_extracted,
                                force_validate=force_validate,
                                skip_summarize=skip_summarize,
                                requested_school_ids=cohort_ids,
                                pipeline_run=run,
                                stage_summaries=stage_summaries,
                            )
                        else:
                            console.print(f"[yellow]Batch mode for '{stage}' not yet implemented[/yellow]")
                    except Exception as exc:
                        raised = exc
                        run_error = str(exc)
                        logger.exception("Batch stage execution failed")
                        console.print(f"[red]✗ Error: {run_error}[/red]")
                        # A stage that raised mid-transaction leaves the session unusable;
                        # clear it so the run can still be finalized.
                        if db.in_transaction():
                            await db.rollback()
                    finally:
                        if run is not None:
                            await finalize_pipeline_run(
                                db,
                                run,
                                country=country,
                                city=city,
                                stage_summaries=stage_summaries,
                                error_summary=run_error,
                            )
                            console.print(f"[dim]Recorded pipeline run {run.id} ({run.status.value}).[/dim]")
            # Batch mode is the automation path: a stage failure must exit non-zero.
            if raised is not None:
                raise raised
