"""Website navigation utilities for Stage 3 of the scraping pipeline.

This module keeps Stage 3 intentionally simple:
- fetch homepage
- discover same-domain links
- classify likely page category with heuristics
- cache discovered pages in `source_pages`
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urldefrag

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import School, SourcePage, ScrapeType
from app.scrapers.base import BaseScraper
from app.scrapers.url_validator import URLValidator

logger = logging.getLogger(__name__)


@dataclass
class NavigatedPage:
    """In-memory representation of one navigated page."""

    url: str
    category: str | None
    markdown: str | None
    content_hash: str


class WebsiteNavigator:
    """Simple website navigator based on HTML link discovery."""

    HTTP_TIMEOUT = 10.0
    HTTP_ATTEMPTS = 2
    MAX_LINKS = 20
    MAX_PAGES = 8
    REQUEST_HEADERS = {
        "User-Agent": "Mozilla/5.0 (compatible; SchoolScraper/1.0; +https://kg.sofia.bg)",
        "Accept-Encoding": "identity",
    }
    TRACKING_QUERY_PARAMS = {
        "fbclid",
        "gclid",
        "yclid",
        "mc_cid",
        "mc_eid",
        "ref",
        "source",
    }

    # Keep categories aligned with SourcePage.page_category comment.
    CATEGORY_KEYWORDS = {
        "about": [
            "about",
            "za-nas",
            "за-нас",
            "история",
            "мисия",
            "екип",
            "who-we-are",
        ],
        "pricing": [
            "price",
            "prices",
            "pricing",
            "fees",
            "fee",
            "tuition",
            "taksi",
            "ceni",
            "цен",
            "такс",
            "цени",
            "такси",
        ],
        "admission": [
            "admission",
            "admissions",
            "apply",
            "enroll",
            "enrol",
            "priem",
            "прием",
            "кандидат",
            "запис",
            "впис",
        ],
        "contact": [
            "contact",
            "contacts",
            "kontakt",
            "контакт",
            "връзка",
            "телефон",
            "phone",
            "address",
            "адрес",
        ],
        "gallery": [
            "gallery",
            "photo",
            "photos",
            "album",
            "галерия",
            "сним",
        ],
    }

    NON_HTML_EXTENSIONS = {
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".svg",
        ".webp",
        ".css",
        ".js",
        ".zip",
        ".rar",
        ".7z",
        ".mp4",
        ".mp3",
    }

    def __init__(self, country_code: str = "bg"):
        self.country_code = country_code

    async def discover_pages(self, website_url: str) -> tuple[str, list[NavigatedPage]]:
        """Discover and lightly classify pages for one school website.

        Returns:
            Tuple: (final_home_url_after_redirects, discovered_pages)
        """
        async with httpx.AsyncClient(
            timeout=self.HTTP_TIMEOUT,
            follow_redirects=True,
            headers=self.REQUEST_HEADERS,
        ) as client:
            home_response = await client.get(website_url)
            home_response.raise_for_status()

            final_home_url = self._normalize_url(str(home_response.url))
            pages: list[NavigatedPage] = []

            # Homepage is always stored.
            home_soup = BeautifulSoup(home_response.text, "html.parser")
            home_markdown = self._extract_text_content(home_soup)
            home_category = self.classify_page(
                final_home_url,
                title=home_soup.title.string if home_soup.title and home_soup.title.string else "",
                anchor_text="",
            )
            pages.append(
                NavigatedPage(
                    url=final_home_url,
                    category=home_category,
                    markdown=home_markdown,
                    content_hash=BaseScraper.compute_hash(home_markdown or final_home_url),
                )
            )

            discovered_links = self._extract_internal_links(final_home_url, home_soup)
            prioritized_links = self._prioritize_links(discovered_links)[: self.MAX_LINKS]

            for page_url, anchor_text in prioritized_links[: self.MAX_PAGES]:
                category = self.classify_page(page_url, title="", anchor_text=anchor_text)
                markdown = None
                content_hash_source = page_url

                if self._should_fetch(page_url):
                    try:
                        page_response = await client.get(page_url)
                        if page_response.status_code < 400:
                            ctype = page_response.headers.get("content-type", "")
                            if "text/html" in ctype.lower():
                                soup = BeautifulSoup(page_response.text, "html.parser")
                                markdown = self._extract_text_content(soup)
                                inferred = self.classify_page(
                                    page_url,
                                    title=soup.title.string if soup.title and soup.title.string else "",
                                    anchor_text=anchor_text,
                                )
                                if inferred:
                                    category = inferred
                                if markdown:
                                    content_hash_source = markdown
                    except httpx.HTTPError:
                        # Keep discovered link even if fetch failed; extraction can retry later.
                        pass

                pages.append(
                    NavigatedPage(
                        url=page_url,
                        category=category,
                        markdown=markdown,
                        content_hash=BaseScraper.compute_hash(content_hash_source),
                    )
                )

            return final_home_url, pages

    def classify_page(self, url: str, title: str = "", anchor_text: str = "") -> str | None:
        """Heuristic page category classifier using URL/title/anchor tokens."""
        haystack = f"{url} {title} {anchor_text}".lower()
        for category, keywords in self.CATEGORY_KEYWORDS.items():
            if any(keyword in haystack for keyword in keywords):
                return category
        return None

    def _extract_internal_links(self, base_url: str, soup: BeautifulSoup) -> list[tuple[str, str]]:
        """Extract deduplicated internal links as (url, anchor_text)."""
        base_host = urlparse(base_url).netloc.lower()
        discovered: list[tuple[str, str]] = []
        seen: set[str] = set()

        for anchor in soup.find_all("a", href=True):
            href = (anchor.get("href") or "").strip()
            if not href:
                continue
            if href.startswith(("mailto:", "tel:", "javascript:", "#")):
                continue

            absolute = self._normalize_url(urljoin(base_url, href))
            parsed = urlparse(absolute)
            if parsed.scheme not in ("http", "https"):
                continue

            if parsed.netloc.lower() != base_host:
                continue

            if absolute in seen:
                continue

            seen.add(absolute)
            anchor_text = anchor.get_text(separator=" ", strip=True)
            discovered.append((absolute, anchor_text))

        return discovered

    def _prioritize_links(self, links: list[tuple[str, str]]) -> list[tuple[str, str]]:
        """Sort links by likely usefulness for extraction."""
        def score(item: tuple[str, str]) -> int:
            url, text = item
            category = self.classify_page(url=url, anchor_text=text)
            if category in {"admission", "pricing", "contact"}:
                return 3
            if category in {"about", "gallery"}:
                return 2
            return 1

        return sorted(links, key=score, reverse=True)

    def _extract_text_content(self, soup: BeautifulSoup) -> str:
        """Extract bounded plain-text content cache for downstream extraction."""
        text = soup.get_text(separator="\n", strip=True)
        # PostgreSQL text cannot contain null bytes.
        text = text.replace("\x00", "")
        # Keep bounded payload in DB.
        return text[:12000]

    def _normalize_url(self, url: str) -> str:
        cleaned, _ = urldefrag(url)
        parsed = urlparse(cleaned)
        filtered_query_params = [
            (key, value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if not key.lower().startswith("utm_") and key.lower() not in self.TRACKING_QUERY_PARAMS
        ]
        canonical_query = urlencode(sorted(filtered_query_params))
        return parsed._replace(query=canonical_query, params="", fragment="").geturl().rstrip("/")

    def _should_fetch(self, url: str) -> bool:
        lower_url = url.lower()
        return not any(lower_url.endswith(ext) for ext in self.NON_HTML_EXTENSIONS)


async def navigate_school(db: AsyncSession, school_id: int, country_code: str = "bg") -> dict:
    """Run Stage 3 navigation for one school and persist discovered pages."""
    result = await db.execute(select(School).where(School.id == school_id))
    school = result.scalar_one_or_none()

    if not school:
        return {"school_id": school_id, "success": False, "reason": "School not found"}
    if not school.website_url:
        return {"school_id": school_id, "success": False, "reason": "No website URL"}

    validator = URLValidator(country_code=country_code)
    normalized_url = validator.normalize_url(school.website_url)
    if not normalized_url:
        return {"school_id": school_id, "success": False, "reason": "Invalid website URL format"}

    # Persist normalized URL so downstream stages never see scheme-less domains.
    if normalized_url != school.website_url:
        school.website_url = normalized_url
        await db.commit()

    navigator = WebsiteNavigator(country_code=country_code)
    final_url: str | None = None
    pages: list[NavigatedPage] = []
    last_exc: httpx.HTTPError | None = None
    for attempt in range(1, navigator.HTTP_ATTEMPTS + 1):
        try:
            final_url, pages = await navigator.discover_pages(normalized_url)
            last_exc = None
            break
        except httpx.HTTPError as exc:
            last_exc = exc
            detail = str(exc).strip() or "<empty>"
            logger.warning(
                "Navigation attempt %s/%s failed for school %s (%s): %s: %s",
                attempt,
                navigator.HTTP_ATTEMPTS,
                school_id,
                normalized_url,
                type(exc).__name__,
                detail,
            )

    if last_exc is not None:
        detail = str(last_exc).strip() or "<empty>"
        return {
            "school_id": school_id,
            "success": False,
            "reason": (
                f"Navigation HTTP error ({type(last_exc).__name__}) for "
                f"{normalized_url}: {detail}"
            ),
        }

    now = datetime.now(timezone.utc)
    created = 0
    updated = 0

    for page in pages:
        existing = await db.execute(
            select(SourcePage).where(
                SourcePage.school_id == school_id,
                SourcePage.scrape_type == ScrapeType.WEBSITE,
                SourcePage.source_url == page.url,
            )
        )
        source_page = existing.scalar_one_or_none()

        if source_page:
            changed = source_page.content_hash != page.content_hash
            if page.category is not None:
                source_page.page_category = page.category
            if page.markdown is not None:
                source_page.raw_markdown = page.markdown
            source_page.is_valid = True
            source_page.last_scraped_at = now
            source_page.scrape_count = (source_page.scrape_count or 0) + 1
            if changed:
                source_page.content_hash = page.content_hash
                source_page.last_changed_at = now
            updated += 1
        else:
            db.add(
                SourcePage(
                    school_id=school_id,
                    scrape_type=ScrapeType.WEBSITE,
                    source_url=page.url,
                    content_hash=page.content_hash,
                    page_category=page.category,
                    raw_markdown=page.markdown,
                    is_valid=True,
                    last_scraped_at=now,
                    last_changed_at=now,
                    scrape_count=1,
                )
            )
            created += 1

    if final_url and final_url != school.website_url:
        school.website_url = final_url

    if pages:
        school.scrape_status = "navigated"

    await db.commit()

    logger.info(
        "Navigated school %s: pages=%s created=%s updated=%s",
        school_id,
        len(pages),
        created,
        updated,
    )

    return {
        "school_id": school_id,
        "success": True,
        "pages_found": len(pages),
        "created": created,
        "updated": updated,
        "final_url": final_url,
    }
