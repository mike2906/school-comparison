"""Find-websites stage: search for the website of a private school that has none.

The register import brings a school in with the register's website, and for a private
school that is usually blank. Nothing searched for one afterwards: the monthly import
runs no website stage, and a school discovery once marked ``no_official_website`` never
entered a batch again. On 2026-10-23, 33 of the 174 private institutions had no site,
19 of them with one a search finds (534 Веда's wedaschule.com, 553's janbibijan.com), so
the fee and facts readers never ran for them.

Each school is searched (website discovery, SearXNG: free) and its best candidates go
through URL validation without the model fallback, so a run costs nothing: an ambiguous
site stays unstored. Only a site validation accepts is stored, the way ``check-website``
stores one (:func:`app.scrapers.stages.website.store_given_site`). A school searched in
the last ``retry_days`` is skipped, so the monthly run does not repeat a search that found
nothing until sites may have changed. The default is a dry run that writes nothing.

Validation counts a school's profile words as its name ("програмиране", "роботика"), so
a search for 150 ЧПГ по програмиране и роботика "Стив Джобс" found SoftUni Будител's site
valid. A run with nobody watching it also needs the school's own name, a part of its
register name in quotes ("Стив Джобс"), on the page (:func:`brand_named`); a name with
no quoted part is not checked.

A stored site's fees and facts come with the next scheduled reads (monthly fees, the
twice-yearly facts) or a thread's run for the schools found.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import School
from app.utils.transliteration import transliterate_bulgarian

logger = logging.getLogger(__name__)

RETRY_DAYS = 90
# Candidates validated per school: search often ranks a directory or a news page first.
MAX_CANDIDATES = 3
SEARCHED_AT_KEY = "website_search_checked_at"


_QUOTES_RE = re.compile(r'["„“”«»]')
_LEGAL_FORM_RE = re.compile(r"^(?:е?оод|ад|еад|кд|сд|ет|ltd|llc)?$", re.IGNORECASE)
_TYPE_RE = re.compile(r"^(?:ч?(?:дг|оу|су|ну|пг|пгдн|сои|ег)|частн\w*\b.*)$", re.IGNORECASE)


def brands(name: Optional[str]) -> list[str]:
    """The quoted parts of a register name that name the school itself: "Стив Джобс" in
    ЧПГ ... "СТИВ ДЖОБС" ЕООД, "Ян Бибиян" and "Мега-Х" in "ЧДГ "ЯН БИБИЯН" - МЕГА-Х" ООД.
    Empty when the name has no part besides its type, its legal form or one long name."""
    parts = [" ".join(part.split()).strip(" -,") for part in _QUOTES_RE.split(name or "")]
    parts = [part for part in parts if part and not _LEGAL_FORM_RE.match(part)]
    if len(parts) < 2:
        return []
    return [part for part in parts if not _TYPE_RE.match(part) and len(part.split()) <= 4]


def brand_named(name: Optional[str], page_text: str) -> bool:
    """True when the page names one of the school's quoted names (either script), or it has none."""
    own = brands(name)
    if not own:
        return True
    text = (page_text or "").casefold()
    for spelling in {variant for part in own for variant in (part, transliterate_bulgarian(part))}:
        words = [word for word in re.findall(r"[^\W\d_]+|\d+", spelling.casefold()) if len(word) >= 3]
        if words and all(re.search(rf"(?<![^\W\d_]){re.escape(word)}(?![^\W\d_])", text) for word in words):
            return True
    return False


async def _page_text(url: str) -> str:
    import httpx

    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            return (await client.get(url)).text
    except httpx.HTTPError:
        return ""


def _searched_recently(attributes: Any, now: datetime, retry_days: int) -> bool:
    value = (attributes or {}).get(SEARCHED_AT_KEY) if isinstance(attributes, dict) else None
    try:
        searched_at = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return False
    if searched_at.tzinfo is None:
        searched_at = searched_at.replace(tzinfo=timezone.utc)
    return now - searched_at < timedelta(days=retry_days)


