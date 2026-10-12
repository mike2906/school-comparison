"""Summarize stage runners."""

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


async def run_summarize_school(db, school_id: int, country: str):
    """Run Stage 7 summarization for one school."""
    from app.scrapers.summary_pipeline import summarize_school

    console.print(f"  Summarizing school {school_id}...")
    result = await summarize_school(db=db, school_id=school_id, country_code=country)
    status = result.get("status")
    if status == "summarized":
        console.print(
            f"[green]  Summarized "
            f"(input={int(result.get('input_tokens', 0) or 0)}, "
            f"output={int(result.get('output_tokens', 0) or 0)}, "
            f"cost=${float(result.get('token_cost_usd', 0.0) or 0.0):.6f})[/green]"
        )
    elif status == "skipped":
        console.print(f"[yellow]  Skipped: {result.get('reason', 'No reason provided')}[/yellow]")
    else:
        console.print(f"[red]  Failed: {result.get('reason', 'Unknown error')}[/red]")
    return result


async def run_summarize_batch(
    db,
    country: str,
    city: Optional[str],
    limit: Optional[int],
    school_ids: Optional[list[int]] = None,
):
    """Run Stage 7 summarization in batch mode."""
    from app.config import get_settings as _get_settings
    from app.database import async_session_maker
    from app.scrapers.summary_pipeline import get_schools_requiring_summary, summarize_school

    settings = _get_settings()
    if school_ids is None:
        schools = await get_schools_requiring_summary(db=db, country_code=country, city=city, limit=limit)
        selected_school_ids = [school.id for school in schools]
    else:
        selected_school_ids = builtins.list(school_ids)

    if not selected_school_ids:
        console.print("[yellow]No schools to summarize (all eligible summaries are current or ineligible)[/yellow]")
        return stage_common.stage_summary()

    requested_concurrency = max(1, int(getattr(settings, "summarization_batch_concurrency", 1)))
    max_concurrency = 8
    concurrency = min(requested_concurrency, max_concurrency)
    if requested_concurrency > max_concurrency:
        console.print(
            f"[yellow]Requested concurrency {requested_concurrency} capped to {max_concurrency}[/yellow]"
        )

    console.print(f"[cyan]Summarizing {len(selected_school_ids)} schools...[/cyan]")
    console.print(f"  Concurrency: {concurrency}")

    summarized_count = 0
    skipped_count = 0
    failed_count = 0
    usage = {"input_tokens": 0, "output_tokens": 0, "token_cost_usd": 0.0}
    results: list[dict] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Generating summaries...", total=len(selected_school_ids))
        semaphore = asyncio.Semaphore(concurrency)

        async def _summarize_single(school_id: int) -> tuple[int, dict]:
            async with semaphore:
                async with async_session_maker() as school_db:
                    try:
                        out = await summarize_school(
                            db=school_db,
                            school_id=school_id,
                            country_code=country,
                        )
                    except Exception as exc:
                        logger.error("Summarization failed for school %s: %s", school_id, exc)
                        out = {"status": "summary_failed", "reason": str(exc)}
                    return school_id, out

        tasks = [asyncio.create_task(_summarize_single(school_id)) for school_id in selected_school_ids]
        for completed in asyncio.as_completed(tasks):
            _, out = await completed
            results.append(out)
            stage_common.add_llm_usage(usage, out)
            status = out.get("status")
            if status == "summarized":
                summarized_count += 1
            elif status == "skipped":
                skipped_count += 1
            else:
                failed_count += 1
            progress.update(task, advance=1)

    console.print("[green]✓ Summarization complete:[/green]")
    console.print(f"  Summarized: {summarized_count}")
    console.print(f"  Skipped: {skipped_count}")
    console.print(f"  Failed: {failed_count}")
    console.print(f"  Tokens: in={usage['input_tokens']}, out={usage['output_tokens']}")
    console.print(f"  Cost: ${usage['token_cost_usd']:.6f}")
    return stage_common.stage_summary_with_usage(
        processed=len(selected_school_ids),
        succeeded=summarized_count,
        failed=failed_count,
        skipped=skipped_count,
        usage=usage,
    )
