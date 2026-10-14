"""Social-links stage: the Facebook page and Instagram profile a school's own website links.

Reads the home page (and the contact page when the home page links neither network), takes
the links and Page Plugin widgets that point to a page (``app.utils.social_links``), and
stores ``attributes["social_links"]``. No model calls. A page linked from the websites of
two different schools is a web designer's or a platform's page, not the school's, and is
dropped. The default is a dry run that writes nothing. Not part of ``all``.
"""

from __future__ import annotations

import datetime
import logging
import re
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Optional
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import CRAWLER_USER_AGENT
from app.models import School
from app.scrapers.fee_pages import fee_http_client
from app.utils.school_facts import SITE_ACCEPTED_STATUSES
from app.utils.social_links import ATTRIBUTE_KEY, NETWORKS, SCHEMA_VERSION, pick_links

logger = logging.getLogger(__name__)

# (final URL, HTML), or None when the page could not be read.
Fetch = Callable[[str], Awaitable[Optional[tuple[str, str]]]]

_CONTACT_RE = re.compile(r"контакт|contact|kontakt", re.IGNORECASE)


def page_links(html: str, base: str) -> list[str]:
    """Every link and frame address on the page, absolute."""
    soup = BeautifulSoup(html, "html.parser")
    found = [tag.get("href") for tag in soup.find_all("a")] + [tag.get("src") for tag in soup.find_all("iframe")]
    return [urljoin(base, str(href).strip()) for href in found if href]


def _on_site(url: str, site: str) -> bool:
    """On the school's own site: the same host, and under its path when it has one
    (sites.google.com/view/a is not /view/b)."""
    parsed, home = urlparse(url), urlparse(site)
    if _host(url) != _host(site):
        return False
    prefix = home.path.rstrip("/").lower()
    path = parsed.path.lower()
    return not prefix or path == prefix or path.startswith(prefix + "/")


def _contact_page(html: str, base: str, site: str) -> Optional[str]:
    for tag in BeautifulSoup(html, "html.parser").find_all("a"):
        href = urljoin(base, str(tag.get("href") or ""))
        if _on_site(href, site) and _CONTACT_RE.search(f"{href} {tag.get_text(' ', strip=True)}"):
            return href
    return None


async def read_school(school: School, fetch: Fetch) -> dict[str, Any]:
    """``{"read": bool, "links": {...}, "ambiguous": [...], "pages": [...]}``."""
    home = await fetch(school.website_url)
    if home is None:
        return {"read": False, "links": {}, "ambiguous": [], "pages": []}
    final, html = home
    hrefs = page_links(html, final)
    pages = [final]
    links, ambiguous = pick_links(hrefs)
    if not links and not ambiguous:
        contact = _contact_page(html, final, final)
        page = await fetch(contact) if contact else None
        if page is not None:
            pages.append(page[0])
            links, ambiguous = pick_links(page_links(page[1], page[0]))
    return {"read": True, "links": links, "ambiguous": ambiguous, "pages": pages}


def _host(url: Optional[str]) -> str:
    return (urlparse(url or "").hostname or "").lower().removeprefix("www.")


def drop_shared(reads: dict[int, dict[str, Any]], sites: dict[int, Optional[str]]) -> None:
    """Drop, in place, any link found for schools on two different websites.

    ``reads`` maps school id to its read (this run's, and the stored links of every other
    school); ``sites`` maps school id to its website URL. Schools on one website host (a
    school and its kindergarten) may share a page; schools on a shared platform's hosts
    (sites.google.com/x and /y share a host) still count as one site, a gap left as is."""
    domains: dict[str, set[str]] = {}
    for school_id, read in reads.items():
        for url in read["links"].values():
            domains.setdefault(url, set()).add(_host(sites.get(school_id)))
    for read in reads.values():
        for network, url in list(read["links"].items()):
            if len(domains[url]) > 1:
                del read["links"][network]
                read.setdefault("shared", []).append(network)


