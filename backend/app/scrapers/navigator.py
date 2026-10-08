"""Stage 4 navigation: Crawl4AI-first implementation."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from fnmatch import fnmatch
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import CRAWLER_USER_AGENT, get_settings
from app.models import School, ScrapeType, SourcePage
from app.scrapers.base import BaseScraper
from app.scrapers.fee_pages import fee_http_client, fetch_fee_documents, page_key
from app.scrapers.price_evidence import currency_price_starts
from app.scrapers.shared_site_check import level_family
from app.scrapers.url_validator import URLValidator
from app.utils.website_data import WEBSITE_DATA_WITHHELD_KEY

logger = logging.getLogger(__name__)


@dataclass
class NavigatedPage:
    """In-memory representation of a crawled page."""

    url: str
    category: str | None
    markdown: str | None
    content_hash: str
    cache_status: str | None = None
    head_fingerprint: str | None = None
    # Same-site links found on the page, as (absolute URL, link text).
    links: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class BatchDiscoverOutcome:
    """Batch crawl outcome for one seed website URL."""

    seed_url: str
    final_url: str | None
    pages: list[NavigatedPage]
    error: str | None = None


class WebsiteNavigator:
    """Crawl4AI deep crawler wrapper for school websites."""

    MAX_PAGES = 12
    MAX_DEPTH = 1
    PAGE_TIMEOUT_SECONDS = 30.0
    CRAWL_TIMEOUT_SECONDS = 120.0
    MAX_CONTENT_CHARS = 15000

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
        "about": [
            "about",
            "za-nas",
            "за-нас",
            "za-chou",
            "za-chdg",
            "za-dg",
            "za-uchilishte",
            "мисия",
            "екип",
            "team",
        ],
        "pricing": ["price", "prices", "pricing", "fees", "tuition", "ceni", "tseni", "taksi", "цен", "цени", "такс"],
        "admission": [
            "admission", "apply", "enroll", "priem", "прием", "кандидат", "запис",
            "предучилищ", "preduchilisht", "първи-клас", "parvi-klas", "пети-клас", "peti-klas",
            "след-седми-клас", "sled-sedmi-klas",
        ],
        "contact": ["contact", "contacts", "kontakti", "контакт", "телефон", "адрес", "address", "phone"],
        "programs": ["program", "curriculum", "courses", "programi", "обуч", "програм", "класни", "klasni-rakovoditeli"],
        "facilities": ["facility", "campus", "baza", "база", "кампус", "infrastructure"],
    }

    PRIORITY_KEYWORDS = [
        "about",
        "za-nas",
        "za-chou",
        "za-chdg",
        "za-dg",
        "za-uchilishte",
        "мисия",
        "team",
        "pricing",
        "prices",
        "fee",
        "fees",
        "ceni",
        "tseni",
        "цени",
        "grafik-i-tseni",
        "taksi",
        "прием",
        "предучилищ",
        "първи-клас",
        "пети-клас",
        "след-седми-клас",
        "admission",
        "apply",
        "enroll",
        "contact",
        "контакт",
        "kontakti",
        "program",
        "програм",
        "обуч",
        "класни",
        "klasni-rakovoditeli",
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
    CONTACT_SIGNAL_CLASS_HINTS = ("contact", "adress", "address", "location", "map")
    MAP_LINK_HINTS = ("google.com/maps", "maps.app.goo.gl", "mapclient=embed", "/maps/")
    MAIN_CONTENT_SELECTORS = (
        '[id^="layout-page-"]',
        "main",
        "article",
        '[role="main"]',
        ".page-content",
        ".entry-content",
        ".post-content",
        ".content-area",
        ".layout-custom",
        ".layout-container",
        ".content-frame",
    )
    MAIN_CONTENT_NOISE_SELECTORS = (
        "script",
        "style",
        "noscript",
        "header",
        "footer",
        "nav",
        "aside",
        "form",
        "#layout-menu",
        "#header-content",
        "#mobile-menu",
        ".menu",
        ".menu-content",
        ".top-menu",
        ".section-menu",
        ".main-menu",
        ".sub-menu",
        ".breadcrumb",
        ".breadcrumbs",
        "#search-form",
        ".search",
        ".swiper",
    )
    PHONE_HREF_RE = re.compile(r"^tel:\s*(.+)$", flags=re.IGNORECASE)
    EMAIL_HREF_RE = re.compile(r"^mailto:\s*(.+)$", flags=re.IGNORECASE)
    EMAIL_TEXT_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", flags=re.IGNORECASE)
    PHONE_TEXT_RE = re.compile(r"(?:\+?\d[\d\s()/.-]{6,}\d)")
    ADDRESS_TEXT_RE = re.compile(
        r"(?:гр\.|ул\.|бул\.|ж\.к\.|жк\.|кв\.|pl\.|street|st\.|boulevard|blvd|road|rd\.|avenue|ave\.)",
        flags=re.IGNORECASE,
    )

    def __init__(self, country_code: str = "bg", *, bypass_cache: bool = True):
        # A crawl reads the live site. crawl4ai's local cache is off unless asked for: after
        # the 0.9 upgrade migrated it, a hit on an older record returned a 16-character
        # content hash in place of the page, which was stored as the page's text.
        self.country_code = country_code
        self.bypass_cache = bypass_cache
        self.settings = get_settings()

    def _build_browser_config(self) -> Any:
        from crawl4ai import BrowserConfig  # type: ignore

        # No stealth mode: the crawler identifies itself and does not hide that it is automated.
        return BrowserConfig(
            headless=True,
            verbose=False,
            ignore_https_errors=True,
            user_agent=CRAWLER_USER_AGENT,
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

        page_category = self.classify_page(website_url)
        decoded_url = unquote(website_url or "").lower()
        use_raw_html = page_category in {"about", "contact"} or any(
            token in decoded_url for token in ("класни-ръководители", "klasni-rakovoditeli")
        )
        markdown_generator = DefaultMarkdownGenerator(
            content_filter=PruningContentFilter(threshold=0.45),
            content_source="raw_html" if use_raw_html else "cleaned_html",
        )

        return CrawlerRunConfig(
            cache_mode=CacheMode.BYPASS if self.bypass_cache else CacheMode.ENABLED,
            check_cache_freshness=True,
            check_robots_txt=True,
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
        cleaned_html = getattr(crawl_result, "cleaned_html", None)
        raw_html = getattr(crawl_result, "html", None)
        html = cleaned_html or raw_html
        identity_signals = self._dedupe_lines(
            self._extract_html_identity_signals(raw_html if isinstance(raw_html, str) else None)
            + self._extract_html_identity_signals(cleaned_html if isinstance(cleaned_html, str) else None)
        )
        contact_signals = self._dedupe_lines(
            self._extract_html_contact_signals(raw_html if isinstance(raw_html, str) else None)
            + self._extract_html_contact_signals(cleaned_html if isinstance(cleaned_html, str) else None)
        )
        focused_html_candidates = [
            self._extract_main_content_text(raw_html if isinstance(raw_html, str) else None),
            self._extract_main_content_text(cleaned_html if isinstance(cleaned_html, str) else None),
        ]
        focused_html_candidates = [candidate for candidate in focused_html_candidates if candidate]
        if focused_html_candidates:
            return self._append_html_signal_sections(
                max(focused_html_candidates, key=len),
                identity_signals=identity_signals,
                contact_signals=contact_signals,
            )

        if isinstance(markdown_obj, str) and markdown_obj.strip():
            candidates.append(markdown_obj.strip())
        elif markdown_obj is not None:
            for attr in ("fit_markdown", "raw_markdown"):
                value = getattr(markdown_obj, attr, None)
                if isinstance(value, str) and value.strip():
                    candidates.append(value.strip())

        if not candidates:
            if not isinstance(html, str) or not html.strip():
                return self._append_html_signal_sections(
                    None,
                    identity_signals=identity_signals,
                    contact_signals=contact_signals,
                )
            soup = BeautifulSoup(html, "html.parser")
            for node in soup.find_all(["script", "style", "noscript", "header", "footer", "nav", "aside"]):
                node.decompose()
            text = soup.get_text("\n", strip=True)
            lines = self._dedupe_lines(text.splitlines())
            extracted = "\n".join(lines)
            normalized = extracted[: self.MAX_CONTENT_CHARS] if extracted else None
            return self._append_html_signal_sections(
                normalized,
                identity_signals=identity_signals,
                contact_signals=contact_signals,
            )

        extracted_candidates: list[str] = []
        for candidate in candidates:
            lines = self._dedupe_lines(candidate.splitlines())
            extracted = "\n".join(lines)
            if extracted:
                extracted_candidates.append(extracted[: self.MAX_CONTENT_CHARS])
        if not extracted_candidates:
            return self._append_html_signal_sections(
                None,
                identity_signals=identity_signals,
                contact_signals=contact_signals,
            )
        return self._append_html_signal_sections(
            max(extracted_candidates, key=len),
            identity_signals=identity_signals,
            contact_signals=contact_signals,
        )

    def _extract_html_identity_signals(self, html: str | None) -> list[str]:
        """Keep deterministic official-site identity metadata for later resolution."""
        if not html or not html.strip():
            return []

        soup = BeautifulSoup(html, "html.parser")
        candidates: list[str] = []
        if soup.title:
            title = soup.title.get_text(" ", strip=True)
            title_parts = re.split(r"\s+[|–—-]\s+|\|", title)
            candidates.extend(part for part in title_parts if part.strip())

        for meta in soup.find_all("meta"):
            key = str(meta.get("property") or meta.get("name") or "").casefold()
            if key in {"og:site_name", "application-name"}:
                candidates.append(str(meta.get("content") or ""))

        for image in soup.find_all("img"):
            identity_hint = " ".join(
                str(value or "")
                for value in (image.get("src"), image.get("class"), image.get("id"))
            ).casefold()
            if "logo" not in identity_hint:
                continue
            candidates.extend([str(image.get("alt") or ""), str(image.get("title") or "")])

        organization_types = {
            "educationalorganization",
            "school",
            "preschool",
            "childcare",
        }
        for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
            try:
                payload = json.loads(script.string or script.get_text() or "null")
            except (TypeError, ValueError):
                continue
            pending = payload if isinstance(payload, list) else [payload]
            while pending:
                item = pending.pop()
                if not isinstance(item, dict):
                    continue
                graph = item.get("@graph")
                if isinstance(graph, list):
                    pending.extend(graph)
                raw_types = item.get("@type")
                types = raw_types if isinstance(raw_types, list) else [raw_types]
                normalized_types = {
                    re.split(r"[/#:]", str(value or "").rstrip("/"))[-1].casefold()
                    for value in types
                }
                if normalized_types & organization_types:
                    candidates.append(str(item.get("name") or ""))

        return self._dedupe_lines(candidates)[:8]

    def _append_identity_signals(self, markdown: str | None, signals: list[str]) -> str | None:
        return self._append_html_signal_sections(markdown, identity_signals=signals)

    def _append_html_signal_sections(
        self,
        markdown: str | None,
        *,
        identity_signals: list[str] | None = None,
        contact_signals: list[str] | None = None,
    ) -> str | None:
        base = (markdown or "").strip()
        section_inputs = (
            ("HTML identity signals", identity_signals or []),
            ("HTML contact signals", contact_signals or []),
        )
        if not any(signals for _heading, signals in section_inputs):
            return base or None

        existing = base.casefold()
        sections: list[str] = []
        for heading, signals in section_inputs:
            missing = [signal for signal in signals if signal.casefold() not in existing]
            if not missing:
                continue
            section = f"## {heading}\n" + "\n".join(missing)
            sections.append(section)
            existing += "\n" + section.casefold()
        if not sections:
            return base or None

        tail = "\n\n".join(sections)
        if not base:
            return tail[: self.MAX_CONTENT_CHARS]

        separator = "\n\n"
        available_base_chars = max(
            0,
            self.MAX_CONTENT_CHARS - len(separator) - len(tail),
        )
        retained_base = base[:available_base_chars].rstrip()
        merged = f"{retained_base}{separator}{tail}" if retained_base else tail
        return merged[: self.MAX_CONTENT_CHARS]

    def _extract_main_content_text(self, html: str | None) -> str | None:
        if not isinstance(html, str) or not html.strip():
            return None

        soup = BeautifulSoup(html, "html.parser")
        for selector in self.MAIN_CONTENT_SELECTORS:
            selector_candidates = soup.select(selector)
            extracted_candidates: list[str] = []
            for candidate in selector_candidates:
                extracted = self._extract_text_from_candidate(candidate)
                if extracted:
                    extracted_candidates.append(extracted)
            if extracted_candidates:
                return max(extracted_candidates, key=len)[: self.MAX_CONTENT_CHARS]

        extracted = self._extract_text_from_candidate(soup.body or soup)
        return extracted[: self.MAX_CONTENT_CHARS] if extracted else None

    def _extract_text_from_candidate(self, candidate: Any) -> str | None:
        fragment = BeautifulSoup(str(candidate), "html.parser")
        root = fragment.find()
        if root is None:
            return None

        for selector in self.MAIN_CONTENT_NOISE_SELECTORS:
            for node in root.select(selector):
                node.decompose()

        table_lines: list[str] = []
        for table in root.find_all("table"):
            for row in table.find_all("tr"):
                cells = [
                    self._clean_text(cell.get_text(" ", strip=True))
                    for cell in row.find_all(["th", "td"])
                ]
                cells = [cell for cell in cells if cell]
                if cells:
                    table_lines.append(" | ".join(cells))

        text_lines = root.get_text("\n", strip=True).splitlines()
        lines = self._dedupe_lines(table_lines + text_lines)
        if len(lines) < 3:
            return None

        extracted = "\n".join(lines).strip()
        return extracted if len(extracted) >= 80 else None

    def _normalize_contact_signal_text(self, text: str) -> str:
        normalized = self._clean_text(text)
        if not normalized:
            return ""
        normalized = re.sub(r"^[*_#`|>\-:\s]+", "", normalized)
        if re.match(r"^\d+\.\s*(?:гр\.|ул\.|бул\.|ж\.к\.|жк\.|кв\.)", normalized, flags=re.IGNORECASE):
            normalized = re.sub(r"^\d+\.\s*", "", normalized)
        return normalized.strip()

    def _format_contact_signal(self, label: str, value: str) -> str | None:
        normalized = self._normalize_contact_signal_text(value)
        if not normalized:
            return None
        return f"{label}: {normalized}"

    def _extract_map_link_coordinates(self, href: str) -> tuple[float, float] | None:
        if not href:
            return None
        parsed = urlparse(href)
        query = parse_qs(parsed.query)

        for param_name in ("ll", "center", "q", "query", "destination"):
            for value in query.get(param_name) or []:
                match = re.search(r"(-?\d+\.\d+)\s*,\s*(-?\d+\.\d+)", value)
                if match:
                    return float(match.group(1)), float(match.group(2))

        fragment_match = re.search(r"[?#](?:.*?)(?:center|q|query|destination)=(-?\d+\.\d+),(-?\d+\.\d+)", href)
        if fragment_match:
            return float(fragment_match.group(1)), float(fragment_match.group(2))

        for value in (parsed.fragment or "", parsed.path or ""):
            match = re.search(r"(-?\d+\.\d+)\s*,\s*(-?\d+\.\d+)", value)
            if match:
                return float(match.group(1)), float(match.group(2))
        path_match = re.search(r"@(-?\d+\.\d+),(-?\d+\.\d+)", href)
        if path_match:
            return float(path_match.group(1)), float(path_match.group(2))

        return None

    def _extract_html_contact_signals(self, html: str | None) -> list[str]:
        if not html or not html.strip():
            return []

        soup = BeautifulSoup(html, "html.parser")
        candidates: list[str] = []

        for anchor in soup.find_all("a", href=True):
            href = str(anchor.get("href") or "").strip()
            text = self._normalize_contact_signal_text(anchor.get_text(" ", strip=True))
            if not href and not text:
                continue

            phone_match = self.PHONE_HREF_RE.match(href)
            if phone_match:
                signal = self._format_contact_signal("Phone", text or phone_match.group(1))
                if signal:
                    candidates.append(signal)
                continue

            email_match = self.EMAIL_HREF_RE.match(href)
            if email_match:
                signal = self._format_contact_signal("Email", text or email_match.group(1))
                if signal:
                    candidates.append(signal)
                continue

            lowered_href = href.lower()
            if any(marker in lowered_href for marker in self.MAP_LINK_HINTS) and self.ADDRESS_TEXT_RE.search(text):
                signal = self._format_contact_signal("Address", text)
                if signal:
                    candidates.append(signal)
                coords = self._extract_map_link_coordinates(href)
                if coords is not None:
                    candidates.append(f"Coordinates: {coords[0]:.6f}, {coords[1]:.6f}")

        for node in soup.find_all(True):
            if node.name in {"html", "body", "ul", "ol", "nav", "header", "footer"}:
                continue
            attrs = " ".join(
                str(value)
                for key, value in node.attrs.items()
                if key in {"class", "id"}
            ).lower()
            if not attrs or not any(hint in attrs for hint in self.CONTACT_SIGNAL_CLASS_HINTS):
                continue
            text = self._normalize_contact_signal_text(node.get_text(" ", strip=True))
            if not text:
                continue
            if self.ADDRESS_TEXT_RE.search(text):
                signal = self._format_contact_signal("Address", text)
                if signal:
                    candidates.append(signal)
            for email in self.EMAIL_TEXT_RE.findall(text):
                signal = self._format_contact_signal("Email", email)
                if signal:
                    candidates.append(signal)
            for phone in self.PHONE_TEXT_RE.findall(text):
                signal = self._format_contact_signal("Phone", phone)
                if signal:
                    candidates.append(signal)

        return self._dedupe_lines(candidates)[:8]

    def _append_contact_signals(self, markdown: str | None, signals: list[str]) -> str | None:
        return self._append_html_signal_sections(markdown, contact_signals=signals)

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

    def _is_extractable_page_content(self, page: NavigatedPage) -> bool:
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

    def _dedupe_pages(self, pages: list[NavigatedPage]) -> list[NavigatedPage]:
        by_url: dict[str, NavigatedPage] = {}
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

    async def robots_disallows(self, url: str) -> bool:
        """Return True when the site's robots.txt forbids the crawler from fetching ``url``."""
        from crawl4ai.utils import RobotsParser  # type: ignore

        try:
            return not await RobotsParser().can_fetch(url, CRAWLER_USER_AGENT)
        except Exception as exc:
            # The crawl itself already honoured robots.txt; an unreadable rules cache must
            # not stop the navigation result from being saved.
            logger.warning("robots.txt check failed for %s: %s", url, exc)
            return False

    async def discover_pages(self, website_url: str) -> tuple[str, list[NavigatedPage]]:
        from crawl4ai import AsyncWebCrawler  # type: ignore

        normalized_url = self._normalize_url(website_url)
        browser_config = self._build_browser_config()
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
        return final_url, pages

    def _extract_pages_from_results(
        self,
        *,
        normalized_url: str,
        results_obj: Any,
    ) -> tuple[str, list[NavigatedPage]]:
        if isinstance(results_obj, (list, tuple)):
            results = list(results_obj)
        else:
            try:
                results = list(results_obj)
            except TypeError:
                results = [results_obj]

        pages: list[NavigatedPage] = []
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
                NavigatedPage(
                    url=storage_url,
                    category=self.classify_page(storage_url, title=title),
                    markdown=markdown if (markdown or "").strip() else None,
                    content_hash=BaseScraper.compute_hash(str(hash_seed)),
                    cache_status=str(cache_status) if cache_status is not None else None,
                    head_fingerprint=str(head_fingerprint) if head_fingerprint else None,
                    links=self._internal_links(result),
                )
            )

        return final_url, self._dedupe_pages(pages)

    def _first_error(self, results_obj: Any) -> str | None:
        for result in self._iter_results(results_obj):
            message = getattr(result, "error_message", None)
            if message:
                return str(message)
        return None

    def _internal_links(self, crawl_result: Any) -> list[tuple[str, str]]:
        links = getattr(crawl_result, "links", None)
        internal = links.get("internal") if isinstance(links, dict) else None
        return [
            (str(link["href"]), str(link.get("text") or ""))
            for link in internal or []
            if isinstance(link, dict) and link.get("href")
        ]

    async def discover_pages_many(
        self,
        website_urls: list[str],
        *,
        max_concurrency: int = 3,
    ) -> dict[str, BatchDiscoverOutcome]:
        """Discover pages for many school websites using chunked Crawl4AI arun_many."""
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

        concurrency = max(1, int(max_concurrency))
        outcomes: dict[str, BatchDiscoverOutcome] = {}

        for chunk_start in range(0, len(normalized_urls), concurrency):
            chunk_urls = normalized_urls[chunk_start : chunk_start + concurrency]
            try:
                chunk_outcomes = await self._discover_pages_many_chunk(
                    chunk_urls,
                    max_concurrency=min(concurrency, len(chunk_urls)),
                )
            except Exception as exc:
                logger.warning(
                    "arun_many failed for %s-school chunk; falling back to sequential discover_pages: %s",
                    len(chunk_urls),
                    exc,
                )
                chunk_outcomes = await self._discover_pages_sequentially(chunk_urls)

            outcomes.update(chunk_outcomes)

        return outcomes

    async def _discover_pages_sequentially(
        self,
        website_urls: list[str],
    ) -> dict[str, BatchDiscoverOutcome]:
        outcomes: dict[str, BatchDiscoverOutcome] = {}
        for url in website_urls:
            try:
                final_url, pages = await self.discover_pages(url)
                outcomes[url] = BatchDiscoverOutcome(
                    seed_url=url,
                    final_url=final_url,
                    pages=pages,
                )
            except Exception as school_exc:
                outcomes[url] = BatchDiscoverOutcome(
                    seed_url=url,
                    final_url=None,
                    pages=[],
                    error=str(school_exc),
                )
        return outcomes

    async def _discover_pages_many_chunk(
        self,
        website_urls: list[str],
        *,
        max_concurrency: int,
    ) -> dict[str, BatchDiscoverOutcome]:
        """Crawl each seed with its own ``arun`` on one shared browser.

        ``arun_many`` with a deep-crawl strategy returns one flat list of pages, not one
        result per seed, so its results cannot be matched back to the schools: indexing
        them by seed handed a school another school's pages and website.
        """
        from crawl4ai import AsyncWebCrawler  # type: ignore

        if not website_urls:
            return {}

        semaphore = asyncio.Semaphore(max(1, int(max_concurrency)))
        timeout = max(
            self.CRAWL_TIMEOUT_SECONDS,
            float(getattr(self.settings, "nav_school_timeout_seconds", self.CRAWL_TIMEOUT_SECONDS)),
        )

        async with AsyncWebCrawler(config=self._build_browser_config()) as crawler:

            async def crawl(url: str) -> BatchDiscoverOutcome:
                async with semaphore:
                    try:
                        results_obj = await asyncio.wait_for(
                            crawler.arun(url=url, config=self._build_run_config(url)),
                            timeout=timeout,
                        )
                        final_url, pages = self._extract_pages_from_results(
                            normalized_url=url,
                            results_obj=results_obj,
                        )
                    except Exception as exc:
                        return BatchDiscoverOutcome(
                            seed_url=url,
                            final_url=None,
                            pages=[],
                            error=str(exc) or type(exc).__name__,
                        )
                    # A failed fetch comes back as an unsuccessful result, not an
                    # exception; without its message the caller would not retry.
                    error = None if pages else self._first_error(results_obj)
                    return BatchDiscoverOutcome(
                        seed_url=url, final_url=final_url, pages=pages, error=error
                    )

            outcomes = await asyncio.gather(*(crawl(url) for url in website_urls))

        return dict(zip(website_urls, outcomes, strict=True))


