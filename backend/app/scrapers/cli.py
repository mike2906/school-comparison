"""
CLI tool for running scraping pipeline stages.

This provides a convenient interface for testing and debugging the scraping
pipeline without needing Celery workers.

Usage:
    # Run by school name (fuzzy match)
    uv run python -m app.scrapers.cli run --school "British School" --stage discover

    # Run by school ID
    uv run python -m app.scrapers.cli run --school-id 42 --stage validate-urls

    # Discover/normalize website URLs
    uv run python -m app.scrapers.cli run --stage discover-websites --city sofia --limit 20

    # Retry failed URL discovery+validation (failed_validate only)
    uv run python -m app.scrapers.cli run --stage recover-failed-urls --city sofia --limit 20

    # Run batch with limit
    uv run python -m app.scrapers.cli run --stage discover --city sofia --limit 10

    # Synchronous mode (no Celery)
    uv run python -m app.scrapers.cli run --stage all --sync --limit 5

    # Dry run (show what would happen)
    uv run python -m app.scrapers.cli run --stage discover --dry-run

    # Reset school status
    uv run python -m app.scrapers.cli reset --school "134 СУ" --to pending

    # List schools
    uv run python -m app.scrapers.cli list --city sofia --limit 10

    # Show stats
    uv run python -m app.scrapers.cli stats --city sofia
"""
import asyncio
import sys
import logging
from typing import Optional
import click
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
# Keep CLI output focused on scraper progress; DEBUG=true still enables app-level behavior.
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
logging.getLogger("sqlalchemy.pool").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

console = Console()


@click.group()
def cli():
    """Sofia School Comparison - Scraping Pipeline CLI"""
    pass


@cli.command()
@click.option("--school", help="School name (fuzzy match)")
@click.option("--school-id", type=int, help="School ID")
@click.option(
    "--stage",
    type=click.Choice(
        [
            "discover",
            "discover-websites",
            "recover-failed-urls",
            "validate-urls",
            "navigate",
            "extract",
            "validate-data",
            "summarize",
            "all",
        ],
        case_sensitive=False,
    ),
    required=True,
    help="Pipeline stage to run",
)
@click.option("--city", default="sofia", help="City to filter by")
@click.option("--country", default="bg", help="Country code")
@click.option("--limit", type=int, help="Limit number of schools to process")
@click.option("--sample-ratio", type=float, default=0.0, help="Sample ratio for unchanged schools (discover only)")
@click.option("--include-navigated", is_flag=True, help="For navigate stage, recrawl already navigated schools")
@click.option("--include-extracted", is_flag=True, help="For extract stage, re-extract already extracted schools")
@click.option("--sync", is_flag=True, help="Run synchronously (no Celery)")
@click.option("--dry-run", is_flag=True, help="Show what would happen without executing")
def run(school, school_id, stage, city, country, limit, sample_ratio, include_navigated, include_extracted, sync, dry_run):
    """Run a pipeline stage."""
    if dry_run:
        console.print(f"[yellow]DRY RUN - would execute:[/yellow]")
        console.print(f"  Stage: {stage}")
        console.print(f"  School: {school or school_id or 'batch'}")
        console.print(f"  City: {city}")
        console.print(f"  Country: {country}")
        console.print(f"  Limit: {limit}")
        console.print(f"  Sample ratio: {sample_ratio}")
        console.print(f"  Include navigated: {include_navigated}")
        console.print(f"  Include extracted: {include_extracted}")
        console.print(f"  Mode: {'sync' if sync else 'celery'}")
        return

    if sync:
        # Run synchronously
        asyncio.run(_run_sync(school, school_id, stage, city, country, limit, sample_ratio, include_navigated, include_extracted))
    else:
        # Run via Celery
        console.print("[yellow]Celery mode not yet implemented. Use --sync for now.[/yellow]")
        sys.exit(1)


