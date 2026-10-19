"""Site-city stage: withhold an accepted website whose home page names another town.

URL validation now rejects a site whose title or main heading names another town
(``app.scrapers.site_city``), but validation runs only for new or failed sites. This stage
re-reads the home page of every accepted site and applies the same rule, so a site
accepted before the rule, or one that changed hands since, is caught too: a hit takes
the validation failure path (website-derived data withheld, ``failed_validate``), and
the next ``recover-failed-urls`` searches for the school's real site. No model calls.
The default is a dry run that writes nothing.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any, Optional

from bs4 import BeautifulSoup
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import School
from app.scrapers.fee_pages import fee_http_client
from app.scrapers.site_city import other_town_named
from app.scrapers.stages.social_links import Fetch, _http_fetch
from app.utils.school_facts import SITE_ACCEPTED_STATUSES

logger = logging.getLogger(__name__)


def identity_texts(html: str) -> list[str]:
    """A home page's title, first two main headings and site name."""
    soup = BeautifulSoup(html, "html.parser")
    texts = [soup.title.get_text(" ", strip=True) if soup.title else ""]
    texts += [tag.get_text(" ", strip=True) for tag in soup.find_all("h1")[:2]]
    texts += [str(tag.get("content") or "") for tag in soup.find_all("meta", attrs={"property": "og:site_name"})]
    return [text for text in texts if text]


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
    query = select(School.id, School.website_url, School.city, School.name_i18n).where(
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
        hits = []
        unread = 0
        for school_id, website_url, school_city, name_i18n in rows:
            page = await fetch(website_url)
            if page is None:
                unread += 1
                continue
            town = other_town_named(identity_texts(page[1]), school_city)
            if town:
                hits.append(
                    {
                        "school_id": school_id,
                        "name_bg": (name_i18n or {}).get("bg"),
                        "website": website_url,
                        "town": town,
                    }
                )
        return hits, unread

    if fetch is not None:
        hits, unread = await check_all(fetch)
    else:
        from crawl4ai.utils import RobotsParser  # type: ignore

        async with fee_http_client() as client:
            hits, unread = await check_all(_http_fetch(client, RobotsParser()))

    if apply:
        from app.scrapers.url_validator import ValidationResult, _update_validation_result

        for hit in hits:
            await _update_validation_result(
                hit["school_id"],
                hit["website"],
                ValidationResult.INVALID,
                None,
                f"Website names another town: {hit['town']}",
            )
            hit["withheld"] = True
    return {"checked": len(rows), "unread": unread, "hits": hits, "applied": apply}
