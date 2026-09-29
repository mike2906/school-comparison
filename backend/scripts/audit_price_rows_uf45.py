#!/usr/bin/env python3
"""UF45: read-only audit of published price rows and display names.

    uv run python scripts/audit_price_rows_uf45.py

Audits exactly what ``GET /schools/{id}`` returns: each school is loaded with the
detail query and projected through ``SchoolDetailResponse`` (the same gates), and every
published price row is checked against the stored text of its linked source page with
the rules in ``app.scrapers.price_evidence`` (1-4), and every published name against the
names its site-group siblings publish (5, the broad form: any two institutions). Stage 6
validation enforces rules 1-4 since UF45 step 2, so on a freshly validated DB their hits
are period conflicts (a warning) and schools validated before step 2.

Writes ``reports/uf45/<timestamp>/hits.csv`` and ``summary.md``. Nothing is written to
the database: the session only reads and is rolled back.
"""
from __future__ import annotations

import asyncio
import csv
import os
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.scrapers.price_evidence import (
    PriceRow,
    check_price_row,
    label_spans,
    normalize_text,
    shared_names,
)
from app.scrapers.shared_site_check import level_family, site_group_key

REPORT_ROOT = Path(os.environ.get("UF45_REPORT_ROOT") or Path(__file__).parent.parent / "reports" / "uf45")


@dataclass(frozen=True)
class Hit:
    school_id: int
    row_id: Optional[int]
    rule: str
    detail: str
    snippet: str


def audit_price_row(
    school_id: int, row_id: int, row: PriceRow, page_text: str | None, school_family: str
) -> list[Hit]:
    return [
        Hit(school_id, row_id, f.rule, f.detail, f.snippet)
        for f in check_price_row(row, page_text, school_family)
    ]


@dataclass(frozen=True)
class PublishedName:
    school_id: int
    website_url: Optional[str]
    names: tuple[str, ...]


def audit_names(schools: Iterable[PublishedName]) -> list[Hit]:
    """Rule 5: a published name equal to another institution's in the same site group."""
    groups: dict[str, list[PublishedName]] = defaultdict(list)
    for school in schools:
        key = site_group_key(school.website_url)
        if key:
            groups[key].append(school)
    hits = []
    for key, members in groups.items():
        for school in members:
            for other in members:
                if other.school_id == school.school_id:
                    continue
                shared = shared_names(school.names, other.names)
                if shared:
                    hits.append(Hit(
                        school.school_id, None, "5_name_not_sibling",
                        f"name {sorted(shared)[0]!r} also published by school {other.school_id}",
                        f"site group {key}",
                    ))
    return hits


# ---------------------------------------------------------------------------
# Database (read-only)
# ---------------------------------------------------------------------------

async def _collect() -> tuple[list[Hit], dict[str, Any]]:
    from sqlalchemy import or_, select

    from app.database import engine
    from app.models import School, SourcePage
    from app.models.pricing import Pricing
    from app.schemas.school import SchoolDetailResponse
    from app.services.school_service import SchoolService
    from sqlalchemy.ext.asyncio import AsyncSession

    hits: list[Hit] = []
    stats: Counter = Counter()
    published_names: list[PublishedName] = []
    pending: list[tuple[int, int, PriceRow, Optional[int], str]] = []
    async with engine.connect() as conn:
        trans = await conn.begin()
        try:
            db = AsyncSession(bind=conn, expire_on_commit=False)
            priced = select(Pricing.school_id).distinct()
            ids = (
                await db.execute(
                    select(School.id)
                    .where(or_(School.id.in_(priced), School.website_url.isnot(None)))
                    .order_by(School.id)
                )
            ).scalars().all()
            service = SchoolService(db)
            for school_id in ids:
                school = await service.get_school_with_details(school_id)
                if school is None:
                    continue
                response = SchoolDetailResponse.model_validate(school)
                published_names.append(PublishedName(
                    school.id, school.website_url, tuple(response.resolved_name_i18n.values())
                ))
                if not response.pricing:
                    continue
                stats["schools_with_published_prices"] += 1
                family = level_family(school.education_level)
                orm_rows = {row.id: row for row in school.pricing}
                for published in response.pricing:
                    stats["published_rows"] += 1
                    orm = orm_rows[published.id]
                    pending.append(
                        (school.id, orm.id, PriceRow.from_pricing(orm), orm.source_page_id, family)
                    )
                db.expunge_all()
            page_ids = {page_id for *_, page_id, _ in pending if page_id is not None}
            pages = dict(
                (
                    await db.execute(
                        select(SourcePage.id, SourcePage.raw_markdown).where(SourcePage.id.in_(page_ids))
                    )
                ).all()
            )
        finally:
            await trans.rollback()
    await engine.dispose()
    for school_id, row_id, row, page_id, family in pending:
        page_text = pages.get(page_id)
        row_hits = audit_price_row(school_id, row_id, row, page_text, family)
        if row_hits:
            stats["rows_with_hits"] += 1
        if row.label and not label_spans(normalize_text(page_text), row.label):
            stats["rows_label_not_on_page"] += 1
        hits.extend(row_hits)
    stats["schools_audited"] = len(published_names)
    hits.extend(audit_names(published_names))
    return hits, dict(stats)


def _write(hits: list[Hit], stats: dict[str, Any]) -> Path:
    out = REPORT_ROOT / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out.mkdir(parents=True, exist_ok=True)
    with (out / "hits.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["school_id", "row_id", "rule", "detail", "evidence_snippet"])
        for h in sorted(hits, key=lambda h: (h.rule, h.school_id, h.row_id or 0)):
            writer.writerow([h.school_id, h.row_id or "", h.rule, h.detail, h.snippet])
    per_rule = Counter(h.rule for h in hits)
    lines = [
        f"# UF45 audit {out.name}",
        "",
        f"- schools audited (published projection): {stats.get('schools_audited', 0)}",
        f"- schools with published prices: {stats.get('schools_with_published_prices', 0)}",
        f"- published price rows: {stats.get('published_rows', 0)}",
        f"- rows with at least one hit: {stats.get('rows_with_hits', 0)}",
        f"- rows whose label is not on the page (rule 1 distance not checked): "
        f"{stats.get('rows_label_not_on_page', 0)}",
        "",
        "| rule | hits | schools |",
        "|---|---|---|",
    ]
    for rule in sorted(per_rule):
        schools = len({h.school_id for h in hits if h.rule == rule})
        lines.append(f"| {rule} | {per_rule[rule]} | {schools} |")
    (out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def main() -> None:
    hits, stats = asyncio.run(_collect())
    out = _write(hits, stats)
    print((out / "summary.md").read_text(encoding="utf-8"))
    print(f"report: {out}")


if __name__ == "__main__":
    main()