async def _run_sync(school_name, school_id, stage, city, country, limit, sample_ratio, include_navigated: bool, include_extracted: bool):
    """Run pipeline stage synchronously."""
    from app.database import async_session_maker

    async with async_session_maker() as db:
        if school_name or school_id:
            # Single school mode
            if school_name:
                school_id = await _find_school_by_name(db, school_name, country)
                if not school_id:
                    console.print(f"[red]School not found: {school_name}[/red]")
                    return

            console.print(f"[cyan]Running stage '{stage}' for school ID {school_id}[/cyan]")

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
                        await _run_discover_website(db, school_id, country)
                    elif stage == "recover-failed-urls":
                        await _run_recover_failed_school(db, school_id, country)
                    elif stage == "validate-urls":
                        await _run_validate_url(db, school_id, country)
                    elif stage == "navigate":
                        await _run_navigate_school(db, school_id, country)
                    elif stage == "extract":
                        await _run_extract_school(db, school_id, country)
                    elif stage == "validate-data":
                        console.print("[yellow]Validate-data stage not yet implemented[/yellow]")
                    elif stage == "summarize":
                        console.print("[yellow]Summarize stage not yet implemented[/yellow]")
                    elif stage == "all":
                        await _run_all_stages(db, school_id, country)

                    progress.update(task, completed=True)
                    console.print(f"[green]✓ Completed stage '{stage}' for school {school_id}[/green]")

                except Exception as e:
                    console.print(f"[red]✗ Error: {str(e)}[/red]")
                    logger.exception("Stage execution failed")

        else:
            # Batch mode
            console.print(f"[cyan]Running stage '{stage}' in batch mode[/cyan]")
            console.print(f"  City: {city}")
            console.print(f"  Limit: {limit or 'all'}")

            if stage == "discover":
                await _run_discover_batch(db, country, city, limit, sample_ratio)
            elif stage == "discover-websites":
                await _run_discover_websites_batch(db, country, city, limit)
            elif stage == "recover-failed-urls":
                await _run_recover_failed_urls_batch(db, country, city, limit)
            elif stage == "validate-urls":
                await _run_validate_urls_batch(db, country, city, limit)
            elif stage == "navigate":
                await _run_navigate_batch(db, country, city, limit, include_navigated=include_navigated)
            elif stage == "extract":
                await _run_extract_batch(db, country, city, limit, include_extracted=include_extracted)
            else:
                console.print(f"[yellow]Batch mode for '{stage}' not yet implemented[/yellow]")


async def _find_school_by_name(db, name: str, country: str) -> Optional[int]:
    """Find school by fuzzy name match."""
    from app.models import School
    from sqlalchemy import select, cast, String, or_, func

    # Try exact match first
    result = await db.execute(
        select(School.id, School.name_i18n).where(
            School.country_code == country,
            or_(
                cast(School.name_i18n["bg"], String).ilike(f"%{name}%"),
                cast(School.name_i18n["en"], String).ilike(f"%{name}%"),
            ),
        )
    )

    schools = result.all()

    if not schools:
        return None

    if len(schools) == 1:
        school_id, name_i18n = schools[0]
        console.print(f"[green]Found school: {name_i18n.get('bg', name_i18n.get('en'))}[/green]")
        return school_id

    # Multiple matches - show options
    console.print(f"[yellow]Found {len(schools)} schools matching '{name}':[/yellow]")
    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("ID", style="dim")
    table.add_column("Name")

    for school_id, name_i18n in schools[:10]:  # Show max 10
        table.add_row(str(school_id), name_i18n.get("bg", name_i18n.get("en", "N/A")))

    console.print(table)
    console.print("[yellow]Use --school-id to specify which school[/yellow]")
    return None


async def _run_discover_batch(db, country: str, city: str, limit: Optional[int], sample_ratio: float):
    """Run discovery stage in batch mode."""
    from app.scrapers.sources import get_adapters_for_country

    adapters = get_adapters_for_country(country, city)

    if not adapters:
        console.print(f"[red]No adapters found for country={country}, city={city}[/red]")
        return

    console.print(f"[cyan]Found {len(adapters)} adapter(s)[/cyan]")

    for adapter_class in adapters:
        adapter = adapter_class(db=db)
        console.print(f"\n[cyan]Running {adapter.ADAPTER_NAME}...[/cyan]")

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task(f"Discovering schools...", total=None)

            try:
                result = await adapter.run(limit=limit, sample_ratio=sample_ratio)
                progress.update(task, completed=True)

                console.print(f"[green]✓ Discovery complete:[/green]")
                console.print(f"  Created: {result['created']}")
                console.print(f"  Updated: {result['updated']}")
                console.print(f"  Skipped: {result['skipped']}")

            except Exception as e:
                console.print(f"[red]✗ Error: {str(e)}[/red]")
                logger.exception("Discovery failed")