# The crawl's exclusions hold for followed fee links too (an old news post about fees is
# not the fee list), except that fee PDFs are wanted and usually sit under an uploads
# folder.
_FEE_LINK_EXCLUDE_PATTERNS = tuple(
    pattern
    for pattern in WebsiteNavigator.EXCLUDE_PATTERNS
    if pattern not in ("*.pdf", "*/wp-content/*")
)


async def _follow_fee_links(
    navigator: WebsiteNavigator, school: School, site_url: str, pages: list[NavigatedPage]
) -> list[NavigatedPage]:
    """``pages`` plus the fee pages and PDFs they link to that the crawl did not capture.

    A crawled fee page that states no price is fetched again as plain HTML and replaced
    when that copy does: a tabbed page can lose its fee tab in the rendered DOM.
    Best effort: a failure here leaves the crawl result as it is.
    """
    links = [
        link
        for page in pages
        for link in page.links
        if not any(fnmatch(link[0].lower(), pattern) for pattern in _FEE_LINK_EXCLUDE_PATTERNS)
    ]
    if not links:
        return pages
    # Only a crawled fee page that states no price is worth a second fetch.
    settled_urls = [
        page.url
        for page in pages
        if page.category != "pricing" or currency_price_starts(page.markdown or "")
    ]
    try:
        async with fee_http_client() as client:
            documents = await fetch_fee_documents(
                links,
                site_url=site_url,
                known_urls=settled_urls,
                html_to_text=navigator._extract_main_content_text,
                client=client,
                school_family=level_family(school.education_level),
                disallowed=navigator.robots_disallows,
            )
    except Exception as exc:
        logger.warning("Fee link follow-up failed for school %s: %s", school.id, exc)
        return pages

    by_key = {page_key(page.url): page for page in pages}
    merged = list(pages)
    for document in documents:
        crawled = by_key.get(page_key(document.url))
        if crawled is not None and not currency_price_starts(document.text):
            continue  # the crawled copy is no worse
        fee_page = NavigatedPage(
            url=crawled.url if crawled is not None else document.url,
            category=(crawled.category if crawled is not None else None) or "pricing",
            markdown=document.text,
            content_hash=BaseScraper.compute_hash(document.text),
        )
        if crawled is not None:
            merged[merged.index(crawled)] = fee_page
        else:
            merged.append(fee_page)
    return merged