def links_block(links: dict[str, str], website_url: Optional[str]) -> dict[str, Any]:
    return {
        "_schema_version": SCHEMA_VERSION,
        "read_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "website_url": website_url,
        **{network: links[network] for network in NETWORKS if network in links},
    }


def _http_fetch(client, robots) -> Fetch:
    async def fetch(url: str) -> Optional[tuple[str, str]]:
        try:
            if not await robots.can_fetch(url, CRAWLER_USER_AGENT):
                return None
            response = await client.get(url, follow_redirects=True, timeout=30)
        except Exception as exc:  # a site that is down is reported and keeps its stored links
            logger.info("social-links could not read %s: %s", url, exc)
            return None
        if response.status_code != 200 or "html" not in response.headers.get("content-type", "html"):
            return None
        return str(response.url), response.text

    return fetch


async def _read_all(db: AsyncSession, ids: list[int], fetch: Fetch) -> dict[int, dict[str, Any]]:
    return {school_id: await read_school(await db.get(School, school_id), fetch) for school_id in ids}


async def run_social_links(
    db: AsyncSession,
    *,
    country: str = "bg",
    city: Optional[str] = None,
    school_ids: Sequence[int] = (),
    limit: Optional[int] = None,
    apply: bool = False,
    fetch: Optional[Fetch] = None,
) -> dict[str, Any]:
    """Run over schools with a website URL validation accepted; returns the report."""
    query = select(School.id).where(
        School.country_code == country,
        School.website_url.isnot(None),
        School.scrape_status.in_(SITE_ACCEPTED_STATUSES),
    )
    if school_ids:
        query = query.where(School.id.in_(list(school_ids)))
    if city:
        query = query.where(func.lower(School.city) == city.lower())
    ids = list((await db.execute(query.order_by(School.id).limit(limit))).scalars().all())

    if fetch is None:
        from crawl4ai.utils import RobotsParser  # type: ignore

        async with fee_http_client() as client:
            reads = await _read_all(db, ids, _http_fetch(client, RobotsParser()))
    else:
        reads = await _read_all(db, ids, fetch)

    # The links stored for every other school count when telling a shared page.
    rows = await db.execute(select(School.id, School.website_url, School.attributes).where(School.country_code == country))
    sites: dict[int, Optional[str]] = {}
    pool = dict(reads)
    for school_id, website_url, attributes in rows:
        sites[school_id] = website_url
        block = (attributes or {}).get(ATTRIBUTE_KEY)
        if school_id not in reads and isinstance(block, dict):
            pool[school_id] = {"links": {n: block[n] for n in NETWORKS if isinstance(block.get(n), str)}}
    drop_shared(pool, sites)

    results = []
    for school_id in ids:
        school = await db.get(School, school_id)
        read = reads[school_id]
        result = {"school_id": school_id, "website": school.website_url, **read}
        if not read["read"]:
            result["outcome"] = "not_read"  # keeps the stored links
        else:
            result["outcome"] = "found" if read["links"] else "none"
            if apply:
                school.attributes = {
                    **(school.attributes or {}),
                    ATTRIBUTE_KEY: links_block(read["links"], school.website_url),
                }
        results.append(result)
    if apply:
        # A page this run found shared is cleared from the other schools that store it too.
        for school_id, read in pool.items():
            if school_id in reads or not read.get("shared"):
                continue
            school = await db.get(School, school_id)
            block = {k: v for k, v in school.attributes[ATTRIBUTE_KEY].items() if k not in read["shared"]}
            school.attributes = {**school.attributes, ATTRIBUTE_KEY: block}
        await db.commit()
    outcomes: dict[str, int] = {}
    for result in results:
        outcomes[result["outcome"]] = outcomes.get(result["outcome"], 0) + 1
    found = {n: sum(n in r["links"] for r in results) for n in NETWORKS}
    return {"apply": apply, "schools": len(results), "outcomes": outcomes, "found": found, "results": results}