async def _run_validate_urls_batch(
    db,
    country: str,
    city: Optional[str],
    limit: Optional[int],
    statuses: Optional[list[str]] = None,
    school_ids: Optional[list[int]] = None,
):
    """Run URL validation stage in batch mode."""
    from app.config import get_settings
    from app.models import School
    from sqlalchemy import select
    from app.scrapers.url_validator import validate_school_url

    statuses = statuses or ["pending", "failed_validate"]

    query = select(School.id, School.website_url, School.name_i18n).where(
        School.country_code == country,
        School.scrape_status.in_(statuses),
        School.website_url.isnot(None),
    )

    if city:
        query = query.where(School.city == city)

    if school_ids:
        query = query.where(School.id.in_(school_ids))

    if limit:
        query = query.limit(limit)

    result = await db.execute(query)
    schools = result.all()

    if not schools:
        console.print("[yellow]No schools to validate[/yellow]")
        return

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
        semaphore = asyncio.Semaphore(concurrency)

        async def _validate_single(school_id: int, website_url: str, school_name: Optional[str]) -> str:
            async with semaphore:
                try:
                    result, _, _ = await validate_school_url(
                        school_id=school_id,
                        url=website_url,
                        country_code=country,
                        update_db=True,
                        school_name=school_name,
                    )
                    return result.value
                except Exception as exc:
                    logger.error(f"Error validating school {school_id}: {exc}")
                    return "error"

        tasks = [
            asyncio.create_task(
                _validate_single(
                    school_id=school_id,
                    website_url=website_url,
                    school_name=(name_i18n or {}).get("bg") or (name_i18n or {}).get("en"),
                )
            )
            for school_id, website_url, name_i18n in schools
            if website_url
        ]

        for completed in asyncio.as_completed(tasks):
            outcome = await completed
            if outcome == "valid":
                valid_count += 1
            elif outcome == "invalid":
                invalid_count += 1
            elif outcome == "ambiguous":
                ambiguous_count += 1
            else:
                error_count += 1
            progress.update(task, advance=1)

    console.print(f"[green]✓ URL validation complete:[/green]")
    console.print(f"  Valid: {valid_count}")
    console.print(f"  Invalid: {invalid_count}")
    console.print(f"  Ambiguous: {ambiguous_count}")
    console.print(f"  Errors: {error_count}")


async def _run_recover_failed_school(db, school_id: int, country: str) -> dict:
    """Rediscover + revalidate URL for one failed school, trying multiple candidates."""
    from app.config import get_settings
    from app.models import School
    from sqlalchemy import select
    from app.scrapers.website_discovery import WebsiteDiscoverer

    result = await db.execute(select(School).where(School.id == school_id))
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


async def _recover_failed_school_with_new_session(school_id: int, country: str) -> dict:
    """Run recovery for one school in an isolated DB session."""
    from app.database import async_session_maker

    async with async_session_maker() as db:
        return await _run_recover_failed_school(db, school_id, country)


async def _run_recover_failed_urls_batch(db, country: str, city: str, limit: Optional[int]):
    """Retry website discovery for failed URLs with multi-candidate validation fallback."""
    from app.config import get_settings
    from app.models import School
    from sqlalchemy import select

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
        return

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
                return await _recover_failed_school_with_new_session(target_school_id, country)

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


async def _run_discover_website(db, school_id: int, country: str):
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


