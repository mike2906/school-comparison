"""Extract stage runners."""

from __future__ import annotations

import asyncio
import builtins
import logging
from typing import Optional

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from app.scrapers.stages import common as stage_common

logger = logging.getLogger(__name__)
console = Console()


async def run_extract_school(db, school_id: int, country: str):
    """Run extraction stage for a single school."""
    from app.scrapers.extractor import extract_school

    console.print(f"  Extracting data for school {school_id}...")
    result = await extract_school(db=db, school_id=school_id, country_code=country)
    llm_stats = result.get("llm_stats") or {}

    if result.get("skipped"):
        console.print("[yellow]  Skipped: content unchanged[/yellow]")
    elif result.get("status") == "extracted":
        console.print(
            f"[green]  Success: general_info={result.get('general_info_success')}[/green]"
        )
        if result.get("details"):
            for detail in result.get("details"):
                console.print(f"    [dim]{detail}[/dim]")
    else:
        console.print(f"[red]  Failed: {result.get('error', 'Unknown error')}[/red]")
        if result.get("details"):
            for detail in result.get("details"):
                console.print(f"    [dim]{detail}[/dim]")

    if llm_stats:
        console.print(
            "    [dim]"
            f"LLM calls={llm_stats.get('total_calls', 0)}, "
            f"model_retries={llm_stats.get('model_retries', 0)}, "
            f"hard_fail={llm_stats.get('hard_failures', 0)} "
            f"({llm_stats.get('hard_failure_rate', 0):.2%})"
            "[/dim]"
        )
    return result


async def run_extract_batch(
    db,
    country: str,
    city: str,
    limit: Optional[int],
    include_extracted: bool = False,
    school_ids: Optional[list[int]] = None,
):
    """Run extraction stage in batch mode."""
    from sqlalchemy import select

    from app.models import School

    explicit_selection = school_ids is not None
    explicit_school_ids = builtins.list(school_ids or [])
    query = select(School).where(
        School.country_code == country,
        School.website_url.isnot(None),
    )

    if explicit_selection:
        query = query.where(School.id.in_(explicit_school_ids))
    else:
        statuses = ["navigated", "extraction_failed"]
        if include_extracted:
            statuses.extend(["extracted", "summarized"])
        query = query.where(School.scrape_status.in_(statuses))

    if city:
        query = query.where(School.city == city)

    if limit:
        query = query.limit(limit)

    result = await db.execute(query)
    schools = result.scalars().all()
    school_ids = [school.id for school in schools]

    if not school_ids:
        console.print("[yellow]No schools to extract (must be navigated or extraction_failed)[/yellow]")
        return stage_common.stage_summary()

    if explicit_selection:
        status_label = "explicit repair selection"
    else:
        status_label = "navigated/failed + extracted/summarized" if include_extracted else "navigated/failed"
    console.print(f"[cyan]Extracting data for {len(school_ids)} schools...[/cyan]")
    console.print(f"  Status filter: {status_label}")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Extracting data...", total=len(school_ids))

        success_count = 0
        skipped_count = 0
        fail_count = 0
        ready_school_ids: list[int] = []
        usage = {"input_tokens": 0, "output_tokens": 0, "token_cost_usd": 0.0}

        from app.config import get_settings as _get_settings
        from app.database import async_session_maker
        from app.scrapers.extractor import keep_previous_website_data

        settings = _get_settings()
        _ext_timeout = settings.extraction_school_timeout_seconds
        requested_concurrency = max(1, int(getattr(settings, "extraction_batch_concurrency", 1)))
        max_concurrency = 8
        concurrency = min(requested_concurrency, max_concurrency)

        console.print(f"  Concurrency: {concurrency}")
        if requested_concurrency > max_concurrency:
            console.print(
                f"[yellow]  Requested concurrency {requested_concurrency} capped to {max_concurrency}[/yellow]"
            )

        if concurrency == 1:
            for school_id in school_ids:
                try:
                    coro = run_extract_school(db, school_id, country)
                    if _ext_timeout > 0:
                        result = await asyncio.wait_for(coro, timeout=_ext_timeout)
                    else:
                        result = await coro
                    stage_common.add_llm_usage(usage, result)
                    if result.get("skipped"):
                        skipped_count += 1
                        ready_school_ids.append(school_id)
                    elif result.get("status") == "extracted":
                        success_count += 1
                        ready_school_ids.append(school_id)
                    else:
                        fail_count += 1
                except asyncio.TimeoutError:
                    logger.error(
                        "Extraction timed out after %.0fs for school %s", _ext_timeout, school_id
                    )
                    await db.rollback()
                    try:
                        await keep_previous_website_data(db, school_id)
                    except Exception as exc:
                        logger.error(
                            "Could not restore status after timeout for school %s: %s",
                            school_id,
                            exc,
                        )
                        await db.rollback()
                    fail_count += 1
                except Exception as e:
                    logger.error(f"Error extracting school {school_id}: {e}")
                    await db.rollback()
                    fail_count += 1
                progress.update(task, advance=1)
        else:
            from app.scrapers.extractor import extract_school

            semaphore = asyncio.Semaphore(concurrency)

            async def _extract_single(school_id: int) -> tuple[int, dict]:
                async with semaphore:
                    try:
                        async with async_session_maker() as school_db:
                            coro = extract_school(db=school_db, school_id=school_id, country_code=country)
                            if _ext_timeout > 0:
                                result = await asyncio.wait_for(coro, timeout=_ext_timeout)
                            else:
                                result = await coro
                            return school_id, result
                    except asyncio.TimeoutError:
                        logger.error(
                            "Extraction timed out after %.0fs for school %s", _ext_timeout, school_id
                        )
                        try:
                            async with async_session_maker() as school_db:
                                await keep_previous_website_data(school_db, school_id)
                        except Exception as exc:
                            logger.error(
                                "Could not restore status after timeout for school %s: %s",
                                school_id,
                                exc,
                            )
                        return school_id, {
                            "school_id": school_id,
                            "status": "extraction_failed",
                            "error": "Extraction timeout",
                        }
                    except Exception as exc:
                        logger.error("Error extracting school %s: %s", school_id, exc)
                        return school_id, {
                            "school_id": school_id,
                            "status": "extraction_failed",
                            "error": str(exc),
                        }

            tasks = [asyncio.create_task(_extract_single(school_id)) for school_id in school_ids]
            for task_result in asyncio.as_completed(tasks):
                school_id, result = await task_result
                stage_common.add_llm_usage(usage, result)
                if result.get("skipped"):
                    skipped_count += 1
                    ready_school_ids.append(school_id)
                elif result.get("status") == "extracted":
                    success_count += 1
                    ready_school_ids.append(school_id)
                else:
                    fail_count += 1
                progress.update(task, advance=1)

    console.print("[green]✓ Extraction complete:[/green]")
    console.print(f"  Successful: {success_count}")
    console.print(f"  Skipped: {skipped_count}")
    console.print(f"  Failed: {fail_count}")
    console.print(f"  Tokens: in={usage['input_tokens']}, out={usage['output_tokens']}")
    console.print(f"  Cost: ${usage['token_cost_usd']:.6f}")

    return {
        **stage_common.stage_summary_with_usage(
            processed=len(school_ids),
            succeeded=success_count,
            failed=fail_count,
            skipped=skipped_count,
            usage=usage,
        ),
        "ready_school_ids": sorted(ready_school_ids),
    }
