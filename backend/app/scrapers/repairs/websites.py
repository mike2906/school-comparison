"""Repair command: revalidate websites through brand aliases and recover failed URLs."""

from __future__ import annotations

import builtins
import logging
import re
from collections import defaultdict
from typing import Optional
from urllib.parse import urlparse

from rich.console import Console
from rich.table import Table

from app.scrapers.stages import common as stage_common
from app.scrapers.stages import extract as extract_stage
from app.scrapers.stages import website as website_stage

logger = logging.getLogger(__name__)
console = Console()


_BRAND_ALIAS_GENERIC_EXACT = {
    "school",
    "kindergarten",
    "preschool",
    "academy",
    "училище",
    "детска градина",
    "гимназия",
}


_BRAND_ALIAS_BANNED_SUBSTRINGS = {
    "google reference school",
    "елитно канадско образование",
    "school community",
    "our identity",
    "иновативно училище",
}


_BRAND_ALIAS_BANNED_MARKERS = {
    "парти център",
    "портал",
    "@school",
    "→",
}


def _normalize_brand_alias_host_text(text: str) -> str:
    return re.sub(r"[^a-z0-9а-я]+", "", (text or "").lower())


def _brand_alias_token_variants(token: str) -> set[str]:
    from app.utils.transliteration import transliterate_bulgarian

    lowered = (token or "").lower()
    variants = {lowered, transliterate_bulgarian(lowered).lower()}
    if lowered.startswith("в"):
        variants.add("w" + transliterate_bulgarian(lowered[1:]).lower())
    if lowered.startswith("w"):
        variants.add("v" + lowered[1:])
    return {value for value in variants if value}


def _select_brand_aliases_for_school(candidate: dict[str, str] | None, website_url: str, school) -> list[str]:
    from app.scrapers.school_tokens import extract_school_name_tokens

    if not candidate:
        return []
    if school.school_type not in {"private", "international"}:
        return []

    host = urlparse(website_url or "").netloc.lower().replace("www.", "")
    host_compact = _normalize_brand_alias_host_text(host)
    aliases: list[str] = []
    seen: set[str] = set()

    for value in candidate.values():
        text = " ".join((value or "").split()).strip()
        lowered = text.lower()
        if not text or lowered in _BRAND_ALIAS_GENERIC_EXACT:
            continue
        if any(bad in lowered for bad in _BRAND_ALIAS_BANNED_SUBSTRINGS):
            continue
        if any(marker in lowered for marker in _BRAND_ALIAS_BANNED_MARKERS):
            continue
        if len(text.split()) > 7:
            continue
        if school.education_level == "kindergarten" and lowered.startswith(
            ("частно средно училище", "частно основно училище", "чоу ", "чсу ")
        ):
            continue

        tokens = [token for token in extract_school_name_tokens(text, limit=8) if len(token) >= 3]
        if not tokens:
            continue

        aligned = False
        for token in tokens:
            for variant in _brand_alias_token_variants(token):
                if len(variant) >= 3 and variant in host_compact:
                    aligned = True
                    break
            if aligned:
                break
        if not aligned:
            continue

        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        aliases.append(text)

    return aliases