async def _candidates(discoverer, school: School) -> list[str]:
    """The school's best search candidates, best first (no writes)."""
    name = (school.name_i18n or {}).get("bg") or (school.name_i18n or {}).get("en")
    aliases = discoverer._validation_aliases_for_school(school)
    hints = discoverer._discovery_hints_for_school(school)
    pool = [(url, "search") for url in await discoverer._search_candidates(school)]
    discoverer.social_hits.pop(school.id, None)
    picked: list[str] = []
    seen: set[str] = set()
    while len(picked) < MAX_CANDIDATES:
        url, _ = discoverer._pick_best_candidate(
            pool, set(seen), school_name=name, school_aliases=aliases, discovery_hints=hints
        )
        if not url:
            break
        seen.add(url)
        picked.append(url)
    return picked


async def run_find_websites(
    db: AsyncSession,
    *,
    country: str = "bg",
    city: Optional[str] = "sofia",
    school_ids: Sequence[int] = (),
    limit: Optional[int] = None,
    retry_days: int = RETRY_DAYS,
    apply: bool = False,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Search for the sites of the schools with none (the given ids, or the private and
    international schools in ``city``); returns the report."""
    from app.scrapers.stages.website import store_given_site
    from app.scrapers.url_validator import URLValidator, ValidationResult, extract_validation_aliases
    from app.scrapers.website_discovery import WebsiteDiscoverer

    now = now or datetime.now(timezone.utc)
    query = select(School).options(selectinload(School.locations)).where(
        School.country_code == country, School.website_url.is_(None)
    )
    if school_ids:
        query = query.where(School.id.in_(list(school_ids)))
    else:
        query = query.where(School.school_type.in_(["private", "international"]))
    if city:
        query = query.where(func.lower(School.city) == city.lower())
    schools = (await db.execute(query.order_by(School.id))).scalars().all()
    due = [school for school in schools if school_ids or not _searched_recently(school.attributes, now, retry_days)]
    if limit is not None:
        due = due[:limit]

    # Ids, not objects: a rollback after one school's failure expires every loaded object.
    due_ids = [school.id for school in due]
    discoverer = WebsiteDiscoverer(country_code=country)
    results: list[dict[str, Any]] = []
    for school_id in due_ids:
        school = (
            await db.execute(
                select(School)
                .options(selectinload(School.locations))
                .where(School.id == school_id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        name = (school.name_i18n or {}).get("bg") or (school.name_i18n or {}).get("en")
        result: dict[str, Any] = {"school_id": school_id, "name_bg": (school.name_i18n or {}).get("bg"), "tried": []}
        try:
            candidates = await _candidates(discoverer, school)
            validator = URLValidator(country_code=country, school_id=school_id, school_city=school.city)
            for url in candidates:
                verdict, final_url, reason = await validator.validate_url(
                    url,
                    use_llm_fallback=False,
                    school_name=name,
                    school_aliases=extract_validation_aliases(school.attributes),
                )
                result["tried"].append({"url": url, "result": verdict.value, "reason": reason})
                if verdict is ValidationResult.VALID and not brand_named(name, await _page_text(final_url or url)):
                    result["tried"][-1].update(result="invalid", reason=f"The page does not name {' or '.join(brands(name))}")
                    continue
                if verdict is ValidationResult.VALID:
                    result["found"] = final_url or url
                    if apply:
                        await store_given_site(db, school, url, final_url, reason, method="search")
                    break
        except Exception as exc:  # one school's failure must not stop the run
            logger.exception("find-websites could not search for school %s", school_id)
            result["error"] = f"{type(exc).__name__}: {exc}"[:200]
            await db.rollback()
        if apply and "error" not in result:
            fresh = await db.get(School, school_id)
            attrs = dict(fresh.attributes or {})
            attrs[SEARCHED_AT_KEY] = now.isoformat()
            fresh.attributes = attrs
            await db.commit()
        results.append(result)

    found = [result for result in results if result.get("found")]
    if apply and found:
        from app.scrapers.stages.website import check_shared_sites

        await check_shared_sites(db, country=country, school_ids=[result["school_id"] for result in found])
    return {
        "schools_without_site": len(schools),
        "searched": len(results),
        "found": len(found),
        "errors": sum("error" in result for result in results),
        "applied": apply,
        "schools": results,
    }
