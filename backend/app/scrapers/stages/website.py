"""Website stages: discovery, website discovery, URL validation, failed-URL recovery and the shared-site check."""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from app.scrapers.stages import common as stage_common

logger = logging.getLogger(__name__)
console = Console()


async def check_shared_sites(
    db,
    *,
    country: str,
    city: Optional[str] = None,
    school_ids: Optional[list[int]] = None,
    dry_run: bool = False,
) -> dict:
    """UF42(a): check websites shared by 2+ institutions; runs after website discovery."""
    from app.scrapers.shared_site_check import run_shared_site_check

    report = await run_shared_site_check(
        db, country=country, city=city, school_ids=school_ids, dry_run=dry_run
    )
    mode = "dry-run" if dry_run else "applied"
    console.print(f"[cyan]Shared-site check ({mode})[/cyan]")
    table = Table(show_header=True, header_style="bold cyan")
    for column in ("Group", "ID", "Level", "Decision", "Reason", "URL"):
        table.add_column(column)
    for group in report["groups"]:
        for row in group["members"]:
            table.add_row(
                group["group"],
                str(row["school_id"]),
                row["education_level"],
                row["action"],
                row["reason"],
                row["new_url"] or row["current_url"],
            )
    if report["groups"]:
        console.print(table)
    counts = report["counts"]
    console.print(
        f"groups={counts['groups']} institutions={counts['institutions']} "
        f"kept={counts['kept']} replaced={counts['replaced']} withheld={counts['withheld']}"
    )
    return report


async def run_discover_batch(db, country: str, city: str, limit: Optional[int], sample_ratio: float):
    """Run discovery stage in batch mode."""
    from app.scrapers.sources import get_adapters_for_country

    adapters = get_adapters_for_country(country, city)

    if not adapters:
        console.print(f"[red]No adapters found for country={country}, city={city}[/red]")
        return stage_common.stage_summary()

    console.print(f"[cyan]Found {len(adapters)} adapter(s)[/cyan]")

    created_total = 0
    updated_total = 0
    skipped_total = 0
    failed_adapters = 0
    for adapter_class in adapters:
        adapter = adapter_class(db=db)
        console.print(f"\n[cyan]Running {adapter.ADAPTER_NAME}...[/cyan]")

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("Discovering schools...", total=None)

            try:
                result = await adapter.run(limit=limit, sample_ratio=sample_ratio)
                progress.update(task, completed=True)

                created_total += int(result["created"])
                updated_total += int(result["updated"])
                skipped_total += int(result["skipped"])
                console.print("[green]✓ Discovery complete:[/green]")
                console.print(f"  Created: {result['created']}")
                console.print(f"  Updated: {result['updated']}")
                console.print(f"  Skipped: {result['skipped']}")

            except Exception as e:
                console.print(f"[red]✗ Error: {str(e)}[/red]")
                logger.exception("Discovery failed")
                # Discovery has no per-school unit that can fail, so count the whole
                # crashed adapter — otherwise a run whose only adapter throws records
                # COMPLETED with all-zero counts.
                failed_adapters += 1

    return stage_common.stage_summary(
        processed=created_total + updated_total + skipped_total + failed_adapters,
        succeeded=created_total + updated_total,
        failed=failed_adapters,
        skipped=skipped_total,
    )