async def _run_discover_websites_batch(db, country: str, city: str, limit: Optional[int]):
    """Run website discovery stage in batch mode."""
    from app.models import School
    from sqlalchemy import select

    query = select(School).where(
        School.country_code == country,
        School.scrape_status.in_(["pending", "failed_validate"]),
    )

    if city:
        query = query.where(School.city == city)

    if limit:
        query = query.limit(limit)

    result = await db.execute(query)
    schools = result.scalars().all()

    if not schools:
        console.print("[yellow]No schools to discover websites for[/yellow]")
        return

    console.print(f"[cyan]Discovering websites for {len(schools)} schools...[/cyan]")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Discovering websites...", total=len(schools))

        found_count = 0
        updated_count = 0

        for school in schools:
            try:
                result = await _run_discover_website(db, school.id, country)
                if result and result.get("found"):
                    found_count += 1
                if result and result.get("updated"):
                    updated_count += 1
            except Exception as e:
                logger.error(f"Error discovering website for school {school.id}: {e}")
            progress.update(task, advance=1)

    console.print(f"[green]✓ Website discovery complete:[/green]")
    console.print(f"  Found: {found_count}")
    console.print(f"  Updated: {updated_count}")
    console.print(f"  Unchanged: {len(schools) - updated_count}")


async def _run_validate_url(db, school_id: int, country: str):
    """Run URL validation for a single school."""
    from app.models import School
    from sqlalchemy import select
    from app.scrapers.url_validator import validate_school_url

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
    )

    console.print(f"  Result: {validation_result.value} - {reason}")


async def _run_navigate_school(db, school_id: int, country: str):
    """Run navigation stage for a single school."""
    from app.scrapers.navigator import navigate_school

    console.print(f"  Navigating website for school {school_id}...")
    result = await navigate_school(db=db, school_id=school_id, country_code=country)

    if result.get("success"):
        console.print(
            f"  Result: pages={result.get('pages_found', 0)}, "
            f"created={result.get('created', 0)}, updated={result.get('updated', 0)}"
        )
    else:
        console.print(f"[yellow]  Skipped: {result.get('reason', 'Unknown reason')}[/yellow]")
    return result


async def _run_navigate_batch(
    db,
    country: str,
    city: str,
    limit: Optional[int],
    include_navigated: bool = False,
):
    """Run navigation stage in batch mode."""
    from app.models import School
    from sqlalchemy import select

    statuses = ["validated", "navigated"] if include_navigated else ["validated"]
    query = select(School).where(
        School.country_code == country,
        School.scrape_status.in_(statuses),
        School.website_url.isnot(None),
    )

    if city:
        query = query.where(School.city == city)

    if limit:
        query = query.limit(limit)

    result = await db.execute(query)
    schools = result.scalars().all()
    school_ids = [school.id for school in schools]

    if not school_ids:
        console.print("[yellow]No schools to navigate[/yellow]")
        return

    status_label = "validated+navigated" if include_navigated else "validated"
    console.print(f"[cyan]Navigating websites for {len(school_ids)} schools...[/cyan]")
    console.print(f"  Status filter: {status_label}")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Navigating websites...", total=len(school_ids))

        success_count = 0
        fail_count = 0

        from app.config import get_settings as _get_settings

        _nav_timeout = _get_settings().nav_school_timeout_seconds

        for school_id in school_ids:
            try:
                coro = _run_navigate_school(db, school_id, country)
                if _nav_timeout > 0:
                    result = await asyncio.wait_for(coro, timeout=_nav_timeout)
                else:
                    result = await coro
                if result and result.get("success"):
                    success_count += 1
                else:
                    fail_count += 1
            except asyncio.TimeoutError:
                logger.error(
                    "Navigation timed out after %.0fs for school %s", _nav_timeout, school_id
                )
                await db.rollback()
                fail_count += 1
            except Exception as e:
                logger.error(f"Error navigating school {school_id}: {e}")
                await db.rollback()
                fail_count += 1
            progress.update(task, advance=1)

    console.print(f"[green]✓ Navigation complete:[/green]")
    console.print(f"  Successful: {success_count}")
    console.print(f"  Failed: {fail_count}")


async def _run_all_stages(db, school_id: int, country: str):
    """Run all pipeline stages for a single school."""
    stages = ["discover-websites", "validate-urls", "navigate", "extract", "validate-data", "summarize"]

    for stage in stages:
        console.print(f"\n[cyan]Stage: {stage}[/cyan]")

        if stage == "discover-websites":
            await _run_discover_website(db, school_id, country)
        elif stage == "validate-urls":
            await _run_validate_url(db, school_id, country)
        elif stage == "navigate":
            await _run_navigate_school(db, school_id, country)
        elif stage == "extract":
            await _run_extract_school(db, school_id, country)
        else:
            console.print(f"[yellow]{stage} not yet implemented[/yellow]")


