"""Audit command: list schools whose display names look like junk."""

from __future__ import annotations

import builtins
import logging
import re
from collections import defaultdict
from typing import Optional

from rich.console import Console
from rich.table import Table

from app.scrapers.stages import common as stage_common

logger = logging.getLogger(__name__)
console = Console()


def _contains_cyrillic(text: str | None) -> bool:
    return bool(text and re.search(r"[А-Яа-я]", text))


async def audit_display_names_command(
    *,
    school_name: Optional[str],
    school_id: Optional[int],
    city: Optional[str],
    country: str,
    limit: Optional[int],
    top: int,
):
    from sqlalchemy import select

    from app.database import async_session_maker
    from app.models import School, SourcePage
    from app.scrapers.display_name_audit import audit_school_display_name

    async with async_session_maker() as db:
        if school_name and not school_id:
            school_id = await stage_common.find_school_by_name(db, school_name, country)
            if not school_id:
                console.print(f"[red]School not found: {school_name}[/red]")
                return

        school_query = select(School).where(
            School.country_code == country,
            School.website_url.isnot(None),
        )
        if city:
            school_query = school_query.where(School.city == city)
        if school_id:
            school_query = school_query.where(School.id == school_id)
        if limit:
            school_query = school_query.limit(limit)

        schools = (await db.execute(school_query)).scalars().all()
        if not schools:
            console.print("[yellow]No schools matched the audit filters.[/yellow]")
            return

        school_ids = [school.id for school in schools]
        page_query = select(SourcePage).where(
            SourcePage.school_id.in_(school_ids),
            SourcePage.raw_markdown.isnot(None),
        )
        pages = (await db.execute(page_query)).scalars().all()
        pages_by_school: dict[int, list[dict[str, object]]] = defaultdict(builtins.list)
        for page in pages:
            pages_by_school[int(page.school_id)].append(
                {
                    "source_url": page.source_url,
                    "page_category": page.page_category,
                    "raw_markdown": page.raw_markdown,
                }
            )

        findings = []
        for school in schools:
            finding = audit_school_display_name(
                {
                    "id": school.id,
                    "name_i18n": school.name_i18n or {},
                    "attributes": school.attributes or {},
                    "website_url": school.website_url,
                },
                pages_by_school.get(school.id, []),
            )
            if finding is not None:
                findings.append(finding)

        findings.sort(key=lambda item: (-item.score, -item.repeated_pages, item.school_id))

        console.print(f"[cyan]Audited {len(schools)} schools with website content[/cyan]")
        console.print(f"  Findings: {len(findings)}")
        if not findings:
            console.print("[green]No likely display-name mismatches found with the current heuristics.[/green]")
            return

        table = Table(title="Likely Display Name Mismatches")
        table.add_column("ID", style="cyan", no_wrap=True)
        table.add_column("School")
        table.add_column("Stored")
        table.add_column("Candidate")
        table.add_column("Score", justify="right")
        table.add_column("Pages", justify="right")
        for finding in findings[: max(1, top)]:
            table.add_row(
                str(finding.school_id),
                finding.legal_name or f"School {finding.school_id}",
                finding.stored_display_name or "-",
                finding.candidate_name,
                str(finding.score),
                str(finding.repeated_pages),
            )
        console.print(table)

        for finding in findings[: min(len(findings), max(1, top), 10)]:
            console.print(
                f"[dim]{finding.school_id}: candidate='{finding.candidate_name}' "
                f"urls={', '.join(finding.evidence_urls[:3])}[/dim]"
            )