async def run_validate_urls_batch(
    db,
    country: str,
    city: Optional[str],
    limit: Optional[int],
    statuses: Optional[list[str]] = None,
    school_ids: Optional[list[int]] = None,
):
    """Run URL validation stage in batch mode."""
    from sqlalchemy import select

    from app.config import get_settings
    from app.models import School
    from app.scrapers.url_validator import extract_validation_aliases, validate_school_url

    query = select(School.id, School.website_url, School.name_i18n, School.attributes, School.city).where(
        School.country_code == country,
        School.website_url.isnot(None),
    )

    if school_ids is None:
        statuses = statuses or ["pending", "failed_validate"]
        query = query.where(School.scrape_status.in_(statuses))
    else:
        query = query.where(School.id.in_(school_ids))

    if city:
        query = query.where(School.city == city)

    if limit:
        query = query.limit(limit)

    result = await db.execute(query)
    schools = result.all()

    if not schools:
        console.print("[yellow]No schools to validate[/yellow]")
        return {**stage_common.stage_summary(), "validated_school_ids": []}

    settings = get_settings()
    requested_concurrency = max(1, int(settings.url_validation_concurrency))
    max_concurrency = max(1, int(settings.url_validation_max_concurrency))
    concurrency = min(requested_concurrency, max_concurrency)

    console.print(f"[cyan]Validating {len(schools)} school URLs...[/cyan]")
    console.print(f"  Concurrency: {concurrency}")
    if requested_concurrency > max_concurrency:
        console.print(
            f"[yellow]  Requested concurrency {requested_concurrency} capped to {max_concurrency} "
            f"(DB session safety)[/yellow]"
        )

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Validating URLs...", total=len(schools))

        valid_count = 0
        invalid_count = 0
        ambiguous_count = 0
        error_count = 0
        usage = {"input_tokens": 0, "output_tokens": 0, "token_cost_usd": 0.0}
        semaphore = asyncio.Semaphore(concurrency)

        async def _validate_single(
            school_id: int,
            website_url: str,
            school_name: Optional[str],
            school_aliases: list[str],
            school_city: Optional[str],
        ) -> tuple[int, str, dict]:
            async with semaphore:
                school_usage: dict = {}
                try:
                    result, _, _ = await validate_school_url(
                        school_id=school_id,
                        url=website_url,
                        country_code=country,
                        update_db=True,
                        school_name=school_name,
                        school_aliases=school_aliases,
                        usage_out=school_usage,
                        school_city=school_city,
                    )
                    return school_id, result.value, school_usage
                except Exception as exc:
                    logger.error(f"Error validating school {school_id}: {exc}")
                    return school_id, "error", school_usage

        tasks = [
            asyncio.create_task(
                _validate_single(
                    school_id=school_id,
                    website_url=website_url,
                    school_name=(name_i18n or {}).get("bg") or (name_i18n or {}).get("en"),
                    school_aliases=extract_validation_aliases(attributes),
                    school_city=city_code,
                )
            )
            for school_id, website_url, name_i18n, attributes, city_code in schools
            if website_url
        ]

        validated_school_ids: list[int] = []
        for completed in asyncio.as_completed(tasks):
            school_id, outcome, school_usage = await completed
            stage_common.add_llm_usage(usage, school_usage)
            if outcome == "valid":
                valid_count += 1
                validated_school_ids.append(school_id)
            elif outcome == "invalid":
                invalid_count += 1
            elif outcome == "ambiguous":
                ambiguous_count += 1
            else:
                error_count += 1
            progress.update(task, advance=1)

    console.print("[green]✓ URL validation complete:[/green]")
    console.print(f"  Valid: {valid_count}")
    console.print(f"  Invalid: {invalid_count}")
    console.print(f"  Ambiguous: {ambiguous_count}")
    console.print(f"  Errors: {error_count}")

    return {
        **stage_common.stage_summary_with_usage(
            usage=usage,
            processed=len(schools),
            succeeded=valid_count,
            failed=invalid_count + error_count,
            skipped=ambiguous_count,
        ),
        "validated_school_ids": sorted(validated_school_ids),
    }


async def run_recover_failed_school(db, school_id: int, country: str) -> dict:
    """Rediscover + revalidate URL for one failed school, trying multiple candidates."""
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from app.config import get_settings
    from app.models import School
    from app.scrapers.website_discovery import WebsiteDiscoverer

    # Discovery builds its search queries from the school's address.
    result = await db.execute(
        select(School).where(School.id == school_id).options(selectinload(School.locations))
    )
    school = result.scalar_one_or_none()
    if not school:
        raise ValueError(f"School {school_id} not found")

    if school.scrape_status != "failed_validate":
        console.print(
            f"[yellow]School {school_id} is '{school.scrape_status}' (expected failed_validate); skipping[/yellow]"
        )
        return {"school_id": school_id, "skipped": True, "reason": "not_failed_validate"}

    settings = get_settings()
    max_attempts = max(1, int(settings.url_recovery_candidate_attempts))
    discoverer = WebsiteDiscoverer(country_code=country)
    return await discoverer.recover_failed_school(
        db=db,
        school=school,
        max_attempts=max_attempts,
    )


async def recover_failed_school_with_new_session(school_id: int, country: str) -> dict:
    """Run recovery for one school in an isolated DB session."""
    from app.database import async_session_maker

    async with async_session_maker() as db:
        return await run_recover_failed_school(db, school_id, country)


