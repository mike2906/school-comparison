"""Site-city stage: withhold an accepted website whose home page names another town or
another numbered school.

URL validation now rejects a site whose title or main heading names another town or
numbers another school (``app.scrapers.site_city``), but validation runs only for new or
failed sites. This stage re-reads the home page of every accepted site and applies the
same rules, so a site
accepted before the rule, or one that changed hands since, is caught too: a hit takes
the validation failure path (website-derived data withheld, ``failed_validate``), and
the next ``recover-failed-urls`` searches for the school's real site. No model calls.
The default is a dry run that writes nothing. A home page the plain fetch cannot read (a
bot wall, a script-built page) is counted as not read and left as it is.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from typing import Any, Optional

from bs4 import BeautifulSoup
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import School
from app.scrapers.fee_pages import fee_http_client
from app.scrapers.site_city import identity_texts, other_number_named, other_town_named
from app.scrapers.stages.social_links import Fetch, _http_fetch
from app.utils.school_facts import SITE_ACCEPTED_STATUSES

logger = logging.getLogger(__name__)

# Home pages read at once: a slow or dead site waits out its timeout without holding up the rest.
CONCURRENCY = 8


async def run_site_city(
    db: AsyncSession,
    *,
    country: str = "bg",
    city: Optional[str] = "sofia",
    school_ids: Sequence[int] = (),
    limit: Optional[int] = None,
    apply: bool = False,
    fetch: Optional[Fetch] = None,
) -> dict[str, Any]:
    """Check the accepted sites (the given ids, or all in ``city``); returns the report."""
    from app.scrapers.url_validator import extract_validation_aliases

    query = select(School.id, School.website_url, School.city, School.name_i18n, School.attributes).where(
        School.country_code == country,
        School.website_url.isnot(None),
        School.scrape_status.in_(SITE_ACCEPTED_STATUSES),
    )
    if school_ids:
        query = query.where(School.id.in_(list(school_ids)))
    if city:
        query = query.where(func.lower(School.city) == city.lower())
    rows = (await db.execute(query.order_by(School.id).limit(limit))).all()

    async def check_all(fetch: Fetch) -> tuple[list[dict[str, Any]], int]:
        semaphore = asyncio.Semaphore(CONCURRENCY)

        async def check(row) -> Optional[dict[str, Any]]:
            school_id, website_url, school_city, name_i18n, attributes = row
            names = [*(name_i18n or {}).values(), *extract_validation_aliases(attributes)]
            try:
                async with semaphore:
                    page = await fetch(website_url)
                if page is None:
                    return None
                texts = identity_texts(BeautifulSoup(page[1], "html.parser"))
                town = other_town_named(texts, school_city)
                school = None if town else other_number_named(texts, names)
            except Exception:  # one odd page must not stop the run: it counts as not read
                logger.exception("site-city could not check school %s", school_id)
                return None
            return {
                "school_id": school_id,
                "name_bg": (name_i18n or {}).get("bg"),
                "website": website_url,
                "town": town,
                "school": school,
                "reason": f"Website names another town: {town}" if town else f"Website names another school: {school}",
            }

        checked = await asyncio.gather(*(check(row) for row in rows))
        hits = [item for item in checked if item and (item["town"] or item["school"])]
        return hits, sum(item is None for item in checked)

    if fetch is not None:
        hits, unread = await check_all(fetch)
    else:
        from crawl4ai.utils import RobotsParser  # type: ignore

        async with fee_http_client() as client:
            hits, unread = await check_all(_http_fetch(client, RobotsParser()))

    if apply:
        from app.scrapers.url_validator import URLValidator, ValidationResult, _update_validation_result

        normalize = URLValidator(country).normalize_url
        for hit in hits:
            # The form validation stores its source page under (validate_school_url).
            await _update_validation_result(
                hit["school_id"],
                normalize(hit["website"]) or hit["website"],
                ValidationResult.INVALID,
                None,
                hit["reason"],
            )
            hit["withheld"] = True
    return {"checked": len(rows), "unread": unread, "hits": hits, "applied": apply}
