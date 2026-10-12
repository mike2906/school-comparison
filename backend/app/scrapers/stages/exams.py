"""Official exam result imports (NVO and ДЗИ); independent of the website pipeline."""

from __future__ import annotations

import logging
from typing import Optional

from rich.console import Console

logger = logging.getLogger(__name__)
console = Console()


async def run_nvo_import(
    db,
    *,
    country: str,
    city: Optional[str],
    year: Optional[int],
    history_years: int,
    exam_types: Optional[list[str]],
    school_ids: Optional[list[int]],
):
    """Run official NVO import."""
    from app.scrapers.nvo_results import import_nvo_results

    console.print("[cyan]Importing official NVO results...[/cyan]")
    console.print(f"  Country: {country}")
    console.print(f"  City: {city or 'all'}")
    console.print(f"  Year: {year or 'latest available'}")
    console.print(f"  History years: {history_years}")
    console.print(f"  Exam types: {exam_types or 'all supported'}")
    if school_ids:
        console.print(f"  School IDs: {school_ids}")

    summary = await import_nvo_results(
        db=db,
        country_code=country,
        city=city,
        year=year,
        history_years=history_years,
        exam_types=exam_types or None,
        school_ids=school_ids,
    )

    console.print("[green]✓ NVO import complete:[/green]")
    console.print(f"  Years imported: {summary.get('years_imported') or []}")
    console.print(f"  Matched schools: {summary.get('matched_schools', 0)}")
    console.print(f"  Created rows: {summary.get('created_rows', 0)}")
    console.print(f"  Updated rows: {summary.get('updated_rows', 0)}")
    console.print(f"  Skipped rows: {summary.get('skipped_rows', 0)}")
    console.print(f"  Unmatched rows: {summary.get('unmatched_rows', 0)}")
    console.print(f"  Source URL: {summary.get('source_url')}")
    if summary.get("slice_failures"):
        console.print(f"[yellow]  Slice failures: {len(summary['slice_failures'])}[/yellow]")
    return summary


async def run_dzi_import(
    db,
    *,
    country: str,
    city: Optional[str],
    year: Optional[int],
    history_years: int,
    school_ids: Optional[list[int]],
):
    """Run official ДЗИ (matura) import: mandatory May–June session, per school and subject."""
    from app.scrapers.dzi_results import import_dzi_results

    console.print("[cyan]Importing official ДЗИ results...[/cyan]")
    console.print(f"  Country: {country}")
    console.print(f"  City: {city or 'all'}")
    console.print(f"  Year: {year or 'latest available'}")
    console.print(f"  History years: {history_years}")
    if school_ids:
        console.print(f"  School IDs: {school_ids}")

    summary = await import_dzi_results(
        db=db,
        country_code=country,
        city=city,
        year=year,
        history_years=history_years,
        school_ids=school_ids,
    )

    console.print("[green]✓ ДЗИ import complete:[/green]")
    console.print(f"  Years imported: {summary.get('years_imported') or []}")
    console.print(f"  Matched schools: {summary.get('matched_schools', 0)}")
    console.print(f"  Created rows: {summary.get('created_rows', 0)}")
    console.print(f"  Updated rows: {summary.get('updated_rows', 0)}")
    console.print(f"  Skipped rows: {summary.get('skipped_rows', 0)}")
    console.print(f"  Unmatched rows: {summary.get('unmatched_rows', 0)}")
    console.print(f"  Grades without a pupil count (withheld): {summary.get('missing_counts', 0)}")
    if summary.get("subject_labels"):
        console.print("  Subjects read (file header → stored as):")
        for label, key in sorted(summary["subject_labels"].items(), key=lambda item: item[1]):
            console.print(f"    {label} → {key}")
    if summary.get("unknown_subjects"):
        console.print(f"[yellow]  Subjects not imported (unknown abbreviation): {', '.join(summary['unknown_subjects'])}[/yellow]")
    console.print(f"  Source URL: {summary.get('source_url')}")
    if summary.get("slice_failures"):
        console.print(f"[yellow]  Slice failures: {len(summary['slice_failures'])}[/yellow]")
        for failure in summary["slice_failures"]:
            console.print(f"[yellow]    {failure['year']}: {failure['error']}[/yellow]")
    return summary
