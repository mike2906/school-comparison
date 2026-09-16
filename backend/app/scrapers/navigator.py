"""Stage 4 navigation: Crawl4AI-first implementation."""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from math import ceil
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import School, ScrapeType, SourcePage
from app.scrapers.base import BaseScraper
from app.scrapers.url_validator import URLValidator
from app.utils.website_data import WEBSITE_PUBLISHABLE_STATUSES

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
    status_code: int | None = None


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
    DEFINITIVE_REMOVAL_STATUS_CODES = frozenset({404, 410})
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

    def __init__(
        self,
        country_code: str = "bg",
        *,
        bypass_cache: bool = False,
        cache_alias_urls: list[str] | None = None,
    ):
        self.country_code = country_code
        self.bypass_cache = bypass_cache
        self.cache_alias_urls = tuple(dict.fromkeys(cache_alias_urls or []))
        self.settings = get_settings()

    def _build_browser_config(self, *, enable_stealth: bool = True) -> Any:
        from crawl4ai import BrowserConfig  # type: ignore

        return BrowserConfig(
            headless=True,
            verbose=False,
            ignore_https_errors=True,
            enable_stealth=enable_stealth,
            user_agent="Mozilla/5.0 (compatible; SchoolScraper/1.0; +https://kg.sofia.bg)",
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
        if page.status_code in self.DEFINITIVE_REMOVAL_STATUS_CODES:
            return False
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

    def _is_same_page_url(self, first_url: str, second_url: str) -> bool:
        first = urlparse(first_url)
        second = urlparse(second_url)
        first_path = unquote(first.path or "/").rstrip("/") or "/"
        second_path = unquote(second.path or "/").rstrip("/") or "/"
        return (
            self._host_key(first_url) == self._host_key(second_url)
            and first_path == second_path
            and first.query == second.query
        )

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

    async def discover_pages(self, website_url: str) -> tuple[str, list[NavigatedPage]]:
        from crawl4ai import AsyncWebCrawler  # type: ignore

        normalized_url = self._normalize_url(website_url)
        browser_config = self._build_browser_config(enable_stealth=True)
        run_config = self._build_run_config(normalized_url)

        async with AsyncWebCrawler(config=browser_config) as crawler:
            results_obj = await asyncio.wait_for(
                crawler.arun(url=normalized_url, config=run_config),
                timeout=self.CRAWL_TIMEOUT_SECONDS,
            )

        cacheable_results: list[Any] = []
        removal_cache_urls: list[str] = []
        final_url, pages = self._extract_pages_from_results(
            normalized_url=normalized_url,
            results_obj=results_obj,
            cacheable_results=cacheable_results if self.bypass_cache else None,
            removal_cache_urls=removal_cache_urls if self.bypass_cache else None,
        )
        if self.bypass_cache:
            await self._refresh_live_cache_best_effort(
                cacheable_results=cacheable_results,
                removal_cache_urls=removal_cache_urls,
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
            retry_cacheable_results: list[Any] = []
            retry_removal_cache_urls: list[str] = []
            retry_final_url, retry_pages = self._extract_pages_from_results(
                normalized_url=normalized_url,
                results_obj=retry_results,
                cacheable_results=retry_cacheable_results if self.bypass_cache else None,
                removal_cache_urls=(
                    retry_removal_cache_urls if self.bypass_cache else None
                ),
            )
            if self.bypass_cache:
                await self._refresh_live_cache_best_effort(
                    cacheable_results=retry_cacheable_results,
                    removal_cache_urls=retry_removal_cache_urls,
                )
            if retry_pages:
                return retry_final_url, self._dedupe_pages(pages + retry_pages)
        except Exception as exc:
            logger.warning("Undetected retry failed for %s: %s", normalized_url, exc)

        return final_url, pages

    async def _refresh_live_cache_best_effort(
        self,
        *,
        cacheable_results: list[Any],
        removal_cache_urls: list[str],
    ) -> None:
        """Maintain Crawl4AI's cache without discarding authoritative live evidence."""
        if removal_cache_urls:
            try:
                await self._remove_cache_entries(removal_cache_urls)
            except Exception as exc:
                logger.warning(
                    "Failed to remove obsolete Crawl4AI cache entries: %s", exc
                )
        if cacheable_results:
            try:
                await self._replace_cache_with_usable_results(cacheable_results)
            except Exception as exc:
                logger.warning("Failed to refresh Crawl4AI cache entries: %s", exc)

    async def _replace_cache_with_usable_results(
        self, crawl_results: list[Any]
    ) -> None:
        """Cache verified usable live results without persisting unusable responses."""
        from crawl4ai.async_database import async_db_manager  # type: ignore

        failures: list[str] = []
        for result in crawl_results:
            result_urls = list(
                dict.fromkeys(
                    filter(
                        None,
                        (
                            str(getattr(result, "url", "") or ""),
                            str(getattr(result, "redirected_url", "") or ""),
                        ),
                    )
                )
            )
            cache_urls = self._expand_cache_aliases(result_urls)
            for cache_url in cache_urls:
                try:
                    cache_result = copy.copy(result)
                    cache_result.url = cache_url
                    await async_db_manager.acache_url(cache_result)
                    cached = await async_db_manager.aget_cached_url(cache_url)
                    if cached is None or cached.html != result.html:
                        raise RuntimeError("cache verification failed")
                except Exception as exc:
                    failure = f"{cache_url}: {exc}"
                    try:
                        await self._remove_exact_cache_entries([cache_url])
                    except Exception as eviction_exc:
                        failure += f"; eviction failed: {eviction_exc}"
                    failures.append(failure)

        if failures:
            raise RuntimeError(
                "Failed to refresh Crawl4AI cache for " + "; ".join(failures)
            )

    async def _remove_cache_entries(self, urls: list[str]) -> None:
        """Remove definitively obsolete URLs from Crawl4AI's cache."""
        await self._remove_exact_cache_entries(self._expand_cache_aliases(urls))

    async def _remove_exact_cache_entries(self, urls: list[str]) -> None:
        """Remove the specified Crawl4AI cache keys without alias expansion."""
        from crawl4ai.async_database import async_db_manager  # type: ignore

        unique_urls = list(dict.fromkeys(url for url in urls if url))
        if not unique_urls:
            return

        async def delete_urls(db: Any) -> None:
            placeholders = ", ".join("?" for _ in unique_urls)
            await db.execute(
                f"DELETE FROM crawled_data WHERE url IN ({placeholders})",
                tuple(unique_urls),
            )

        await async_db_manager.execute_with_retry(delete_urls)
        failures: list[str] = []
        for url in unique_urls:
            if await async_db_manager.aget_cached_url(url) is not None:
                failures.append(url)
        if failures:
            raise RuntimeError(
                "Failed to remove Crawl4AI cache entries for " + "; ".join(failures)
            )

    def _expand_cache_aliases(self, urls: list[str]) -> list[str]:
        """Include stored canonical aliases for cache replacement or removal."""
        unique_urls = list(dict.fromkeys(url for url in urls if url))
        for alias_url in self.cache_alias_urls:
            if alias_url not in unique_urls and any(
                self._is_same_page_url(alias_url, candidate_url)
                for candidate_url in unique_urls
            ):
                unique_urls.append(alias_url)
        return unique_urls

    def _extract_pages_from_results(
        self,
        *,
        normalized_url: str,
        results_obj: Any,
        cacheable_results: list[Any] | None = None,
        removal_cache_urls: list[str] | None = None,
    ) -> tuple[str, list[NavigatedPage]]:
        results = self._iter_results(results_obj)

        pages: list[NavigatedPage] = []
        cache_candidates: dict[str, tuple[NavigatedPage, Any]] = {}
        final_url = normalized_url
        final_url_selected = False

        for result in results:
            raw_status_code = getattr(result, "status_code", None)
            try:
                status_code = int(raw_status_code) if raw_status_code is not None else None
            except (TypeError, ValueError):
                status_code = None
            if (
                not getattr(result, "success", False)
                and status_code not in self.DEFINITIVE_REMOVAL_STATUS_CODES
            ):
                continue

            requested_url = getattr(result, "url", None) or normalized_url
            if status_code in self.DEFINITIVE_REMOVAL_STATUS_CODES:
                raw_url = requested_url
            else:
                raw_url = getattr(result, "redirected_url", None) or requested_url
            storage_url = self._normalize_url(str(raw_url))
            if (
                not final_url_selected
                and status_code not in self.DEFINITIVE_REMOVAL_STATUS_CODES
            ):
                final_url = storage_url
                final_url_selected = True

            markdown = self._extract_markdown(result)
            title = ""
            metadata = getattr(result, "metadata", None)
            if isinstance(metadata, dict):
                title = str(metadata.get("title", "") or "")

            head_fingerprint = getattr(result, "head_fingerprint", None)
            cache_status = getattr(result, "cache_status", None)
            hash_seed = head_fingerprint or markdown or storage_url
            page = NavigatedPage(
                url=storage_url,
                category=self.classify_page(storage_url, title=title),
                markdown=markdown if (markdown or "").strip() else None,
                content_hash=BaseScraper.compute_hash(str(hash_seed)),
                cache_status=str(cache_status) if cache_status is not None else None,
                head_fingerprint=str(head_fingerprint) if head_fingerprint else None,
                status_code=status_code,
            )
            pages.append(page)
            if (
                removal_cache_urls is not None
                and page.status_code in self.DEFINITIVE_REMOVAL_STATUS_CODES
            ):
                removal_cache_urls.extend(
                    [
                        str(requested_url),
                        str(getattr(result, "redirected_url", "") or ""),
                        storage_url,
                    ]
                )
            if cacheable_results is not None and self._is_extractable_page_content(page):
                current = cache_candidates.get(page.url)
                if current is None or len(page.markdown or "") > len(
                    current[0].markdown or ""
                ):
                    cache_candidates[page.url] = (page, result)

        deduped_pages = self._dedupe_pages(pages)
        if cacheable_results is not None:
            cacheable_results.extend(
                cache_candidates[page.url][1]
                for page in deduped_pages
                if page.url in cache_candidates
            )
        return final_url, deduped_pages

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
        from crawl4ai import AsyncWebCrawler, RateLimiter, SemaphoreDispatcher  # type: ignore

        if not website_urls:
            return {}

        browser_config = self._build_browser_config(enable_stealth=True)
        run_configs = [self._build_run_config(url) for url in website_urls]
        concurrency = max(1, int(max_concurrency))
        timeout_budget = max(
            self.CRAWL_TIMEOUT_SECONDS,
            float(getattr(self.settings, "nav_school_timeout_seconds", self.CRAWL_TIMEOUT_SECONDS))
            * max(1, ceil(len(website_urls) / concurrency)),
        )
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

        async with AsyncWebCrawler(config=browser_config) as crawler:
            containers = await asyncio.wait_for(
                crawler.arun_many(
                    urls=website_urls,
                    config=run_configs,
                    dispatcher=dispatcher,
                ),
                timeout=timeout_budget,
            )

        if not isinstance(containers, list):
            containers = [containers]

        outcomes: dict[str, BatchDiscoverOutcome] = {}
        for idx, url in enumerate(website_urls):
            container = containers[idx] if idx < len(containers) else None
            if container is None:
                outcomes[url] = BatchDiscoverOutcome(
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
                outcomes[url] = BatchDiscoverOutcome(
                    seed_url=url,
                    final_url=final_url,
                    pages=pages,
                    error=str(error_message) if error_message else None,
                )
            except Exception as exc:
                outcomes[url] = BatchDiscoverOutcome(
                    seed_url=url,
                    final_url=None,
                    pages=[],
                    error=str(exc),
                )

        return outcomes


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

    school_id = school.id
    original_scrape_status = school.scrape_status
    original_website_url = school.website_url
    now = datetime.now(timezone.utc)
    created = 0
    updated = 0
    contentful_pages = sum(
        navigator._is_extractable_page_content(page) for page in pages
    )
    definitive_removals = sum(
        page.status_code in navigator.DEFINITIVE_REMOVAL_STATUS_CODES
        for page in pages
    )
    cache_hits = sum(
        (page.cache_status or "").startswith("hit") for page in pages
    )
    material_change = False
    definitive_invalidated = 0

    if (
        navigator.bypass_cache
        and original_scrape_status in WEBSITE_PUBLISHABLE_STATUSES
        and contentful_pages == 0
        and definitive_removals == 0
    ):
        return {
            "school_id": school_id,
            "success": False,
            "reason": "No extractable page content",
            "pages_found": len(pages),
            "pages_with_content": 0,
            "created": 0,
            "updated": 0,
            "cache_hits": cache_hits,
            "invalidated": 0,
            "final_url": final_url,
            "material_change": False,
            "status_preserved": True,
        }

    candidates_result = await db.execute(
        select(SourcePage).where(
            SourcePage.school_id == school_id,
            SourcePage.scrape_type == ScrapeType.WEBSITE,
        )
    )
    website_source_pages = list(candidates_result.scalars().all())

    for page in pages:
        has_content = navigator._is_extractable_page_content(page)
        if (
            navigator.bypass_cache
            and original_scrape_status in WEBSITE_PUBLISHABLE_STATUSES
            and not has_content
            and page.status_code not in navigator.DEFINITIVE_REMOVAL_STATUS_CODES
        ):
            continue
        stored_markdown = page.markdown if has_content else None

        source_pages = [
            candidate
            for candidate in website_source_pages
            if navigator._is_same_page_url(candidate.source_url, page.url)
        ]

        if source_pages:
            for source_page in source_pages:
                was_valid = bool(source_page.is_valid)
                hash_changed = source_page.content_hash != page.content_hash
                content_changed = source_page.raw_markdown != stored_markdown
                category_changed = (
                    page.category is not None
                    and source_page.page_category != page.category
                )
                validity_changed = source_page.is_valid != has_content
                material_change = material_change or any(
                    (content_changed, category_changed, validity_changed)
                )
                if page.category is not None:
                    source_page.page_category = page.category
                source_page.raw_markdown = stored_markdown
                source_page.is_valid = has_content
                source_page.last_scraped_at = now
                source_page.scrape_count = (source_page.scrape_count or 0) + 1
                if hash_changed or content_changed:
                    source_page.content_hash = page.content_hash
                    source_page.last_changed_at = now
                if (
                    page.status_code in navigator.DEFINITIVE_REMOVAL_STATUS_CODES
                    and was_valid
                ):
                    definitive_invalidated += 1
                updated += 1
        else:
            material_change = material_change or has_content
            source_page = SourcePage(
                school_id=school_id,
                scrape_type=ScrapeType.WEBSITE,
                source_url=page.url,
                content_hash=page.content_hash,
                page_category=page.category,
                raw_markdown=stored_markdown,
                is_valid=has_content,
                last_scraped_at=now,
                last_changed_at=now,
                scrape_count=1,
            )
            db.add(source_page)
            website_source_pages.append(source_page)
            created += 1

    existing_pages_result = await db.execute(
        select(SourcePage).where(
            SourcePage.school_id == school_id,
            SourcePage.scrape_type == ScrapeType.WEBSITE,
        )
    )
    existing_pages = existing_pages_result.scalars().all()
    canonical_site_url = final_url or normalized_url

    invalidated = definitive_invalidated
    for existing_page in existing_pages:
        text = (existing_page.raw_markdown or "").strip()
        if not text:
            if existing_page.is_valid:
                existing_page.is_valid = False
                invalidated += 1
                material_change = True
            continue
        if not navigator._is_same_site_url(existing_page.source_url, canonical_site_url):
            existing_page.is_valid = False
            existing_page.raw_markdown = None
            invalidated += 1
            material_change = True
            continue
        if navigator._is_bot_challenge_url(existing_page.source_url) or navigator._is_bot_protection_content(text):
            existing_page.is_valid = False
            existing_page.raw_markdown = None
            invalidated += 1
            material_change = True

    if final_url and final_url != school.website_url:
        school.website_url = final_url
        material_change = material_change or not navigator._is_same_site_url(
            original_website_url, final_url
        )

    status_preserved = False
    if (
        original_scrape_status in WEBSITE_PUBLISHABLE_STATUSES
        and definitive_invalidated > 0
    ):
        school.scrape_status = "navigated"
    elif navigator.bypass_cache and original_scrape_status in WEBSITE_PUBLISHABLE_STATUSES:
        if material_change:
            school.scrape_status = "navigated"
        else:
            school.scrape_status = original_scrape_status
            status_preserved = True
    elif contentful_pages > 0:
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
            "material_change": material_change,
            "status_preserved": status_preserved,
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
        "material_change": material_change,
        "status_preserved": status_preserved,
    }


async def navigate_school(
    db: AsyncSession,
    school_id: int,
    country_code: str = "bg",
    *,
    bypass_cache: bool = False,
) -> dict[str, Any]:
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

    cache_alias_urls: list[str] = []
    if bypass_cache:
        source_urls_result = await db.execute(
            select(SourcePage.source_url).where(
                SourcePage.school_id == school_id,
                SourcePage.scrape_type == ScrapeType.WEBSITE,
            )
        )
        cache_alias_urls = list(source_urls_result.scalars().all())

    navigator = WebsiteNavigator(
        country_code=country_code,
        bypass_cache=bypass_cache,
        cache_alias_urls=cache_alias_urls,
    )
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