@cli.command()
@click.option("--school", help="School name (fuzzy match)")
@click.option("--school-id", type=int, help="School ID")
@click.option("--status", help="Reset all schools with this status")
@click.option(
    "--to",
    "to_status",
    type=click.Choice(
        [
            "pending",
            "failed_validate",
            "validated",
            "navigated",
            "extraction_failed",
            "extracted",
            "summarized",
            "no_official_website",
        ],
        case_sensitive=False,
    ),
    required=True,
    help="Target status",
)
@click.option("--country", default="bg", help="Country code")
def reset(school, school_id, status, to_status, country):
    """Reset school scrape status."""
    asyncio.run(_reset_status(school, school_id, status, to_status, country))


async def _reset_status(school_name, school_id, from_status, to_status, country):
    """Reset school scrape status."""
    from app.database import async_session_maker
    from app.models import School
    from sqlalchemy import select, update

    async with async_session_maker() as db:
        if school_name or school_id:
            # Single school
            if school_name:
                school_id = await _find_school_by_name(db, school_name, country)
                if not school_id:
                    return

            await db.execute(
                update(School).where(School.id == school_id).values(scrape_status=to_status)
            )
            await db.commit()
            console.print(f"[green]✓ Reset school {school_id} to '{to_status}'[/green]")

        elif from_status:
            # Batch reset
            result = await db.execute(
                update(School)
                .where(School.country_code == country, School.scrape_status == from_status)
                .values(scrape_status=to_status)
            )
            await db.commit()
            console.print(f"[green]✓ Reset {result.rowcount} schools from '{from_status}' to '{to_status}'[/green]")

        else:
            console.print("[red]Must specify --school, --school-id, or --status[/red]")


@cli.command()
@click.option("--city", help="Filter by city")
@click.option("--country", default="bg", help="Country code")
@click.option("--status", help="Filter by scrape status")
@click.option("--limit", type=int, default=20, help="Number of schools to show")
def list(city, country, status, limit):
    """List schools."""
    asyncio.run(_list_schools(city, country, status, limit))


async def _list_schools(city, country, status, limit):
    """List schools with filters."""
    from app.database import async_session_maker
    from app.models import School
    from sqlalchemy import select

    async with async_session_maker() as db:
        query = select(School).where(School.country_code == country)

        if city:
            query = query.where(School.city == city)

        if status:
            query = query.where(School.scrape_status == status)

        query = query.limit(limit)

        result = await db.execute(query)
        schools = result.scalars().all()

        if not schools:
            console.print("[yellow]No schools found[/yellow]")
            return

        table = Table(show_header=True, header_style="bold cyan")
        table.add_column("ID", style="dim", width=6)
        table.add_column("Name", width=40)
        table.add_column("Type", width=12)
        table.add_column("Status", width=15)
        table.add_column("City", width=10)

        for school in schools:
            name = school.name_i18n.get("bg", school.name_i18n.get("en", "N/A"))
            table.add_row(
                str(school.id),
                name[:38] + "..." if len(name) > 40 else name,
                school.school_type,
                school.scrape_status,
                school.city or "N/A",
            )

        console.print(table)
        console.print(f"\n[dim]Showing {len(schools)} of {len(schools)} schools[/dim]")


@cli.command()
@click.option("--city", help="Filter by city")
@click.option("--country", default="bg", help="Country code")
def stats(city, country):
    """Show pipeline statistics."""
    asyncio.run(_show_stats(city, country))


