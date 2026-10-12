"""Navigate stage runners: crawl each school's website and record terminal failures."""

from __future__ import annotations

import asyncio
import builtins
import datetime
import logging
import time
from math import ceil
from typing import Optional

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

logger = logging.getLogger(__name__)
console = Console()


def _navigation_failure_result(
    *,
    school_id: int,
    url: str | None,
    reason: str,
    timeout_phase: str | None,
    elapsed_seconds: float,
    attempt_count: int,
) -> dict:
    return {
        "school_id": school_id,
        "success": False,
        "reason": reason,
        "final_reason": reason,
        "url": url,
        "page_url": url,
        "timeout_phase": timeout_phase,
        "elapsed_seconds": round(max(0.0, elapsed_seconds), 3),
        "attempt_count": max(1, attempt_count),
    }


async def _persist_navigation_terminal_telemetry(
    db, results: list[dict], urls: dict[int, str | None]
) -> None:
    """Persist final per-school navigation evidence without broadening pipeline work."""
    from sqlalchemy import select
    from sqlalchemy.orm.attributes import flag_modified

    from app.models import School

    ids = [
        int(result["school_id"])
        for result in results
        if result and result.get("school_id")
    ]
    if not ids:
        return
    schools = builtins.list((await db.execute(select(School).where(School.id.in_(ids)))).scalars())
    result_by_id = {
        int(result["school_id"]): result
        for result in results
        if result and result.get("school_id")
    }
    for school in schools:
        result = result_by_id[school.id]
        attrs = dict(school.attributes or {})
        if result.get("success"):
            attrs.pop("navigation_terminal_failure", None)
        else:
            attrs["navigation_terminal_failure"] = {
                "url": result.get("url") or urls.get(school.id),
                "page_url": result.get("page_url") or result.get("url") or urls.get(school.id),
                "timeout_phase": result.get("timeout_phase"),
                "elapsed_seconds": float(result.get("elapsed_seconds", 0.0) or 0.0),
                "attempt_count": int(result.get("attempt_count", 1) or 1),
                "final_reason": (
                    result.get("final_reason")
                    or result.get("reason")
                    or "Navigation failed"
                ),
                "recorded_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }
        school.attributes = attrs
        flag_modified(school, "attributes")
        db.add(school)
    await db.commit()


async def run_navigate_school(db, school_id: int, country: str):
    """Run navigation stage for a single school."""
    from app.scrapers.navigator import navigate_school

    console.print(f"  Navigating website for school {school_id}...")
    result = await navigate_school(db=db, school_id=school_id, country_code=country)

    if result.get("success"):
        console.print(
            f"  Result: pages={result.get('pages_found', 0)}, "
            f"created={result.get('created', 0)}, updated={result.get('updated', 0)}, "
            f"cache_hits={result.get('cache_hits', 0)}"
        )
    else:
        console.print(f"[yellow]  Skipped: {result.get('reason', 'Unknown reason')}[/yellow]")
    return result


async def run_navigate_batch(
    db,
    country: str,
    city: str,
    limit: Optional[int],
    include_navigated: bool = False,
    school_ids: Optional[list[int]] = None,
    skip_timed_out_chunks: bool = False,
):
    """Run navigation stage in batch mode."""
    from sqlalchemy import select

    from app.models import School
    from app.scrapers.navigator import navigate_schools_batch

    query = select(School).where(
        School.country_code == country,
        School.website_url.isnot(None),
    )

    explicit_selection = school_ids is not None
    explicit_school_ids = builtins.list(school_ids or [])
    if explicit_selection:
        query = query.where(School.id.in_(explicit_school_ids))
    else:
        statuses = ["validated", "navigated"] if include_navigated else ["validated"]
        query = query.where(School.scrape_status.in_(statuses))

    if city:
        query = query.where(School.city == city)

    if limit:
        query = query.limit(limit)

    result = await db.execute(query)
    schools = result.scalars().all()
    school_ids = [school.id for school in schools]
    school_urls = {school.id: school.website_url for school in schools}

    if not school_ids:
        console.print("[yellow]No schools to navigate[/yellow]")
        return

    if explicit_selection:
        status_label = "explicit repair selection"
    else:
        status_label = "validated+navigated" if include_navigated else "validated"
    console.print(f"[cyan]Navigating websites for {len(school_ids)} schools...[/cyan]")
    console.print(f"  Status filter: {status_label}")

    results: list[dict] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Navigating websites...", total=len(school_ids))

        success_count = 0
        fail_count = 0

        from app.config import get_settings as _get_settings

        settings = _get_settings()
        _nav_timeout = settings.nav_school_timeout_seconds
        nav_batch_concurrency = max(1, int(getattr(settings, "nav_batch_concurrency", 3)))
        console.print(f"  Batch crawl concurrency: {nav_batch_concurrency}")

        async def _navigate_chunk_individually(
            school_chunk: list[int],
            *,
            prior_phase: str,
            prior_elapsed: float,
        ) -> list[dict]:
            isolated: list[dict] = []
            for isolated_school_id in school_chunk:
                started = time.monotonic()
                try:
                    coro = run_navigate_school(db, isolated_school_id, country)
                    if _nav_timeout > 0:
                        item = await asyncio.wait_for(coro, timeout=_nav_timeout)
                    else:
                        item = await coro
                    item = dict(item or {})
                    item.setdefault("school_id", isolated_school_id)
                    item.setdefault("url", school_urls.get(isolated_school_id))
                    item.setdefault("page_url", item.get("url"))
                    item.setdefault("elapsed_seconds", round(time.monotonic() - started, 3))
                    item.setdefault("attempt_count", 2)
                    item.setdefault(
                        "timeout_phase", prior_phase if not item.get("success") else None
                    )
                    item.setdefault("final_reason", item.get("reason"))
                except asyncio.TimeoutError:
                    elapsed = time.monotonic() - started
                    logger.error(
                        "Navigation timed out after %.0fs for school %s", _nav_timeout, isolated_school_id
                    )
                    await db.rollback()
                    item = _navigation_failure_result(
                        school_id=isolated_school_id,
                        url=school_urls.get(isolated_school_id),
                        reason="Navigation timeout",
                        timeout_phase="school_retry",
                        elapsed_seconds=prior_elapsed + elapsed,
                        attempt_count=2,
                    )
                except Exception as exc:
                    elapsed = time.monotonic() - started
                    logger.error("Error navigating school %s: %s", isolated_school_id, exc)
                    await db.rollback()
                    item = _navigation_failure_result(
                        school_id=isolated_school_id,
                        url=school_urls.get(isolated_school_id),
                        reason=str(exc),
                        timeout_phase=(
                            prior_phase if "timeout" in str(exc).lower() else None
                        ),
                        elapsed_seconds=prior_elapsed + elapsed,
                        attempt_count=2,
                    )
                isolated.append(item)
            return isolated

        if skip_timed_out_chunks and explicit_school_ids:
            timed_out_school_ids: list[int] = []
            for chunk_start in range(0, len(school_ids), nav_batch_concurrency):
                school_chunk = school_ids[chunk_start : chunk_start + nav_batch_concurrency]
                chunk_started = time.monotonic()
                try:
                    coro = navigate_schools_batch(
                        db=db,
                        school_ids=school_chunk,
                        country_code=country,
                        max_concurrency=nav_batch_concurrency,
                    )
                    if _nav_timeout > 0:
                        chunk_timeout = _nav_timeout * max(
                            1, ceil(len(school_chunk) / nav_batch_concurrency)
                        )
                        chunk_results = await asyncio.wait_for(coro, timeout=chunk_timeout)
                    else:
                        chunk_results = await coro
                except asyncio.TimeoutError:
                    chunk_elapsed = time.monotonic() - chunk_started
                    logger.error(
                        "Navigation timed out after %.0fs for school chunk %s",
                        _nav_timeout,
                        school_chunk,
                    )
                    await db.rollback()
                    chunk_results = await _navigate_chunk_individually(
                        school_chunk,
                        prior_phase="batch_chunk",
                        prior_elapsed=chunk_elapsed,
                    )
                    timed_out_school_ids.extend(
                        int(item["school_id"])
                        for item in chunk_results
                        if not item.get("success") and item.get("timeout_phase")
                    )
                except Exception as exc:
                    logger.warning(
                        "Batch navigation chunk failed for %s; retrying sequentially: %s",
                        school_chunk,
                        exc,
                    )
                    await db.rollback()
                    chunk_results = await _navigate_chunk_individually(
                        school_chunk,
                        prior_phase="batch_chunk_error",
                        prior_elapsed=time.monotonic() - chunk_started,
                    )

                results.extend(chunk_results)
                for result in chunk_results:
                    if result and result.get("success"):
                        success_count += 1
                    else:
                        fail_count += 1
                    progress.update(task, advance=1)

            if timed_out_school_ids:
                console.print(
                    f"[yellow]  Timed out and skipped: {len(timed_out_school_ids)} schools[/yellow]"
                )
        else:
            try:
                results = await navigate_schools_batch(
                    db=db,
                    school_ids=school_ids,
                    country_code=country,
                    max_concurrency=nav_batch_concurrency,
                )
            except Exception as exc:
                logger.warning("Batch navigation failed; falling back to sequential mode: %s", exc)
                await db.rollback()
                results = []
                for school_id in school_ids:
                    try:
                        coro = run_navigate_school(db, school_id, country)
                        if _nav_timeout > 0:
                            result = await asyncio.wait_for(coro, timeout=_nav_timeout)
                        else:
                            result = await coro
                    except asyncio.TimeoutError:
                        logger.error(
                            "Navigation timed out after %.0fs for school %s", _nav_timeout, school_id
                        )
                        await db.rollback()
                        result = {"school_id": school_id, "success": False, "reason": "Navigation timeout"}
                    except Exception as e:
                        logger.error(f"Error navigating school {school_id}: {e}")
                        await db.rollback()
                        result = {"school_id": school_id, "success": False, "reason": str(e)}
                    results.append(result)

            for result in results:
                if result and result.get("success"):
                    success_count += 1
                else:
                    fail_count += 1
                progress.update(task, advance=1)

    console.print("[green]✓ Navigation complete:[/green]")
    console.print(f"  Successful: {success_count}")
    console.print(f"  Failed: {fail_count}")
    await _persist_navigation_terminal_telemetry(db, results, school_urls)
    return results