async def _persist_navigation_result(
    db: AsyncSession,
    school: School,
    *,
    normalized_url: str,
    final_url: str | None,
    pages: list[NavigatedPage],
    validator: URLValidator,
    navigator: WebsiteNavigator,
) -> dict[str, Any]:
    if final_url:
        final_url = validator._canonicalize_bot_protection_final_url(
            normalized_url=normalized_url,
            final_url=final_url,
        )

    pages = await _follow_fee_links(navigator, school, final_url or normalized_url, pages)

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

    # The site asks not to be crawled: stop using the pages fetched before and withhold the
    # data extracted from them. Only a later extraction plus validation clears the marker.
    robots_blocked = not pages and await navigator.robots_disallows(normalized_url)
    if robots_blocked:
        for existing_page in existing_pages:
            if existing_page.is_valid:
                existing_page.is_valid = False
                invalidated += 1
        school.attributes = {**(school.attributes or {}), WEBSITE_DATA_WITHHELD_KEY: True}

    if final_url and final_url != school.website_url:
        school.website_url = final_url

    if contentful_pages > 0:
        school.scrape_status = "navigated"

    await db.commit()

    if contentful_pages == 0:
        return {
            "school_id": school_id,
            "success": False,
            "reason": "Disallowed by robots.txt" if robots_blocked else "No extractable page content",
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


async def navigate_school(db: AsyncSession, school_id: int, country_code: str = "bg") -> dict[str, Any]:
    """Run Stage 4 navigation and persist source pages."""
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

    navigator = WebsiteNavigator(country_code=country_code)
    try:
        final_url, pages = await navigator.discover_pages(normalized_url)
    except Exception as exc:
        logger.error("Navigation failed for school %s: %s", school_id, exc)
        return {"school_id": school_id, "success": False, "reason": f"Navigation failed: {exc}"}

    return await _persist_navigation_result(
        db=db,
        school=school,
        normalized_url=normalized_url,
        final_url=final_url,
        pages=pages,
        validator=validator,
        navigator=navigator,
    )


async def navigate_schools_batch(
    db: AsyncSession,
    school_ids: list[int],
    *,
    country_code: str = "bg",
    max_concurrency: int = 3,
) -> list[dict[str, Any]]:
    """Run Stage 4 navigation for many schools using Crawl4AI arun_many."""
    if not school_ids:
        return []

    schools_result = await db.execute(select(School).where(School.id.in_(school_ids)))
    schools = schools_result.scalars().all()
    by_id = {school.id: school for school in schools}

    validator = URLValidator(country_code=country_code)
    navigator = WebsiteNavigator(country_code=country_code)

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
                outcome = BatchDiscoverOutcome(
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

        result = await _persist_navigation_result(
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
