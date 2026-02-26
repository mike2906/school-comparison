"""Website navigation utilities for Stage 3 of the scraping pipeline.

This module keeps Stage 3 intentionally simple:
- fetch homepage
- discover same-domain links
- classify likely page category with heuristics
- cache discovered pages in `source_pages`
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urldefrag, unquote

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
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


class _DecodedKeywordURLScorer:
    """URLScorer that URL-decodes paths before keyword matching.

    crawl4ai's KeywordRelevanceScorer scores URLs as raw strings, which means
    Cyrillic paths encoded as %D0%B7%D0%B0-%D0%B0%D0%BA%D0%B0%D0%B4%D0%B5%D0%BC%D0%B8%D1%8F
    won't match keywords like 'za-akademia'. This scorer decodes the URL first,
    enabling proper matching of both ASCII transliterated paths AND Cyrillic paths.

    This class duck-types crawl4ai.deep_crawling.scorers.URLScorer so it can be passed
    as the `url_scorer` argument of BestFirstCrawlingStrategy.
    """

    def __init__(self, keywords: list[str], weight: float = 1.0) -> None:
        self._keywords = [kw.lower() for kw in keywords]
        self._weight = weight

    def _calculate_score(self, url: str) -> float:
        try:
            decoded = unquote(url).lower()
        except Exception:
            decoded = url.lower()
        matched = sum(1 for kw in self._keywords if kw in decoded)
        return min(1.0, matched / max(1, len(self._keywords)))

    def score(self, url: str) -> float:
        return self._calculate_score(url) * self._weight

    @property
    def weight(self) -> float:
        return self._weight

    @property
    def stats(self) -> object:
        return None


class WebsiteNavigator:
    """Simple website navigator based on HTML link discovery."""

    HTTP_TIMEOUT = 10.0
    HTTP_ATTEMPTS = 2
    MAX_LINKS = 20
    MAX_PAGES = 8
    # Hard cap on a single crawl4ai browser call (home page or subpage).
    # Playwright's page_timeout doesn't always abort cleanly; this asyncio-level
    # deadline guarantees the call returns even if the browser hangs.
    CRAWL4AI_PAGE_HARD_TIMEOUT = 25.0
    # Hard cap for a full deep-crawl run (multiple pages via BestFirstCrawlingStrategy).
    CRAWL4AI_DEEP_HARD_TIMEOUT = 120.0
    # Challenge retry settings: Sucuri/Cloudflare pages often need extra delay for JS/cookies.
    CRAWL4AI_CHALLENGE_RETRY_DELAY_SECONDS = 12.0
    CRAWL4AI_CHALLENGE_RETRY_PAGE_TIMEOUT_SECONDS = 45.0
    CRAWL4AI_CHALLENGE_RETRY_HARD_TIMEOUT = 75.0
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
        "programs": [
            "program",
            "programs",
            "programme",
            "curriculum",
            "courses",
            "course",
            "kursove",
            "програм",
            "обучен",
            "учебен",
            "курсове",
            "дейност",
            "extracurricular",
            "activities",
            "занимания",
            "клуб",
            "извънклас",
        ],
        "facilities": [
            "facility",
            "facilities",
            "campus",
            "infrastructure",
            "база",
            "кампус",
            "инфраструктур",
            "сграда",
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
    NOISE_TAGS = {
        "script",
        "style",
        "noscript",
        "svg",
        "iframe",
        "canvas",
        "form",
        "button",
        "input",
        "select",
        "textarea",
        "header",
        "footer",
        "nav",
        "aside",
    }
    MAX_CONTENT_CHARS = 12000

    # URL path segments that indicate low-value pages (news, gallery, pagination, etc.)
    # ASCII only - Cyrillic won't match URL-encoded paths like %d0%b3%d0%b0%d0%bb%d0%b5%d1%80%d0%b8%d1%8f
    EXCLUDE_PATH_SEGMENTS = {
        "category", "tag", "tags", "news", "blog", "post", "posts",
        "events", "event", "gallery", "galeria", "photo", "photos",
        "sitemap", "search", "cart", "shop", "login", "register", "page",
        "wp-content", "wp-admin", "wp-login",
        "feed", "rss", "atom",
    }

    # URL path keywords for BestFirst scoring (ASCII only — Cyrillic paths are URL-encoded
    # and won't match; transliterated BG paths like /za-nas/, /ceni/ are handled here)
    PRIORITY_PATH_KEYWORDS = (
        # English
        "about", "mission", "team", "staff", "faculty",
        "price", "prices", "pricing", "fee", "fees", "tuition",
        "contact", "contacts", "location",
        "admission", "admissions", "apply", "enroll",
        "program", "programs", "curriculum", "courses", "course",
        "facility", "facilities", "campus",
        # Bulgarian transliterated (ASCII) — these DO appear in URLs
        "za-nas", "za-uchilishte", "za-detska", "za-uchilishteto", "za-gradinata",
        "za-akademia", "za-shkola",
        "ceni", "taksi", "taksa",
        "kontakt", "kontakti",
        "priem", "zapisvane", "kandidatvane",
        "programa", "programi", "obuchenie",
        "baza", "kampus", "infrastruktura",
        "kursove",
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

    def __init__(self, country_code: str = "bg"):
        self.country_code = country_code
        configured_extractor = (get_settings().nav_content_extractor or "bs4").strip().lower()
        if configured_extractor not in {"bs4", "trafilatura", "crawl4ai"}:
            logger.warning(
                "Unknown NAV_CONTENT_EXTRACTOR='%s'; defaulting to bs4",
                configured_extractor,
            )
            configured_extractor = "bs4"
        self.content_extractor = configured_extractor
        configured_fetch_engine = (get_settings().nav_fetch_engine or "httpx").strip().lower()
        if configured_fetch_engine not in {"httpx", "crawl4ai", "crawl4ai_deep"}:
            logger.warning(
                "Unknown NAV_FETCH_ENGINE='%s'; defaulting to httpx",
                configured_fetch_engine,
            )
            configured_fetch_engine = "httpx"
        # TODO: For MVP, consider standardizing on a single fetch engine/extractor and
        # moving alternate paths to experimental scripts.
        self.fetch_engine = configured_fetch_engine
        # Internal flag used for 2nd fallback retries on bot-protected sites.
        self.crawl4ai_use_undetected_stealth = False

    def _clone_for_engine(self, fetch_engine: str, *, use_undetected_stealth: bool = False) -> "WebsiteNavigator":
        clone = WebsiteNavigator.__new__(WebsiteNavigator)
        clone.country_code = self.country_code
        clone.content_extractor = self.content_extractor
        clone.fetch_engine = fetch_engine
        clone.crawl4ai_use_undetected_stealth = use_undetected_stealth
        return clone

    def _build_crawl4ai_browser_config(self):
        from crawl4ai import BrowserConfig  # type: ignore

        use_combo = bool(getattr(self, "crawl4ai_use_undetected_stealth", False))
        return BrowserConfig(
            # "Combining both features" recommendation: undetected + stealth with headed browser.
            headless=not use_combo,
            verbose=False,
            user_agent=self.REQUEST_HEADERS.get("User-Agent"),
            ignore_https_errors=True,
            enable_stealth=use_combo,
        )

    def _create_crawl4ai_webcrawler(self, browser_config):
        from crawl4ai import AsyncWebCrawler  # type: ignore

        use_combo = bool(getattr(self, "crawl4ai_use_undetected_stealth", False))
        if not use_combo:
            return AsyncWebCrawler(config=browser_config)

        from crawl4ai import UndetectedAdapter  # type: ignore
        from crawl4ai.async_crawler_strategy import AsyncPlaywrightCrawlerStrategy  # type: ignore

        strategy = AsyncPlaywrightCrawlerStrategy(
            browser_config=browser_config,
            browser_adapter=UndetectedAdapter(),
        )
        return AsyncWebCrawler(config=browser_config, crawler_strategy=strategy)

    def _build_challenge_retry_run_config(self, *, deep_crawl_strategy=None, score_links: bool = False):
        from crawl4ai import CacheMode, CrawlerRunConfig  # type: ignore

        return CrawlerRunConfig(
            cache_mode=CacheMode.BYPASS,
            page_timeout=max(
                int(self.HTTP_TIMEOUT * 1000),
                int(self.CRAWL4AI_CHALLENGE_RETRY_PAGE_TIMEOUT_SECONDS * 1000),
            ),
            wait_until="domcontentloaded",
            # Let anti-bot JS/cookie checks settle before html/markdown extraction.
            delay_before_return_html=self.CRAWL4AI_CHALLENGE_RETRY_DELAY_SECONDS,
            wait_for="css:body",
            simulate_user=True,
            magic=True,
            verbose=False,
            deep_crawl_strategy=deep_crawl_strategy,
            score_links=score_links,
        )

    def _crawl_result_status_code(self, crawl_result: object) -> int | None:
        raw = getattr(crawl_result, "status_code", None)
        if raw is None:
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    def _looks_like_bot_challenge_payload(self, text: str | None) -> bool:
        lowered = (text or "").strip().lower()
        if not lowered:
            return False
        if any(marker in lowered for marker in self.BOT_PROTECTION_TEXT_MARKERS):
            return True
        if "robot challenge screen" in lowered:
            return True
        if "this page requires cookies to be enabled" in lowered:
            return True
        return False

    def _crawl_result_is_bot_challenge(self, crawl_result: object) -> bool:
        status_code = self._crawl_result_status_code(crawl_result)
        if status_code == 202:
            return True

        redirected = getattr(crawl_result, "redirected_url", None)
        if isinstance(redirected, str) and redirected and self._is_bot_challenge_url(redirected):
            return True

        final_url = getattr(crawl_result, "url", None)
        if isinstance(final_url, str) and final_url and self._is_bot_challenge_url(final_url):
            return True

        markdown = self._extract_markdown_from_crawl_result(crawl_result)
        if self._looks_like_bot_challenge_payload(markdown):
            return True

        html = self._extract_html_from_crawl_result(crawl_result)
        if not html:
            return False
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(separator=" ", strip=True)
        return self._looks_like_bot_challenge_payload(text)

    def _deep_results_need_challenge_retry(self, results: list[object]) -> bool:
        successful = [result for result in results if getattr(result, "success", False)]
        if not successful:
            return False
        return all(self._crawl_result_is_bot_challenge(result) for result in successful)

    async def discover_pages(self, website_url: str) -> tuple[str, list[NavigatedPage]]:
        """Discover and lightly classify pages for one school website.

        Returns:
            Tuple: (final_home_url_after_redirects, discovered_pages)
        """
        if self.fetch_engine == "crawl4ai_deep":
            try:
                return await self._discover_pages_with_crawl4ai_deep(website_url)
            except Exception as exc:
                logger.warning("crawl4ai_deep fetch engine failed, falling back to httpx: %s", exc)
        elif self.fetch_engine == "crawl4ai":
            try:
                return await self._discover_pages_with_crawl4ai(website_url)
            except Exception as exc:
                logger.warning("crawl4ai fetch engine failed, falling back to httpx: %s", exc)
        return await self._discover_pages_with_httpx(website_url)

    async def _discover_pages_with_httpx(self, website_url: str) -> tuple[str, list[NavigatedPage]]:
        """Discover pages with httpx fetches (default engine)."""
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
            home_markdown = self._extract_text_content(home_response.text, home_soup, source_url=final_home_url)
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
                                markdown = self._extract_text_content(
                                    page_response.text,
                                    soup,
                                    source_url=page_url,
                                )
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

    async def _discover_pages_with_crawl4ai(self, website_url: str) -> tuple[str, list[NavigatedPage]]:
        """Discover pages with crawl4ai AsyncWebCrawler fetches."""
        try:
            from crawl4ai import CacheMode, CrawlerRunConfig  # type: ignore
        except ImportError as exc:
            raise RuntimeError("crawl4ai is not installed") from exc

        browser_config = self._build_crawl4ai_browser_config()
        run_config = CrawlerRunConfig(
            cache_mode=CacheMode.BYPASS,
            page_timeout=int(self.HTTP_TIMEOUT * 1000),
            wait_until="domcontentloaded",
            verbose=False,
        )
        challenge_retry_run_config = self._build_challenge_retry_run_config()

        async with self._create_crawl4ai_webcrawler(browser_config) as crawler:
            try:
                home_result = await asyncio.wait_for(
                    crawler.arun(url=website_url, config=run_config),
                    timeout=self.CRAWL4AI_PAGE_HARD_TIMEOUT,
                )
            except asyncio.TimeoutError as exc:
                raise RuntimeError(
                    f"crawl4ai home fetch timed out after {self.CRAWL4AI_PAGE_HARD_TIMEOUT}s"
                ) from exc

            if getattr(home_result, "success", False) and self._crawl_result_is_bot_challenge(home_result):
                logger.info("crawl4ai home fetch returned bot challenge; retrying with challenge-wait settings")
                try:
                    home_result = await asyncio.wait_for(
                        crawler.arun(url=website_url, config=challenge_retry_run_config),
                        timeout=max(
                            self.CRAWL4AI_PAGE_HARD_TIMEOUT,
                            self.CRAWL4AI_CHALLENGE_RETRY_HARD_TIMEOUT,
                        ),
                    )
                except asyncio.TimeoutError as exc:
                    raise RuntimeError(
                        f"crawl4ai challenge-retry home fetch timed out after "
                        f"{self.CRAWL4AI_CHALLENGE_RETRY_HARD_TIMEOUT}s"
                    ) from exc

            if not getattr(home_result, "success", False):
                message = getattr(home_result, "error_message", None) or "crawl failed"
                raise RuntimeError(f"crawl4ai home fetch failed: {message}")

            final_home_url = self._normalize_url(
                getattr(home_result, "redirected_url", None)
                or getattr(home_result, "url", None)
                or website_url
            )
            home_html = self._extract_html_from_crawl_result(home_result)
            if not home_html:
                raise RuntimeError("crawl4ai home fetch returned empty HTML")

            pages: list[NavigatedPage] = []
            home_soup = BeautifulSoup(home_html, "html.parser")
            home_markdown = self._extract_text_from_crawl_result_or_html(
                crawl_result=home_result,
                html=home_html,
                soup=home_soup,
                source_url=final_home_url,
            )
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
                storage_url = page_url

                if self._should_fetch(page_url):
                    try:
                        page_result = await asyncio.wait_for(
                            crawler.arun(url=page_url, config=run_config),
                            timeout=self.CRAWL4AI_PAGE_HARD_TIMEOUT,
                        )
                        if getattr(page_result, "success", False):
                            status_code = getattr(page_result, "status_code", 200) or 200
                            if int(status_code) < 400:
                                fetched_url = (
                                    getattr(page_result, "redirected_url", None)
                                    or getattr(page_result, "url", None)
                                    or page_url
                                )
                                storage_url = self._normalize_url(str(fetched_url))

                                page_html = self._extract_html_from_crawl_result(page_result)
                                if page_html:
                                    soup = BeautifulSoup(page_html, "html.parser")
                                    markdown = self._extract_text_from_crawl_result_or_html(
                                        crawl_result=page_result,
                                        html=page_html,
                                        soup=soup,
                                        source_url=storage_url,
                                    )
                                    inferred = self.classify_page(
                                        storage_url,
                                        title=soup.title.string if soup.title and soup.title.string else "",
                                        anchor_text=anchor_text,
                                    )
                                    if inferred:
                                        category = inferred
                                    if markdown:
                                        content_hash_source = markdown
                    except Exception:
                        # Keep discovered link even if fetch failed; extraction can retry later.
                        pass

                pages.append(
                    NavigatedPage(
                        url=storage_url,
                        category=category,
                        markdown=markdown,
                        content_hash=BaseScraper.compute_hash(content_hash_source),
                    )
                )

            return final_home_url, pages

    async def _discover_pages_with_crawl4ai_deep(
        self, website_url: str
    ) -> tuple[str, list[NavigatedPage]]:
        """Discover pages using crawl4ai BestFirstCrawlingStrategy (browser-rendered, parallel).

        Advantages over sequential approaches:
        - Renders JavaScript (works on React/Vue sites that return empty with httpx)
        - Visits pages by relevance score (most useful pages first within budget)
        - Built-in per-page timeout prevents indefinite hangs
        - Domain-scoped to avoid off-site crawling
        - Filters out gallery/news/blog/category noise paths
        """
        try:
            from crawl4ai import CacheMode, CrawlerRunConfig  # type: ignore
            from crawl4ai import BestFirstCrawlingStrategy  # type: ignore
            from crawl4ai.deep_crawling.filters import DomainFilter, URLPatternFilter, FilterChain  # type: ignore
        except ImportError as exc:
            raise RuntimeError("crawl4ai is not installed") from exc

        parsed_home = urlparse(website_url)
        domain = parsed_home.netloc

        # Build exclude patterns for low-value URL path segments
        exclude_patterns = [
            f"*/{seg}/*" for seg in self.EXCLUDE_PATH_SEGMENTS
        ] + [
            f"*/{seg}" for seg in self.EXCLUDE_PATH_SEGMENTS
        ] + [
            # Skip pagination pages like /page/2/, /page/3/
            "*/page/[0-9]*",
            # Skip WordPress date-based blog post paths like /2020/04/10/post-title/
            "*/20[0-9][0-9]/[0-9][0-9]/*",
            "*/19[0-9][0-9]/[0-9][0-9]/*",
            # Skip media files
            "*.jpg", "*.jpeg", "*.png", "*.gif", "*.svg", "*.webp",
            "*.css", "*.js", "*.pdf", "*.zip", "*.mp4", "*.mp3",
        ]

        # Use our URL-decode-aware scorer so Cyrillic paths encoded as %d0%b7%d0%b0-...
        # still match keywords like 'za-nas', 'ceni', 'za-akademia', etc.
        # Also includes literal Cyrillic for sites that expose unencoded Cyrillic paths.
        scorer = _DecodedKeywordURLScorer(
            keywords=list(self.PRIORITY_PATH_KEYWORDS) + [
                # Cyrillic substrings (decoded path matching)
                "за-нас", "за ас", "мисия", "екип",
                "цен", "такс", "прием", "запис",
                "програм", "обучен", "курсове", "дейност",
                "контакт", "адрес", "за-градин", "за-учили",
                "инфраструктур", "база", "кампус",
            ],
            weight=1.0,
        )

        filter_chain = FilterChain(filters=[
            DomainFilter(allowed_domains=[domain]),
            URLPatternFilter(patterns=exclude_patterns, reverse=True),
        ])

        strategy = BestFirstCrawlingStrategy(
            max_depth=1,
            max_pages=self.MAX_PAGES + 2,  # Slight overrun budget in case some fail
            filter_chain=filter_chain,
            url_scorer=scorer,  # type: ignore[arg-type]
        )

        browser_config = self._build_crawl4ai_browser_config()
        run_config = CrawlerRunConfig(
            cache_mode=CacheMode.BYPASS,
            page_timeout=int(self.HTTP_TIMEOUT * 1000),
            wait_until="domcontentloaded",
            verbose=False,
            deep_crawl_strategy=strategy,
            score_links=True,
        )
        challenge_retry_run_config = self._build_challenge_retry_run_config(
            deep_crawl_strategy=strategy,
            score_links=True,
        )

        pages: list[NavigatedPage] = []
        final_home_url = self._normalize_url(website_url)

        async with self._create_crawl4ai_webcrawler(browser_config) as crawler:
            try:
                results = await asyncio.wait_for(
                    crawler.arun(url=website_url, config=run_config),
                    timeout=self.CRAWL4AI_DEEP_HARD_TIMEOUT,
                )
            except asyncio.TimeoutError as exc:
                raise RuntimeError(
                    f"crawl4ai_deep timed out after {self.CRAWL4AI_DEEP_HARD_TIMEOUT}s"
                ) from exc

            results = list(results)
            if self._deep_results_need_challenge_retry(results):
                logger.info("crawl4ai_deep returned bot-challenge pages; retrying with challenge-wait settings")
                try:
                    retried_results = await asyncio.wait_for(
                        crawler.arun(url=website_url, config=challenge_retry_run_config),
                        timeout=max(
                            self.CRAWL4AI_DEEP_HARD_TIMEOUT,
                            self.CRAWL4AI_CHALLENGE_RETRY_HARD_TIMEOUT,
                        ),
                    )
                    results = list(retried_results)
                except asyncio.TimeoutError as exc:
                    raise RuntimeError(
                        f"crawl4ai_deep challenge-retry timed out after "
                        f"{self.CRAWL4AI_CHALLENGE_RETRY_HARD_TIMEOUT}s"
                    ) from exc

            for result in results:
                if not getattr(result, "success", False):
                    continue

                result_url = (
                    getattr(result, "redirected_url", None)
                    or getattr(result, "url", None)
                    or website_url
                )
                storage_url = self._normalize_url(str(result_url))

                # Track the final home URL from the first (homepage) result
                if not pages:
                    final_home_url = storage_url

                # Prefer bs4 extraction on browser-rendered HTML:
                # - Strips nav/header/footer/aside noise elements
                # - Works well with the downstream extraction prompts
                # Fall back to cleaned raw_markdown if no HTML available.
                page_html = self._extract_html_from_crawl_result(result)
                markdown: str | None = None

                if page_html:
                    page_soup = BeautifulSoup(page_html, "html.parser")
                    markdown = self._extract_text_content_bs4(page_soup)
                else:
                    page_soup = None
                    md_obj = getattr(result, "markdown", None)
                    if md_obj is not None:
                        raw_md = (
                            getattr(md_obj, "raw_markdown", None)
                            if not isinstance(md_obj, str)
                            else md_obj
                        )
                        if raw_md and isinstance(raw_md, str) and raw_md.strip():
                            markdown = self._clean_crawl4ai_markdown(raw_md)
                title_text = ""
                if page_soup and page_soup.title and page_soup.title.string:
                    title_text = page_soup.title.string
                category = self.classify_page(storage_url, title=title_text, anchor_text="")

                content_hash_source = markdown if markdown else storage_url
                pages.append(
                    NavigatedPage(
                        url=storage_url,
                        category=category,
                        markdown=markdown if markdown and markdown.strip() else None,
                        content_hash=BaseScraper.compute_hash(content_hash_source),
                    )
                )

        return final_home_url, pages

    def _clean_crawl4ai_markdown(self, raw_md: str) -> str:
        """Clean crawl4ai raw_markdown: remove link URLs, dedupe lines, truncate."""
        # Remove markdown links but keep anchor text: [text](url) -> text
        cleaned = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", raw_md)
        # Remove bare URLs
        cleaned = re.sub(r"https?://\S+", "", cleaned)
        # Clean and dedupe lines
        lines = []
        for raw_line in cleaned.splitlines():
            line = self._clean_text(raw_line)
            if not line:
                continue
            if self._is_probable_nav_noise(line):
                continue
            lines.append(line)
        result = "\n".join(self._dedupe_lines(lines))
        result = result.replace("\x00", "")
        return result[: self.MAX_CONTENT_CHARS]

    def classify_page(self, url: str, title: str = "", anchor_text: str = "") -> str | None:
        """Heuristic page category classifier using URL/title/anchor tokens."""
        haystack = f"{url} {title} {anchor_text}".lower()
        for category, keywords in self.CATEGORY_KEYWORDS.items():
            if any(keyword in haystack for keyword in keywords):
                return category
        return None

    def _extract_internal_links(self, base_url: str, soup: BeautifulSoup) -> list[tuple[str, str]]:
        """Extract deduplicated internal links as (url, anchor_text)."""
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

            if not self._is_same_site_url(absolute, base_url):
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

    def _extract_text_content(
        self,
        html_or_soup: str | BeautifulSoup,
        soup: BeautifulSoup | None = None,
        source_url: str | None = None,
    ) -> str:
        """Extract bounded text cache for downstream extraction.

        If trafilatura/crawl4ai is enabled, use it first and fallback to bs4 extraction when unavailable.
        """
        if isinstance(html_or_soup, BeautifulSoup):
            html = str(html_or_soup)
            parsed_soup = html_or_soup
        else:
            html = html_or_soup
            parsed_soup = soup or BeautifulSoup(html_or_soup, "html.parser")

        if self.content_extractor == "trafilatura":
            extracted = self._extract_text_with_trafilatura(html)
            if extracted:
                return extracted[: self.MAX_CONTENT_CHARS]
        elif self.content_extractor == "crawl4ai":
            extracted = self._extract_text_with_crawl4ai(html, base_url=source_url or "")
            if extracted:
                return extracted[: self.MAX_CONTENT_CHARS]

        return self._extract_text_content_bs4(parsed_soup)

    def _extract_text_from_crawl_result_or_html(
        self,
        crawl_result: object,
        html: str,
        soup: BeautifulSoup,
        source_url: str,
    ) -> str:
        """Prefer crawl4ai markdown payload when configured, else use configured extractor."""
        if self.content_extractor == "crawl4ai":
            from_result = self._extract_markdown_from_crawl_result(crawl_result)
            if from_result:
                return from_result[: self.MAX_CONTENT_CHARS]
            return self._extract_text_content_bs4(soup)
        return self._extract_text_content(html, soup, source_url=source_url)

    def _extract_html_from_crawl_result(self, crawl_result: object) -> str:
        html = getattr(crawl_result, "html", None) or getattr(crawl_result, "cleaned_html", None) or ""
        return str(html) if html else ""

    def _extract_markdown_from_crawl_result(self, crawl_result: object) -> str | None:
        markdown_obj = getattr(crawl_result, "markdown", None)
        if markdown_obj is None:
            return None

        candidates: list[str] = []
        if isinstance(markdown_obj, str):
            text = markdown_obj.strip()
            if text:
                candidates.append(text)
        else:
            for attr in ("fit_markdown", "raw_markdown"):
                value = getattr(markdown_obj, attr, None)
                if isinstance(value, str) and value.strip():
                    candidates.append(value.strip())

        if not candidates:
            return None

        lines = []
        for raw_line in candidates[0].splitlines():
            line = self._clean_text(raw_line)
            if not line:
                continue
            if self._is_probable_nav_noise(line):
                continue
            lines.append(line)
        extracted = "\n".join(self._dedupe_lines(lines))
        return extracted or None

    def _extract_text_content_bs4(self, soup: BeautifulSoup) -> str:
        """Extract bounded markdown-like content cache for downstream extraction."""
        working = BeautifulSoup(str(soup), "html.parser")

        for node in working.find_all(list(self.NOISE_TAGS)):
            node.decompose()

        root = working.find("main") or working.find("article") or working.body or working
        parts: list[str] = []

        # Preserve document structure to help extraction prompts.
        for element in root.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "tr"]):
            text = self._clean_text(element.get_text(separator=" ", strip=True))
            if not text:
                continue
            if self._is_probable_nav_noise(text):
                continue

            tag = element.name or ""
            if tag.startswith("h") and len(tag) == 2 and tag[1].isdigit():
                level = int(tag[1])
                line = f"{'#' * min(level, 3)} {text}"
            elif tag == "li":
                line = f"- {text}"
            elif tag == "tr":
                cells = [self._clean_text(cell.get_text(separator=" ", strip=True)) for cell in element.find_all(["th", "td"])]
                cells = [cell for cell in cells if cell]
                if not cells:
                    continue
                line = " | ".join(cells)
            else:
                line = text

            parts.append(line)

        deduped = self._dedupe_lines(parts)
        text = "\n".join(deduped)
        # PostgreSQL text cannot contain null bytes.
        text = text.replace("\x00", "")
        return text[: self.MAX_CONTENT_CHARS]

    def _extract_text_with_trafilatura(self, html: str) -> str | None:
        """Try trafilatura extraction, fallback-safe when dependency is missing."""
        try:
            import trafilatura  # type: ignore
        except ImportError:
            logger.warning("NAV_CONTENT_EXTRACTOR=trafilatura but package is not installed; falling back to bs4")
            return None

        try:
            extracted = trafilatura.extract(
                html,
                include_links=False,
                include_tables=True,
                favor_precision=True,
                deduplicate=True,
                output_format="txt",
            )
        except Exception as exc:
            logger.warning("Trafilatura extraction failed: %s", exc)
            return None

        if not extracted:
            return None

        lines = []
        for raw_line in extracted.splitlines():
            line = self._clean_text(raw_line)
            if not line:
                continue
            if self._is_probable_nav_noise(line):
                continue
            lines.append(line)
        return "\n".join(self._dedupe_lines(lines))

    def _extract_text_with_crawl4ai(self, html: str, base_url: str = "") -> str | None:
        """Try crawl4ai markdown extraction from raw HTML, with safe bs4 fallback."""
        try:
            from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator  # type: ignore
        except ImportError:
            logger.warning("NAV_CONTENT_EXTRACTOR=crawl4ai but package is not installed; falling back to bs4")
            return None

        try:
            generator = DefaultMarkdownGenerator(options={"ignore_links": True})
            result = generator.generate_markdown(
                input_html=html,
                base_url=base_url or "",
                citations=False,
            )
        except Exception as exc:
            logger.warning("crawl4ai extraction failed: %s", exc)
            return None

        extracted = (
            getattr(result, "fit_markdown", None)
            or getattr(result, "raw_markdown", None)
            or None
        )
        if not extracted:
            return None

        lines = []
        for raw_line in extracted.splitlines():
            line = self._clean_text(raw_line)
            if not line:
                continue
            if self._is_probable_nav_noise(line):
                continue
            lines.append(line)
        return "\n".join(self._dedupe_lines(lines))

    def _clean_text(self, text: str) -> str:
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def _is_probable_nav_noise(self, text: str) -> bool:
        lower = text.lower().strip()
        if not lower:
            return True
        if len(lower) <= 2:
            return True
        return False

    def _dedupe_lines(self, lines: list[str]) -> list[str]:
        """Deduplicate repeated boilerplate while preserving first occurrence."""
        if not lines:
            return []
        counts: dict[str, int] = {}
        for line in lines:
            key = line.lower().strip()
            counts[key] = counts.get(key, 0) + 1

        deduped: list[str] = []
        seen: set[str] = set()
        for line in lines:
            key = line.lower().strip()
            if key in seen:
                continue
            seen.add(key)
            # Drop highly repeated short labels (nav/menu noise).
            if counts.get(key, 0) >= 3 and len(key) < 80:
                continue
            deduped.append(line)
        return deduped

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

    def _is_bot_challenge_url(self, url: str) -> bool:
        path = (urlparse(url).path or "").lower()
        return any(path.startswith(prefix) for prefix in self.BOT_CHALLENGE_PATH_PREFIXES)

    def _is_bot_protection_content(self, markdown: str | None) -> bool:
        text = (markdown or "").strip().lower()
        if not text:
            return False
        if any(marker in text for marker in self.BOT_PROTECTION_TEXT_MARKERS):
            return True
        if "captcha" in text and "security" in text and len(text) < 1500:
            return True
        return False

    def _is_extractable_page_content(self, page: NavigatedPage) -> bool:
        text = (page.markdown or "").strip()
        if not text:
            return False
        if self._is_bot_challenge_url(page.url):
            return False
        if self._is_bot_protection_content(text):
            return False
        return True

    def _page_quality_score(self, page: NavigatedPage) -> tuple[int, int, int, int]:
        markdown = (page.markdown or "").strip()
        return (
            1 if self._is_extractable_page_content(page) else 0,
            1 if markdown else 0,
            1 if page.category else 0,
            len(markdown),
        )

    def _merge_duplicate_pages(self, existing: NavigatedPage, candidate: NavigatedPage) -> NavigatedPage:
        existing_score = self._page_quality_score(existing)
        candidate_score = self._page_quality_score(candidate)

        preferred = candidate if candidate_score > existing_score else existing
        fallback = existing if preferred is candidate else candidate

        merged_category = preferred.category or fallback.category
        merged_markdown = preferred.markdown if (preferred.markdown or "").strip() else fallback.markdown
        merged_hash_source = merged_markdown if (merged_markdown or "").strip() else preferred.url

        return NavigatedPage(
            url=preferred.url,
            category=merged_category,
            markdown=merged_markdown,
            content_hash=BaseScraper.compute_hash(merged_hash_source),
        )

    def _dedupe_pages_by_url(self, pages: list[NavigatedPage]) -> list[NavigatedPage]:
        if not pages:
            return []

        by_url: dict[str, NavigatedPage] = {}
        ordered_urls: list[str] = []
        for page in pages:
            if page.url not in by_url:
                by_url[page.url] = page
                ordered_urls.append(page.url)
                continue
            by_url[page.url] = self._merge_duplicate_pages(by_url[page.url], page)
        return [by_url[url] for url in ordered_urls]

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
        # Treat subdomains of the canonical site as same-site content.
        return host.endswith(f".{canonical_host}") or canonical_host.endswith(f".{host}")


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

    # If the primary engine found no content (likely a JS-rendered site), try crawl4ai_deep
    # as a browser-based fallback that can render JavaScript.
    primary_engine = navigator.fetch_engine
    contentful_check = sum(1 for p in pages if navigator._is_extractable_page_content(p))
    if contentful_check == 0 and primary_engine != "crawl4ai_deep":
        try:
            logger.info(
                "School %s: primary engine '%s' found no content; trying crawl4ai_deep fallback",
                school_id,
                primary_engine,
            )
            deep_navigator = navigator._clone_for_engine("crawl4ai_deep", use_undetected_stealth=False)
            final_url_deep, pages_deep = await deep_navigator.discover_pages(normalized_url)
            contentful_deep = sum(1 for p in pages_deep if deep_navigator._is_extractable_page_content(p))
            if contentful_deep > 0:
                logger.info(
                    "School %s: crawl4ai_deep fallback found %s contentful pages",
                    school_id,
                    contentful_deep,
                )
                final_url = final_url_deep
                pages = pages_deep
            else:
                logger.info(
                    "School %s: crawl4ai_deep fallback still empty; retrying with undetected+stealth mode",
                    school_id,
                )
                deep_navigator_undetected = navigator._clone_for_engine(
                    "crawl4ai_deep",
                    use_undetected_stealth=True,
                )
                final_url_undetected, pages_undetected = await deep_navigator_undetected.discover_pages(normalized_url)
                contentful_undetected = sum(
                    1 for p in pages_undetected if deep_navigator_undetected._is_extractable_page_content(p)
                )
                if contentful_undetected > 0:
                    logger.info(
                        "School %s: undetected+stealth deep fallback found %s contentful pages",
                        school_id,
                        contentful_undetected,
                    )
                    final_url = final_url_undetected
                    pages = pages_undetected
        except Exception as exc:
            logger.warning("crawl4ai_deep fallback failed for school %s: %s", school_id, exc)

    if final_url:
        final_url = validator._canonicalize_bot_protection_final_url(
            normalized_url=normalized_url,
            final_url=final_url,
        )

    discovered_pages = len(pages)
    pages = navigator._dedupe_pages_by_url(pages)
    if len(pages) < discovered_pages:
        logger.info(
            "School %s: deduped discovered pages by URL (%s -> %s)",
            school_id,
            discovered_pages,
            len(pages),
        )

    now = datetime.now(timezone.utc)
    created = 0
    updated = 0
    contentful_pages = 0
    bot_blocked_pages = 0
    invalidated_existing_bot_pages = 0
    invalidated_off_domain_pages = 0
    invalidated_empty_pages = 0

    for page in pages:
        has_content = navigator._is_extractable_page_content(page)
        if has_content:
            contentful_pages += 1
        elif (page.markdown or "").strip() and (
            navigator._is_bot_challenge_url(page.url) or navigator._is_bot_protection_content(page.markdown)
        ):
            bot_blocked_pages += 1
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
    for existing_page in existing_pages:
        if not (existing_page.raw_markdown or "").strip():
            if existing_page.is_valid:
                existing_page.is_valid = False
                invalidated_empty_pages += 1
            continue

        if not navigator._is_same_site_url(existing_page.source_url, canonical_site_url):
            if existing_page.is_valid or existing_page.raw_markdown:
                existing_page.is_valid = False
                existing_page.raw_markdown = None
                invalidated_off_domain_pages += 1
            continue

        is_bot_page = (
            navigator._is_bot_challenge_url(existing_page.source_url)
            or navigator._is_bot_protection_content(existing_page.raw_markdown)
        )
        if not is_bot_page:
            continue
        if not existing_page.is_valid and not existing_page.raw_markdown:
            continue
        existing_page.is_valid = False
        existing_page.raw_markdown = None
        invalidated_existing_bot_pages += 1

    if final_url and final_url != school.website_url:
        school.website_url = final_url

    if contentful_pages > 0:
        school.scrape_status = "navigated"

    await db.commit()

    logger.info(
        "Navigated school %s: pages_discovered=%s pages_unique=%s contentful=%s bot_blocked=%s invalidated_bot_pages=%s invalidated_off_domain_pages=%s invalidated_empty_pages=%s created=%s updated=%s",
        school_id,
        discovered_pages,
        len(pages),
        contentful_pages,
        bot_blocked_pages,
        invalidated_existing_bot_pages,
        invalidated_off_domain_pages,
        invalidated_empty_pages,
        created,
        updated,
    )

    if contentful_pages == 0:
        return {
            "school_id": school_id,
            "success": False,
            "reason": "No extractable page content (possible bot protection or JS-only site)",
            "pages_found": len(pages),
            "pages_discovered": discovered_pages,
            "pages_with_content": 0,
            "created": created,
            "updated": updated,
            "final_url": final_url,
        }

    return {
        "school_id": school_id,
        "success": True,
        "pages_found": len(pages),
        "pages_discovered": discovered_pages,
        "pages_with_content": contentful_pages,
        "created": created,
        "updated": updated,
        "final_url": final_url,
    }