async def run_recover_failed_urls_batch(db, country: str, city: str, limit: Optional[int]):
    """Retry website discovery for failed URLs with multi-candidate validation fallback."""
    from sqlalchemy import select

    from app.config import get_settings
    from app.models import School

    query = select(School.id).where(
        School.country_code == country,
        School.scrape_status == "failed_validate",
    )

    if city:
        query = query.where(School.city == city)

    if limit:
        query = query.limit(limit)

    result = await db.execute(query)
    school_ids = [row[0] for row in result.all()]

    if not school_ids:
        console.print("[yellow]No failed_validate schools to recover[/yellow]")
        return stage_common.stage_summary()

    console.print(f"[cyan]Recovering {len(school_ids)} failed school URLs...[/cyan]")

    settings = get_settings()
    requested_concurrency = max(1, int(settings.url_recovery_concurrency))
    max_concurrency = max(1, int(settings.url_validation_max_concurrency))
    concurrency = min(requested_concurrency, max_concurrency)
    console.print(f"  Concurrency: {concurrency}")
    if requested_concurrency > max_concurrency:
        console.print(
            f"[yellow]  Requested concurrency {requested_concurrency} capped to {max_concurrency} "
            f"(DB session safety)[/yellow]"
        )

    validated_count = 0
    still_failed_count = 0
    terminal_count = 0
    skipped_count = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Recovering failed URLs...", total=len(school_ids))

        semaphore = asyncio.Semaphore(concurrency)

        async def _recover_single(target_school_id: int) -> dict:
            async with semaphore:
                return await recover_failed_school_with_new_session(target_school_id, country)

        tasks = [asyncio.create_task(_recover_single(school_id)) for school_id in school_ids]
        for completed in asyncio.as_completed(tasks):
            try:
                out = await completed
                if out.get("terminal"):
                    terminal_count += 1
                elif out.get("skipped"):
                    skipped_count += 1
                elif out.get("status") == "validated":
                    validated_count += 1
                else:
                    still_failed_count += 1
            except Exception as exc:
                logger.error(f"Failed to recover school URL: {exc}")
            progress.update(task, advance=1)

    console.print("[green]✓ Failed URL recovery pass complete:[/green]")
    console.print(f"  Recovered + validated: {validated_count}")
    console.print(f"  Still failed: {still_failed_count}")
    console.print(f"  No official website: {terminal_count}")
    console.print(f"  Skipped: {skipped_count}")

    # Recovery rediscovers websites too, so it gets the same shared-site check.
    await check_shared_sites(db, country=country, school_ids=school_ids)

    return stage_common.stage_summary(
        processed=len(school_ids),
        succeeded=validated_count,
        failed=still_failed_count,
        skipped=terminal_count + skipped_count,
    )


async def run_discover_website(db, school_id: int, country: str):
    """Run website discovery for a single school."""
    from app.scrapers.website_discovery import discover_school_website

    console.print(f"  Discovering website for school {school_id}...")
    result = await discover_school_website(db=db, school_id=school_id, country_code=country)

    if result.get("found"):
        console.print(
            f"  Result: {result.get('website_url')} "
            f"(method={result.get('method')}, updated={result.get('updated')})"
        )
    else:
        console.print(f"[yellow]  Not found: {result.get('reason', 'No reason')}[/yellow]")
    return result


async def run_discover_websites_batch(db, country: str, city: str, limit: Optional[int]):
    """Run website discovery stage in batch mode."""
    from sqlalchemy import and_, or_, select

    from app.models import School

    query = select(School).where(
        School.country_code == country,
        or_(
            School.scrape_status.in_(["pending", "failed_validate"]),
            and_(School.scrape_status == "extraction_failed", School.website_url.is_(None)),
        ),
    )

    if city:
        query = query.where(School.city == city)

    if limit:
        query = query.limit(limit)

    result = await db.execute(query)
    schools = result.scalars().all()

    if not schools:
        console.print("[yellow]No schools to discover websites for[/yellow]")
        return stage_common.stage_summary()

    console.print(f"[cyan]Discovering websites for {len(schools)} schools...[/cyan]")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Discovering websites...", total=len(schools))

        found_count = 0
        updated_count = 0
        error_count = 0

        for school in schools:
            try:
                result = await run_discover_website(db, school.id, country)
                if result and result.get("found"):
                    found_count += 1
                if result and result.get("updated"):
                    updated_count += 1
            except Exception as e:
                error_count += 1
                logger.error(f"Error discovering website for school {school.id}: {e}")
            progress.update(task, advance=1)

    console.print("[green]✓ Website discovery complete:[/green]")
    console.print(f"  Found: {found_count}")
    console.print(f"  Updated: {updated_count}")
    console.print(f"  Unchanged: {len(schools) - updated_count}")

    await check_shared_sites(db, country=country, school_ids=[school.id for school in schools])

    return stage_common.stage_summary(
        processed=len(schools),
        succeeded=found_count,
        failed=error_count,
        skipped=len(schools) - found_count - error_count,
    )


async def run_validate_url(db, school_id: int, country: str):
    """Run URL validation for a single school."""
    from sqlalchemy import select

    from app.models import School
    from app.scrapers.url_validator import extract_validation_aliases, validate_school_url

    result = await db.execute(select(School).where(School.id == school_id))
    school = result.scalar_one_or_none()

    if not school:
        raise ValueError(f"School {school_id} not found")

    if not school.website_url:
        console.print(f"[yellow]School {school_id} has no website URL[/yellow]")
        return

    console.print(f"  Validating: {school.website_url}")

    validation_result, final_url, reason = await validate_school_url(
        school_id=school_id,
        url=school.website_url,
        country_code=country,
        update_db=True,
        school_name=(school.name_i18n or {}).get("bg") or (school.name_i18n or {}).get("en"),
        school_aliases=extract_validation_aliases(school.attributes),
        school_city=school.city,
    )

    console.print(f"  Result: {validation_result.value} - {reason}")
