"""
CLI tool for running scraping pipeline stages.

This provides a convenient interface for testing and debugging the scraping
pipeline without needing Celery workers.

Usage:
    # Run by school name (fuzzy match)
    uv run python -m app.scrapers.cli run --school "British School" --stage discover

    # Run by school ID
    uv run python -m app.scrapers.cli run --school-id 42 --stage validate-urls

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
        ["discover", "validate-urls", "navigate", "extract", "validate-data", "summarize", "all"],
        case_sensitive=False,
    ),
    required=True,
    help="Pipeline stage to run",
)
@click.option("--city", default="sofia", help="City to filter by")
@click.option("--country", default="bg", help="Country code")
@click.option("--limit", type=int, help="Limit number of schools to process")
@click.option("--sample-ratio", type=float, default=0.0, help="Sample ratio for unchanged schools (discover only)")
@click.option("--sync", is_flag=True, help="Run synchronously (no Celery)")
@click.option("--dry-run", is_flag=True, help="Show what would happen without executing")
def run(school, school_id, stage, city, country, limit, sample_ratio, sync, dry_run):
    """Run a pipeline stage."""
    if dry_run:
        console.print(f"[yellow]DRY RUN - would execute:[/yellow]")
        console.print(f"  Stage: {stage}")
        console.print(f"  School: {school or school_id or 'batch'}")
        console.print(f"  City: {city}")
        console.print(f"  Country: {country}")
        console.print(f"  Limit: {limit}")
        console.print(f"  Sample ratio: {sample_ratio}")
        console.print(f"  Mode: {'sync' if sync else 'celery'}")
        return

    if sync:
        # Run synchronously
        asyncio.run(_run_sync(school, school_id, stage, city, country, limit, sample_ratio))
    else:
        # Run via Celery
        console.print("[yellow]Celery mode not yet implemented. Use --sync for now.[/yellow]")
        sys.exit(1)


async def _run_sync(school_name, school_id, stage, city, country, limit, sample_ratio):
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
                    elif stage == "validate-urls":
                        await _run_validate_url(db, school_id, country)
                    elif stage == "navigate":
                        console.print("[yellow]Navigate stage not yet implemented[/yellow]")
                    elif stage == "extract":
                        console.print("[yellow]Extract stage not yet implemented[/yellow]")
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
            elif stage == "validate-urls":
                await _run_validate_urls_batch(db, country, city, limit)
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


async def _run_validate_urls_batch(db, country: str, city: str, limit: Optional[int]):
    """Run URL validation stage in batch mode."""
    from app.models import School
    from sqlalchemy import select

    # Get schools with pending or failed_validate status
    query = select(School).where(
        School.country_code == country,
        School.scrape_status.in_(["pending", "failed_validate"]),
        School.website_url.isnot(None),
    )

    if city:
        query = query.where(School.city == city)

    if limit:
        query = query.limit(limit)

    result = await db.execute(query)
    schools = result.scalars().all()

    if not schools:
        console.print("[yellow]No schools to validate[/yellow]")
        return

    console.print(f"[cyan]Validating {len(schools)} school URLs...[/cyan]")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Validating URLs...", total=len(schools))

        valid_count = 0
        invalid_count = 0

        for school in schools:
            try:
                await _run_validate_url(db, school.id, country)
                # Refresh school to get updated status
                await db.refresh(school)

                if school.scrape_status == "validated":
                    valid_count += 1
                else:
                    invalid_count += 1

            except Exception as e:
                logger.error(f"Error validating school {school.id}: {e}")
                invalid_count += 1

            progress.update(task, advance=1)

    console.print(f"[green]✓ URL validation complete:[/green]")
    console.print(f"  Valid: {valid_count}")
    console.print(f"  Invalid: {invalid_count}")


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
    )

    console.print(f"  Result: {validation_result.value} - {reason}")


async def _run_all_stages(db, school_id: int, country: str):
    """Run all pipeline stages for a single school."""
    stages = ["validate-urls", "navigate", "extract", "validate-data", "summarize"]

    for stage in stages:
        console.print(f"\n[cyan]Stage: {stage}[/cyan]")

        if stage == "validate-urls":
            await _run_validate_url(db, school_id, country)
        else:
            console.print(f"[yellow]{stage} not yet implemented[/yellow]")


@cli.command()
@click.option("--school", help="School name (fuzzy match)")
@click.option("--school-id", type=int, help="School ID")
@click.option("--status", help="Reset all schools with this status")
@click.option(
    "--to",
    "to_status",
    type=click.Choice(["pending", "validated", "navigated", "extracted", "summarized"], case_sensitive=False),
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


if __name__ == "__main__":
    cli()
