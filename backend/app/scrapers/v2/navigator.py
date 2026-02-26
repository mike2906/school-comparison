"""Stage 4 navigation (v2): Crawl4AI-first implementation.

This module keeps the storage contract compatible with v1 while relying on
Crawl4AI deep crawling primitives for URL discovery and content collection.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from math import ceil
from typing import Any
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import School, ScrapeType, SourcePage
from app.scrapers.base import BaseScraper
from app.scrapers.url_validator import URLValidator

logger = logging.getLogger(__name__)


@dataclass
class NavigatedPageV2:
    """In-memory representation of a crawled page."""

    url: str
    category: str | None
    markdown: str | None
    content_hash: str
    cache_status: str | None = None
    head_fingerprint: str | None = None


@dataclass
class BatchDiscoverOutcomeV2:
    """Batch crawl outcome for one seed website URL."""

    seed_url: str
    final_url: str | None
    pages: list[NavigatedPageV2]
    error: str | None = None


class WebsiteNavigatorV2:
    """Crawl4AI deep crawler wrapper for school websites."""

    MAX_PAGES = 8
    MAX_DEPTH = 1
    PAGE_TIMEOUT_SECONDS = 30.0
    CRAWL_TIMEOUT_SECONDS = 120.0
    MAX_CONTENT_CHARS = 15000
    RETRYABLE_CRAWL_ERROR_MARKERS = (
        "timeout",
        "timed out",
        "net::err",
        "captcha",
        "cloudflare",
        "blocked",
        "access denied",
        "too many requests",
        "429",
        "403",
    )

    BOT_CHALLENGE_PATH_PREFIXES = (
        "/.well-known/sgcaptcha",
        "/.well-known/captcha",
        "/cdn-cgi/challenge",
        "/cdn-cgi/l/chk_jschl",
    )
    BOT_PROTECTION_TEXT_MARKERS = (
        "checking the site connection security",
        "ddos protection by sucuri",
        "checking your browser before accessing",
        "verify you are human",
        "enable javascript and cookies to continue",
        "cloudflare ray id",
    )

    CATEGORY_KEYWORDS = {
        "about": ["about", "za-nas", "за-нас", "мисия", "екип", "team"],
        "pricing": ["price", "prices", "pricing", "fees", "tuition", "ceni", "taksi", "цен", "такс"],
        "admission": ["admission", "apply", "enroll", "priem", "прием", "кандидат", "запис"],
        "contact": ["contact", "contacts", "kontakti", "контакт", "телефон", "адрес", "address", "phone"],
        "programs": ["program", "curriculum", "courses", "programi", "обуч", "програм"],
        "facilities": ["facility", "campus", "baza", "база", "кампус", "infrastructure"],
    }

    PRIORITY_KEYWORDS = [
        "about",
        "za-nas",
        "мисия",
        "team",
        "pricing",
        "prices",
        "fee",
        "fees",
        "ceni",
        "taksi",
        "прием",
        "admission",
        "apply",
        "enroll",
        "contact",
        "контакт",
        "kontakti",
        "program",
        "програм",
        "обуч",
        "facility",
        "база",
        "кампус",
    ]

    EXCLUDE_PATTERNS = [
        "*/category/*",
        "*/tag/*",
        "*/news/*",
        "*/blog/*",
        "*/posts/*",
        "*/events/*",
        "*/gallery/*",
        "*/photo*",
        "*/sitemap*",
        "*/search*",
        "*/wp-content/*",
        "*/wp-admin/*",
        "*/feed*",
        "*/rss*",
        "*.jpg",
        "*.jpeg",
        "*.png",
        "*.gif",
        "*.svg",
        "*.webp",
        "*.css",
        "*.js",
        "*.pdf",
        "*.zip",
        "*.mp4",
        "*.mp3",
    ]

    def __init__(self, country_code: str = "bg"):
        self.country_code = country_code
        self.settings = get_settings()

    def _build_browser_config(self, *, enable_stealth: bool = True) -> Any:
        from crawl4ai import BrowserConfig  # type: ignore

        return BrowserConfig(
            headless=True,
            verbose=False,
            ignore_https_errors=True,
            enable_stealth=enable_stealth,
            user_agent="Mozilla/5.0 (compatible; SchoolScraperV2/1.0; +https://kg.sofia.bg)",
        )

    def _build_run_config(self, website_url: str) -> Any:
        from crawl4ai import BestFirstCrawlingStrategy, CacheMode, CrawlerRunConfig  # type: ignore
        from crawl4ai.content_filter_strategy import PruningContentFilter  # type: ignore
        from crawl4ai.deep_crawling.filters import DomainFilter, FilterChain, URLPatternFilter  # type: ignore
        from crawl4ai.deep_crawling.scorers import KeywordRelevanceScorer  # type: ignore
        from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator  # type: ignore

        parsed = urlparse(website_url)
        domain = parsed.netloc

        filter_chain = FilterChain(
            filters=[
                DomainFilter(allowed_domains=[domain]),
                URLPatternFilter(patterns=self.EXCLUDE_PATTERNS, reverse=True),
            ]
        )
        scorer = KeywordRelevanceScorer(keywords=self.PRIORITY_KEYWORDS, weight=1.0, case_sensitive=False)
        strategy = BestFirstCrawlingStrategy(
            max_depth=self.MAX_DEPTH,
            max_pages=self.MAX_PAGES,
            filter_chain=filter_chain,
            url_scorer=scorer,
        )

        markdown_generator = DefaultMarkdownGenerator(
            content_filter=PruningContentFilter(threshold=0.45)
        )

        return CrawlerRunConfig(
            cache_mode=CacheMode.ENABLED,
            check_cache_freshness=True,
            cache_validation_timeout=8.0,
            page_timeout=int(self.PAGE_TIMEOUT_SECONDS * 1000),
            wait_until="domcontentloaded",
            delay_before_return_html=0.2,
            markdown_generator=markdown_generator,
            deep_crawl_strategy=strategy,
            score_links=True,
            exclude_external_links=True,
            verbose=False,
        )

    def _normalize_url(self, url: str) -> str:
        validator = URLValidator(country_code=self.country_code)
        normalized = validator.normalize_url(url)
        return normalized or url

    def _clean_text(self, raw: str) -> str:
        text = re.sub(r"\s+", " ", (raw or "")).strip()
        return text

    def _dedupe_lines(self, lines: list[str]) -> list[str]:
        seen: set[str] = set()
        deduped: list[str] = []
        for line in lines:
            normalized = self._clean_text(line)
            if not normalized:
                continue
            key = normalized.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(normalized)
        return deduped

    def _extract_markdown(self, crawl_result: Any) -> str | None:
        markdown_obj = getattr(crawl_result, "markdown", None)
        candidates: list[str] = []

        if isinstance(markdown_obj, str) and markdown_obj.strip():
            candidates.append(markdown_obj.strip())
        elif markdown_obj is not None:
            for attr in ("fit_markdown", "raw_markdown"):
                value = getattr(markdown_obj, attr, None)
                if isinstance(value, str) and value.strip():
                    candidates.append(value.strip())

        if not candidates:
            html = getattr(crawl_result, "cleaned_html", None) or getattr(crawl_result, "html", None)
            if not isinstance(html, str) or not html.strip():
                return None
            soup = BeautifulSoup(html, "html.parser")
            for node in soup.find_all(["script", "style", "noscript", "header", "footer", "nav", "aside"]):
                node.decompose()
            text = soup.get_text("\n", strip=True)
            lines = self._dedupe_lines(text.splitlines())
            extracted = "\n".join(lines)
            return extracted[: self.MAX_CONTENT_CHARS] if extracted else None

        lines = self._dedupe_lines(candidates[0].splitlines())
        extracted = "\n".join(lines)
        return extracted[: self.MAX_CONTENT_CHARS] if extracted else None

    def classify_page(self, url: str, title: str = "", anchor_text: str = "") -> str | None:
        haystack = f"{url} {title} {anchor_text}".lower()
        for category, keywords in self.CATEGORY_KEYWORDS.items():
            if any(keyword in haystack for keyword in keywords):
                return category
        return None

    def _is_bot_challenge_url(self, url: str) -> bool:
        path = (urlparse(url).path or "").lower()
        return any(path.startswith(prefix) for prefix in self.BOT_CHALLENGE_PATH_PREFIXES)

    def _is_bot_protection_content(self, text: str | None) -> bool:
        lowered = (text or "").lower()
        return any(marker in lowered for marker in self.BOT_PROTECTION_TEXT_MARKERS)

    def _is_extractable_page_content(self, page: NavigatedPageV2) -> bool:
        text = (page.markdown or "").strip()
        if not text:
            return False
        if self._is_bot_challenge_url(page.url):
            return False
        if self._is_bot_protection_content(text):
            return False
        return True

    def _host_key(self, url: str) -> str:
        netloc = (urlparse(url).netloc or "").lower()
        if "@" in netloc:
            netloc = netloc.rsplit("@", 1)[-1]
        host = netloc.split(":", 1)[0]
        return host[4:] if host.startswith("www.") else host

    def _is_same_site_url(self, url: str, canonical_site_url: str) -> bool:
        host = self._host_key(url)
        canonical_host = self._host_key(canonical_site_url)
        if not host or not canonical_host:
            return False
        if host == canonical_host:
            return True
        return host.endswith(f".{canonical_host}") or canonical_host.endswith(f".{host}")

    def _dedupe_pages(self, pages: list[NavigatedPageV2]) -> list[NavigatedPageV2]:
        by_url: dict[str, NavigatedPageV2] = {}
        ordered: list[str] = []
        for page in pages:
            if page.url not in by_url:
                by_url[page.url] = page
                ordered.append(page.url)
                continue
            current = by_url[page.url]
            current_score = (1 if (current.markdown or "").strip() else 0, len(current.markdown or ""))
            candidate_score = (1 if (page.markdown or "").strip() else 0, len(page.markdown or ""))
            by_url[page.url] = page if candidate_score > current_score else current
        return [by_url[url] for url in ordered]

    def _iter_results(self, results_obj: Any) -> list[Any]:
        if isinstance(results_obj, (list, tuple)):
            return list(results_obj)
        try:
            return list(results_obj)
        except TypeError:
            return [results_obj]

    def _collect_failure_messages(self, results_obj: Any) -> list[str]:
        messages: list[str] = []
        for result in self._iter_results(results_obj):
            if getattr(result, "success", False):
                continue
            error_message = getattr(result, "error_message", None)
            if error_message:
                messages.append(str(error_message))
        return messages

    def _should_retry_with_undetected(self, failure_messages: list[str]) -> bool:
        for message in failure_messages:
            lowered = message.lower()
            if any(marker in lowered for marker in self.RETRYABLE_CRAWL_ERROR_MARKERS):
                return True
        return False

    def _build_undetected_crawler(self, browser_config: Any) -> Any | None:
        from crawl4ai import AsyncWebCrawler, UndetectedAdapter  # type: ignore
        from crawl4ai.async_crawler_strategy import AsyncPlaywrightCrawlerStrategy  # type: ignore

        strategy = AsyncPlaywrightCrawlerStrategy(
            browser_config=browser_config,
            browser_adapter=UndetectedAdapter(),
        )
        return AsyncWebCrawler(config=browser_config, crawler_strategy=strategy)

    async def discover_pages(self, website_url: str) -> tuple[str, list[NavigatedPageV2]]:
        from crawl4ai import AsyncWebCrawler  # type: ignore

        normalized_url = self._normalize_url(website_url)
        browser_config = self._build_browser_config(enable_stealth=True)
        run_config = self._build_run_config(normalized_url)

        async with AsyncWebCrawler(config=browser_config) as crawler:
            results_obj = await asyncio.wait_for(
                crawler.arun(url=normalized_url, config=run_config),
                timeout=self.CRAWL_TIMEOUT_SECONDS,
            )

        final_url, pages = self._extract_pages_from_results(
            normalized_url=normalized_url,
            results_obj=results_obj,
        )
        if any(self._is_extractable_page_content(page) for page in pages):
            return final_url, pages

        failure_messages = self._collect_failure_messages(results_obj)
        if not self._should_retry_with_undetected(failure_messages):
            return final_url, pages

        try:
            undetected_config = self._build_browser_config(enable_stealth=False)
            undetected_crawler = self._build_undetected_crawler(undetected_config)
            if undetected_crawler is None:
                return final_url, pages
            async with undetected_crawler as crawler:
                retry_results = await asyncio.wait_for(
                    crawler.arun(url=normalized_url, config=run_config),
                    timeout=self.CRAWL_TIMEOUT_SECONDS,
                )
            retry_final_url, retry_pages = self._extract_pages_from_results(
                normalized_url=normalized_url,
                results_obj=retry_results,
            )
            if retry_pages:
                return retry_final_url, self._dedupe_pages(pages + retry_pages)
        except Exception as exc:
            logger.warning("V2 undetected retry failed for %s: %s", normalized_url, exc)

        return final_url, pages

    def _extract_pages_from_results(
        self,
        *,
        normalized_url: str,
        results_obj: Any,
    ) -> tuple[str, list[NavigatedPageV2]]:
        if isinstance(results_obj, (list, tuple)):
            results = list(results_obj)
        else:
            try:
                results = list(results_obj)
            except TypeError:
                results = [results_obj]

        pages: list[NavigatedPageV2] = []
        final_url = normalized_url

        for result in results:
            if not getattr(result, "success", False):
                continue

            raw_url = getattr(result, "redirected_url", None) or getattr(result, "url", None) or normalized_url
            storage_url = self._normalize_url(str(raw_url))
            if not pages:
                final_url = storage_url

            markdown = self._extract_markdown(result)
            title = ""
            metadata = getattr(result, "metadata", None)
            if isinstance(metadata, dict):
                title = str(metadata.get("title", "") or "")

            head_fingerprint = getattr(result, "head_fingerprint", None)
            cache_status = getattr(result, "cache_status", None)
            hash_seed = head_fingerprint or markdown or storage_url
            pages.append(
                NavigatedPageV2(
                    url=storage_url,
                    category=self.classify_page(storage_url, title=title),
                    markdown=markdown if (markdown or "").strip() else None,
                    content_hash=BaseScraper.compute_hash(str(hash_seed)),
                    cache_status=str(cache_status) if cache_status is not None else None,
                    head_fingerprint=str(head_fingerprint) if head_fingerprint else None,
                )
            )

        return final_url, self._dedupe_pages(pages)

    async def discover_pages_many(
        self,
        website_urls: list[str],
        *,
        max_concurrency: int = 3,
    ) -> dict[str, BatchDiscoverOutcomeV2]:
        """Discover pages for many school websites using Crawl4AI arun_many."""
        from crawl4ai import AsyncWebCrawler, RateLimiter, SemaphoreDispatcher  # type: ignore

        if not website_urls:
            return {}

        normalized_urls: list[str] = []
        seen: set[str] = set()
        for url in website_urls:
            normalized = self._normalize_url(url)
            if not normalized or normalized in seen:
                continue
            normalized_urls.append(normalized)
            seen.add(normalized)

        if not normalized_urls:
            return {}

        browser_config = self._build_browser_config(enable_stealth=True)
        run_configs = [self._build_run_config(url) for url in normalized_urls]
        concurrency = max(1, int(max_concurrency))
        timeout_budget = self.CRAWL_TIMEOUT_SECONDS * max(1, ceil(len(normalized_urls) / concurrency))
        rate_limiter = RateLimiter(
            base_delay=(0.2, 0.8),
            max_delay=6.0,
            max_retries=1,
            rate_limit_codes=[429, 503],
        )
        dispatcher = SemaphoreDispatcher(
            semaphore_count=concurrency,
            rate_limiter=rate_limiter,
        )

        outcomes: dict[str, BatchDiscoverOutcomeV2] = {}
        try:
            async with AsyncWebCrawler(config=browser_config) as crawler:
                containers = await asyncio.wait_for(
                    crawler.arun_many(
                        urls=normalized_urls,
                        config=run_configs,
                        dispatcher=dispatcher,
                    ),
                    timeout=timeout_budget,
                )
        except Exception as exc:
            logger.warning("V2 arun_many failed; falling back to sequential discover_pages: %s", exc)
            for url in normalized_urls:
                try:
                    final_url, pages = await self.discover_pages(url)
                    outcomes[url] = BatchDiscoverOutcomeV2(
                        seed_url=url,
                        final_url=final_url,
                        pages=pages,
                    )
                except Exception as school_exc:
                    outcomes[url] = BatchDiscoverOutcomeV2(
                        seed_url=url,
                        final_url=None,
                        pages=[],
                        error=str(school_exc),
                    )
            return outcomes

        if not isinstance(containers, list):
            containers = [containers]

        for idx, url in enumerate(normalized_urls):
            container = containers[idx] if idx < len(containers) else None
            if container is None:
                outcomes[url] = BatchDiscoverOutcomeV2(
                    seed_url=url,
                    final_url=None,
                    pages=[],
                    error="Missing crawl result container",
                )
                continue

            error_message = getattr(container, "error_message", None)
            try:
                final_url, pages = self._extract_pages_from_results(
                    normalized_url=url,
                    results_obj=container,
                )
                outcomes[url] = BatchDiscoverOutcomeV2(
                    seed_url=url,
                    final_url=final_url,
                    pages=pages,
                    error=str(error_message) if error_message else None,
                )
            except Exception as exc:
                outcomes[url] = BatchDiscoverOutcomeV2(
                    seed_url=url,
                    final_url=None,
                    pages=[],
                    error=str(exc),
                )

        return outcomes


async def _persist_navigation_result_v2(
    db: AsyncSession,
    school: School,
    *,
    normalized_url: str,
    final_url: str | None,
    pages: list[NavigatedPageV2],
    validator: URLValidator,
    navigator: WebsiteNavigatorV2,
) -> dict[str, Any]:
    if final_url:
        final_url = validator._canonicalize_bot_protection_final_url(
            normalized_url=normalized_url,
            final_url=final_url,
        )

    school_id = school.id
    now = datetime.now(timezone.utc)
    created = 0
    updated = 0
    contentful_pages = 0
    cache_hits = 0

    for page in pages:
        has_content = navigator._is_extractable_page_content(page)
        if has_content:
            contentful_pages += 1
        if (page.cache_status or "").startswith("hit"):
            cache_hits += 1

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
            source_page.raw_markdown = page.markdown if has_content else None
            source_page.is_valid = has_content
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
                    raw_markdown=page.markdown if has_content else None,
                    is_valid=has_content,
                    last_scraped_at=now,
                    last_changed_at=now,
                    scrape_count=1,
                )
            )
            created += 1

    existing_pages_result = await db.execute(
        select(SourcePage).where(
            SourcePage.school_id == school_id,
            SourcePage.scrape_type == ScrapeType.WEBSITE,
        )
    )
    existing_pages = existing_pages_result.scalars().all()
    canonical_site_url = final_url or normalized_url

    invalidated = 0
    for existing_page in existing_pages:
        text = (existing_page.raw_markdown or "").strip()
        if not text:
            if existing_page.is_valid:
                existing_page.is_valid = False
                invalidated += 1
            continue
        if not navigator._is_same_site_url(existing_page.source_url, canonical_site_url):
            existing_page.is_valid = False
            existing_page.raw_markdown = None
            invalidated += 1
            continue
        if navigator._is_bot_challenge_url(existing_page.source_url) or navigator._is_bot_protection_content(text):
            existing_page.is_valid = False
            existing_page.raw_markdown = None
            invalidated += 1

    if final_url and final_url != school.website_url:
        school.website_url = final_url

    if contentful_pages > 0:
        school.scrape_status = "navigated"

    await db.commit()

    if contentful_pages == 0:
        return {
            "school_id": school_id,
            "success": False,
            "reason": "No extractable page content",
            "pages_found": len(pages),
            "pages_with_content": 0,
            "created": created,
            "updated": updated,
            "cache_hits": cache_hits,
            "invalidated": invalidated,
            "final_url": final_url,
        }

    return {
        "school_id": school_id,
        "success": True,
        "pages_found": len(pages),
        "pages_with_content": contentful_pages,
        "created": created,
        "updated": updated,
        "cache_hits": cache_hits,
        "invalidated": invalidated,
        "final_url": final_url,
    }


async def navigate_school_v2(db: AsyncSession, school_id: int, country_code: str = "bg") -> dict[str, Any]:
    """Run Stage 4 navigation (v2) and persist source pages."""
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

    if normalized_url != school.website_url:
        school.website_url = normalized_url
        await db.commit()

    navigator = WebsiteNavigatorV2(country_code=country_code)
    try:
        final_url, pages = await navigator.discover_pages(normalized_url)
    except Exception as exc:
        logger.error("V2 navigation failed for school %s: %s", school_id, exc)
        return {"school_id": school_id, "success": False, "reason": f"Navigation failed: {exc}"}

    return await _persist_navigation_result_v2(
        db=db,
        school=school,
        normalized_url=normalized_url,
        final_url=final_url,
        pages=pages,
        validator=validator,
        navigator=navigator,
    )


async def navigate_schools_v2_batch(
    db: AsyncSession,
    school_ids: list[int],
    *,
    country_code: str = "bg",
    max_concurrency: int = 3,
) -> list[dict[str, Any]]:
    """Run Stage 4 navigation (v2) for many schools using Crawl4AI arun_many."""
    if not school_ids:
        return []

    schools_result = await db.execute(select(School).where(School.id.in_(school_ids)))
    schools = schools_result.scalars().all()
    by_id = {school.id: school for school in schools}

    validator = URLValidator(country_code=country_code)
    navigator = WebsiteNavigatorV2(country_code=country_code)

    normalized_by_school: dict[int, str] = {}
    failures_by_school: dict[int, str] = {}
    batch_urls: list[str] = []

    for school_id in school_ids:
        school = by_id.get(school_id)
        if school is None:
            failures_by_school[school_id] = "School not found"
            continue
        if not school.website_url:
            failures_by_school[school_id] = "No website URL"
            continue
        normalized_url = validator.normalize_url(school.website_url)
        if not normalized_url:
            failures_by_school[school_id] = "Invalid website URL format"
            continue
        normalized_by_school[school_id] = normalized_url
        batch_urls.append(normalized_url)
        if normalized_url != school.website_url:
            school.website_url = normalized_url

    if batch_urls:
        await db.commit()

    outcomes = await navigator.discover_pages_many(batch_urls, max_concurrency=max_concurrency)
    results: list[dict[str, Any]] = []

    for school_id in school_ids:
        if school_id in failures_by_school:
            results.append(
                {
                    "school_id": school_id,
                    "success": False,
                    "reason": failures_by_school[school_id],
                }
            )
            continue

        school = by_id[school_id]
        normalized_url = normalized_by_school[school_id]
        outcome = outcomes.get(normalized_url)
        if outcome is None:
            results.append(
                {
                    "school_id": school_id,
                    "success": False,
                    "reason": "Navigation failed: Missing batch outcome",
                }
            )
            continue
        if outcome.error and not outcome.pages:
            try:
                retry_final_url, retry_pages = await navigator.discover_pages(normalized_url)
                outcome = BatchDiscoverOutcomeV2(
                    seed_url=normalized_url,
                    final_url=retry_final_url,
                    pages=retry_pages,
                    error=outcome.error if not retry_pages else None,
                )
            except Exception as retry_exc:
                results.append(
                    {
                        "school_id": school_id,
                        "success": False,
                        "reason": f"Navigation failed: {outcome.error} (retry failed: {retry_exc})",
                    }
                )
                continue
            if outcome.error and not outcome.pages:
                results.append(
                    {
                        "school_id": school_id,
                        "success": False,
                        "reason": f"Navigation failed: {outcome.error}",
                    }
                )
                continue

        result = await _persist_navigation_result_v2(
            db=db,
            school=school,
            normalized_url=normalized_url,
            final_url=outcome.final_url,
            pages=outcome.pages,
            validator=validator,
            navigator=navigator,
        )
        results.append(result)

    return results