async def _show_stats(city, country):
    """Show pipeline statistics."""
    from app.database import async_session_maker
    from app.models import School
    from sqlalchemy import select, func

    async with async_session_maker() as db:
        # Total schools
        query = select(func.count(School.id)).where(School.country_code == country)
        if city:
            query = query.where(School.city == city)

        result = await db.execute(query)
        total = result.scalar()

        # By status
        query = (
            select(School.scrape_status, func.count(School.id))
            .where(School.country_code == country)
            .group_by(School.scrape_status)
        )
        if city:
            query = query.where(School.city == city)

        result = await db.execute(query)
        status_counts = dict(result.all())

        # By type
        query = (
            select(School.school_type, func.count(School.id))
            .where(School.country_code == country)
            .group_by(School.school_type)
        )
        if city:
            query = query.where(School.city == city)

        result = await db.execute(query)
        type_counts = dict(result.all())

        # Display
        console.print(f"\n[bold cyan]Pipeline Statistics[/bold cyan]")
        console.print(f"Country: {country}")
        if city:
            console.print(f"City: {city}")
        console.print(f"\n[bold]Total schools: {total}[/bold]\n")

        # Status breakdown
        console.print("[bold]By Status:[/bold]")
        for status, count in sorted(status_counts.items()):
            console.print(f"  {status}: {count}")

        # Type breakdown
        console.print(f"\n[bold]By Type:[/bold]")
        for school_type, count in sorted(type_counts.items()):
            console.print(f"  {school_type}: {count}")



async def _run_extract_school(db, school_id: int, country: str):
    """Run extraction stage for a single school."""
    from app.scrapers.extractor import extract_school

    console.print(f"  Extracting data for school {school_id}...")
    result = await extract_school(db=db, school_id=school_id, country_code=country)
    llm_stats = result.get("llm_stats") or {}

    if result.get("skipped"):
        console.print(f"[yellow]  Skipped: content unchanged[/yellow]")
    elif result.get("status") == "extracted":
        console.print(f"[green]  Success: pricing={result.get('pricing_count')} items, general_info={result.get('general_info_success')}[/green]")
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
            f"typed_fail={llm_stats.get('typed_validation_failures', 0)} "
            f"({llm_stats.get('typed_validation_failure_rate', 0):.2%}), "
            f"recovered={llm_stats.get('relaxed_parse_successes', 0)}/{llm_stats.get('relaxed_parse_attempts', 0)}, "
            f"hard_fail={llm_stats.get('hard_failures', 0)} "
            f"({llm_stats.get('hard_failure_rate', 0):.2%})"
            "[/dim]"
        )
    return result


async def _run_extract_batch(
    db,
    country: str,
    city: str,
    limit: Optional[int],
    include_extracted: bool = False,
):
    """Run extraction stage in batch mode."""
    from app.models import School
    from sqlalchemy import select

    statuses = ["navigated", "extraction_failed"]
    if include_extracted:
        statuses.append("extracted")

    query = select(School).where(
        School.country_code == country,
        School.scrape_status.in_(statuses),
        School.website_url.isnot(None),
    )

    if city:
        query = query.where(School.city == city)

    if limit:
        query = query.limit(limit)

    result = await db.execute(query)
    schools = result.scalars().all()
    school_ids = [school.id for school in schools]

    if not school_ids:
        console.print("[yellow]No schools to extract (must be navigated or extraction_failed)[/yellow]")
        return

    status_label = f"navigated/failed + extracted" if include_extracted else "navigated/failed"
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

        from app.config import get_settings as _get_settings
        from app.database import async_session_maker

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
                    coro = _run_extract_school(db, school_id, country)
                    if _ext_timeout > 0:
                        result = await asyncio.wait_for(coro, timeout=_ext_timeout)
                    else:
                        result = await coro
                    if result.get("skipped"):
                        skipped_count += 1
                    elif result.get("status") == "extracted":
                        success_count += 1
                    else:
                        fail_count += 1
                except asyncio.TimeoutError:
                    logger.error(
                        "Extraction timed out after %.0fs for school %s", _ext_timeout, school_id
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

            for completed in asyncio.as_completed(tasks):
                school_id, result = await completed
                if result.get("skipped"):
                    skipped_count += 1
                elif result.get("status") == "extracted":
                    success_count += 1
                else:
                    fail_count += 1
                    console.print(
                        f"[red]  School {school_id} failed: {result.get('error', 'Unknown error')}[/red]"
                    )
                progress.update(task, advance=1)

    console.print(f"[green]✓ Extraction complete:[/green]")
    console.print(f"  Successful: {success_count}")
    console.print(f"  Skipped: {skipped_count}")
    console.print(f"  Failed: {fail_count}")


if __name__ == "__main__":
    cli()