async def repair_websites_command(
    *,
    school_name: Optional[str],
    school_id: Optional[int],
    city: Optional[str],
    country: str,
    limit: Optional[int],
    include_state: bool,
    dry_run: bool,
    recover_failed: bool,
    run_extract: bool,
):
    from sqlalchemy import select

    from app.database import async_session_maker
    from app.models import School, SourcePage
    from app.models.scrape_log import ScrapeType
    from app.scrapers.extraction.display_naming import _extract_display_name_i18n_deterministic
    from app.scrapers.url_validator import extract_validation_aliases, validate_school_url

    async with async_session_maker() as db:
        if school_name and not school_id:
            school_id = await stage_common.find_school_by_name(db, school_name, country)
            if not school_id:
                console.print(f"[red]School not found: {school_name}[/red]")
                return

        query = select(School).where(School.country_code == country)
        if city:
            query = query.where(School.city == city)
        if school_id:
            query = query.where(School.id == school_id)
        if not include_state:
            query = query.where(School.school_type.in_(["private", "international"]))
        if limit:
            query = query.limit(limit)

        schools = (await db.execute(query)).scalars().all()
        if not schools:
            console.print("[yellow]No schools matched the repair-websites filters.[/yellow]")
            return

        failed_validate_ids = [school.id for school in schools if school.scrape_status == "failed_validate"]
        if recover_failed and failed_validate_ids and not dry_run:
            console.print(f"[cyan]Recovering {len(failed_validate_ids)} failed website mappings...[/cyan]")
            for target_school_id in failed_validate_ids:
                try:
                    await website_stage.run_recover_failed_school(db, target_school_id, country)
                except Exception as exc:
                    logger.error("Failed website recovery for school %s: %s", target_school_id, exc)
            # Recovery sets new URLs; they go through the shared-site check like discovery.
            await website_stage.check_shared_sites(db, country=country, school_ids=failed_validate_ids)

            schools = (await db.execute(query)).scalars().all()

        schools_by_id = {school.id: school for school in schools}
        website_school_ids = [school.id for school in schools if school.website_url]
        if not website_school_ids:
            console.print("[yellow]No schools with website URLs matched the repair-websites filters.[/yellow]")
            return

        pages = (
            await db.execute(
                select(SourcePage)
                .where(
                    SourcePage.school_id.in_(website_school_ids),
                    SourcePage.scrape_type == ScrapeType.WEBSITE,
                    SourcePage.is_valid != False,  # noqa: E712  (SQL expression)
                    SourcePage.raw_markdown.isnot(None),
                )
                .order_by(SourcePage.school_id, SourcePage.id)
            )
        ).scalars().all()

        pages_by_school: dict[int, list] = defaultdict(builtins.list)
        for page in pages:
            pages_by_school[page.school_id].append(page)

        alias_updates: dict[int, list[str]] = {}
        preview_rows: list[tuple[int, str, list[str]]] = []
        for target_school_id in website_school_ids:
            school = schools_by_id[target_school_id]
            school_pages = pages_by_school.get(target_school_id)
            if not school_pages:
                continue

            candidate = _extract_display_name_i18n_deterministic(
                text="\n\n".join((page.raw_markdown or "") for page in school_pages[:4]),
                registry_name=(school.name_i18n or {}).get("bg") or (school.name_i18n or {}).get("en"),
                country_code=school.country_code,
            )
            aliases = _select_brand_aliases_for_school(candidate, school.website_url, school)
            if not aliases:
                continue

            existing_aliases = extract_validation_aliases(school.attributes)
            merged_aliases: list[str] = []
            seen: set[str] = set()
            for value in existing_aliases + aliases:
                key = value.casefold()
                if key in seen:
                    continue
                seen.add(key)
                merged_aliases.append(value)
            if merged_aliases == existing_aliases:
                continue

            alias_updates[target_school_id] = merged_aliases
            preview_rows.append((target_school_id, stage_common.school_label(school), aliases))

        console.print(f"[cyan]Website repair candidates: {len(preview_rows)}[/cyan]")
        console.print(f"  Failed websites to recover: {len(failed_validate_ids)}")
        console.print(f"  Alias promotions: {len(alias_updates)}")
        console.print(f"  Re-extract after validate: {run_extract and not dry_run}")

        if preview_rows:
            preview_table = Table(title="Alias Promotions")
            preview_table.add_column("ID", style="cyan", no_wrap=True)
            preview_table.add_column("School")
            preview_table.add_column("New aliases")
            for row in preview_rows[:20]:
                preview_table.add_row(str(row[0]), row[1], ", ".join(row[2]))
            console.print(preview_table)
            if len(preview_rows) > 20:
                console.print(f"[dim]Showing first 20 of {len(preview_rows)} alias promotions[/dim]")

        if dry_run:
            return

        for target_school_id, aliases in alias_updates.items():
            school = schools_by_id[target_school_id]
            attrs = dict(school.attributes or {})
            attrs["name_aliases"] = aliases
            school.attributes = attrs
        await db.commit()

        validate_target_ids = sorted(
            {
                school.id
                for school in schools
                if school.website_url and (school.id in alias_updates or school.id in failed_validate_ids)
            }
        )
        if not validate_target_ids:
            console.print("[yellow]No schools needed revalidation after alias promotion.[/yellow]")
            return

        validated_ids: list[int] = []
        invalid_rows: list[tuple[int, str, str]] = []
        for target_school_id in validate_target_ids:
            school = schools_by_id[target_school_id]
            school_name_value = (school.name_i18n or {}).get("bg") or (school.name_i18n or {}).get("en")
            aliases = dict(school.attributes or {}).get("name_aliases", [])
            result, _, reason = await validate_school_url(
                school_id=target_school_id,
                url=school.website_url,
                country_code=school.country_code,
                update_db=True,
                school_name=school_name_value,
                school_aliases=aliases,
            )
            if result.value == "valid":
                validated_ids.append(target_school_id)
            else:
                invalid_rows.append((target_school_id, stage_common.school_label(school), reason or result.value))

        console.print("[green]✓ Validation complete:[/green]")
        console.print(f"  Validated: {len(validated_ids)}")
        console.print(f"  Not validated: {len(invalid_rows)}")
        for row in invalid_rows[:10]:
            console.print(f"  [yellow]{row[0]} {row[1]}[/yellow]: {row[2]}")

        if run_extract and validated_ids:
            console.print(f"[cyan]Refreshing extraction for {len(validated_ids)} schools...[/cyan]")
            await extract_stage.run_extract_batch(
                db=db,
                country=country,
                city=city or "",
                limit=None,
                include_extracted=True,
                school_ids=validated_ids,
            )
