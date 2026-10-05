"""Website discovery utilities for the scraping pipeline.

This stage runs before URL validation and aims to populate/normalize
``school.website_url`` using deterministic sources first, then a lightweight
web-search fallback when needed.
"""

from __future__ import annotations

import json
import logging
import pathlib
import re
import time
from datetime import datetime, timezone
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import CRAWLER_USER_AGENT, get_settings
from app.models import School, SourcePage, ScrapeType
from app.scrapers.base import BaseScraper
from app.scrapers.school_tokens import extract_school_name_tokens
from app.scrapers.url_validator import URLValidator, extract_validation_aliases

logger = logging.getLogger(__name__)


_URL_HINTS = ("website", "web", "url", "site", "domain")
_EMAIL_HINTS = ("email", "mail", "contact")
_WEBSITE_METADATA_SUFFIXES = ("_checked_at", "_method", "_reason")
_WEBSITE_METADATA_KEYS = {
    "url_validation_ambiguous",
}
_REGISTRY_SCHEMES = ("moe://", "kg://")
_REGISTRY_DOMAINS = ("ri-api.mon.bg", "kg.sofia.bg")


def _load_country_directory_domains(country_code: str) -> tuple[str, ...]:
    """Load country-specific directory domain blocklist from data/bg/directory_domains.json."""
    data_file = (
        pathlib.Path(__file__).parent.parent.parent  # backend/
        / "data" / country_code / "directory_domains.json"
    )
    try:
        return tuple(json.loads(data_file.read_text(encoding="utf-8")))
    except (FileNotFoundError, json.JSONDecodeError):
        return ()


_NOISY_DIRECTORY_DOMAINS = (
    "papagal.bg",
    "uchilishtata.bg",
    "maikomila.bg",
    "obrazovanie.maikomila.bg",
    "obrazovanieto.bg",
    "detskigradini.bg",
    "dz-priem.plovdiv.bg",
    "wikipedia.org",
) + _load_country_directory_domains("bg")
_DOCUMENT_EXTENSIONS = (
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".zip",
    ".rar",
    ".rtf",
)
_NOISY_PATH_SNIPPETS = (
    "/documents/",
    "/document/",
    "/wp-content/uploads/",
    "/asset_publisher/",
    "/spravochnik/",  # Bulgarian word for "reference directory"
    "/item/",         # registry/marketplace listing path (e.g. /item/school-name)
)

# BG-specific: transliterated and Cyrillic school-type terms that appear as path slugs
# in aggregator/directory URLs (e.g. finansi.bg/chastna-detska-gradina-NAME).
# A legitimate school site would never have its own school-type as a path segment.
_BG_SCHOOL_TYPE_PATH_PREFIXES = (
    "chastna-detska-gradina",
    "chastno-detska-gradina",
    "chastna-detska-yasla",
    "detska-gradina-",
    "chastno-uchilishte",
    "chastna-uchilishte",
    # Cyrillic equivalents (matched after URL-decoding)
    "частна-детска-градина",
    "частна детска градина",
    "детска-градина-",
    "частно-училище",
)
_FREE_EMAIL_DOMAINS = {
    "gmail.com",
    "abv.bg",
    "yahoo.com",
    "yahoo.bg",
    "outlook.com",
    "hotmail.com",
    "mail.bg",
}
_EMAIL_DOMAIN_HINTS = (
    "school",
    "kindergarten",
    "gradina",
    "academy",
    "lyceum",
    "gimnaz",
    "uchilish",
    "kids",
    "child",
)
_FREE_EMAIL_LOCALPART_STOPWORDS = {
    "office",
    "info",
    "contact",
    "admin",
    "mail",
    "email",
    "director",
    "directorate",
    "school",
    "kindergarten",
    "detska",
    "gradina",
    "kids",
    "team",
}
_NON_REUSABLE_CANDIDATE_REASON_MARKERS = (
    "not a school website",
    "expected school name not found",
    "weak expected-school name match",
    "website identity mismatch",
    "directory-like",
    "blocked domain",
)
_TRANSIENT_CANDIDATE_REASON_MARKERS = (
    "http error",
    "connection timeout",
    "validation error",
    "name or service not known",
    "temporary failure",
    "temporarily unavailable",
)
_TERMINAL_NO_WEBSITE_STATUSES = {"failed_validate", "extraction_failed"}
_SCHOOL_ABBREV_VARIANTS = {
    "дг": ("дг", "dg"),
    "чдг": ("чдг", "cdg", "dg"),
    "су": ("су", "su"),
    "оу": ("оу", "ou"),
    "пг": ("пг", "pg"),
}
_LEGAL_ENTITY_SUFFIXES = {
    "eood",
    "ood",
    "ead",
    "ad",
    "et",
    "eoood",
    "ооод",
    "еоод",
    "еад",
    "ад",
    "ет",
}
_DISCOVERY_LOCATION_TOKENS = {"софия", "sofia", "град", "grad", "city"}
_DISCOVERY_BRAND_STOPWORDS = {
    "частна",
    "частно",
    "частен",
    "начално",
    "основно",
    "средно",
    "езиково",
    "профилирана",
    "професионална",
    "английска",
    "детска",
    "градина",
    "училище",
    "гимназия",
    "общинска",
    "общински",
    "държавна",
    "държавно",
}
_GENERIC_DIRECTORY_HOST_SNIPPETS = (
    "spravochnik",
    "registar",
    "register",
    "directory",
    "catalog",
    "zadeteto",
    "uchiteli",
    "kartasofia",
    "info-register",
    "obrazovatel",
)
_GENERIC_DIRECTORY_PATH_SNIPPETS = (
    "/directory",
    "/listing",
    "/listings",
    "/catalog",
    "/firm",
    "/firms",
    "/company",
    "/companies",
    "/profile",
    "/spisuk-na-chastni",
    "/detski-gradini",
)
_SEARCH_ARTICLE_PATH_SNIPPETS = (
    "/news/",
    "/novini/",
    "/article/",
    "/articles/",
    "/story/",
    "/stories/",
    "/blog/",
    "/posts/",
    "/forum/",
    "/topic/",
    "/obshtestvo_",
)
_KNOWN_MEDIA_HOSTS = {
    "offnews.bg",
    "24chasa.bg",
    "capital.bg",
    "dnevnik.bg",
    "dir.bg",
    "dnes.bg",
    "actualno.com",
    "btvnovinite.bg",
    "nova.bg",
    "bntnews.bg",
    "bnr.bg",
    "mediapool.bg",
    "segabg.com",
    "standartnews.com",
}
_SOCIAL_HOSTS = {
    "facebook.com",
    "m.facebook.com",
    "instagram.com",
    "www.instagram.com",
    "youtube.com",
    "www.youtube.com",
    "linkedin.com",
    "www.linkedin.com",
    "x.com",
    "twitter.com",
    "tiktok.com",
    "www.tiktok.com",
}
_ADDRESS_LOCALITY_PATTERNS = (
    re.compile(r"(?:ж\.\s*к\.|жк|жилищен комплекс|кв\.)\s*\"?([A-Za-zА-Яа-я0-9][A-Za-zА-Яа-я0-9 .'\-–]+)\"?"),
    re.compile(r"(?:район)\s+\"?([A-Za-zА-Яа-я0-9][A-Za-zА-Яа-я0-9 .'\-–]+)\"?"),
)
_ADDRESS_STREET_PATTERN = re.compile(r"(?:ул\.|бул\.|пл\.)\s*\"?([A-Za-zА-Яа-я0-9][A-Za-zА-Яа-я0-9 .'\-–]+)\"?")
_LOCALITY_HINT_STOPWORDS = {
    "софия",
    "sofia",
    "район",
    "жк",
    "ж.к",
    "кв",
    "ул",
    "бул",
    "пл",
}


class WebsiteDiscoverer:
    """Find likely official website URLs for schools."""

    HTTP_TIMEOUT = 6.0
    MAX_SEARCH_RESULTS = 5
    SEARXNG_ENDPOINT_PATH = "/search"
    DUCKDUCKGO_ENDPOINT = "https://duckduckgo.com/html/"
    BRAVE_ENDPOINT = "https://search.brave.com/search"
    _DISABLED_UNTIL_MONOTONIC: dict[str, float | None] = {}
    SUPPORTED_PROVIDERS = ("searxng", "brave", "duckduckgo")
    SEARCH_HEADERS = {
        # Brave can return unsupported brotli variants; identity is safest for parser mode.
        "Accept-Encoding": "identity",
        "User-Agent": CRAWLER_USER_AGENT,
    }

    def __init__(self, country_code: str = "bg"):
        self.country_code = country_code
        self.validator = URLValidator(country_code=country_code)
        settings = get_settings()
        self.search_providers = self._parse_providers(settings.website_search_providers)
        self.searxng_base_url = (settings.searxng_base_url or "").rstrip("/")
        self.search_timeout = max(1.0, float(settings.website_search_timeout_seconds))
        self.provider_disable_seconds = max(0, int(settings.website_search_provider_disable_seconds))

    async def discover(
        self,
        db: AsyncSession,
        school: School,
        use_search_fallback: bool = True,
    ) -> dict:
        """Discover a school website URL and persist it when found."""
        existing_url = school.website_url
        resolved_url = None
        method = None
        seen: set[str] = set()
        searched: list[str] = []  # Track raw search URLs for caller reuse

        is_failed_retry = school.scrape_status == "failed_validate"
        can_mark_no_website = school.scrape_status in _TERMINAL_NO_WEBSITE_STATUSES
        existing_normalized = self.validator.normalize_url(existing_url or "")
        school_aliases = self._validation_aliases_for_school(school)
        discovery_hints = self._discovery_hints_for_school(school)

        # For failed URLs, force a fresh discovery attempt.
        if is_failed_retry and existing_normalized:
            seen.add(existing_normalized)

        if is_failed_retry:
            # 1) Fresh web search first
            if use_search_fallback:
                searched = await self._search_candidates(school)
                search_candidates = [(url, "search") for url in searched]
                resolved_url, method = self._pick_best_candidate(
                    search_candidates,
                    seen,
                    school_name=(school.name_i18n or {}).get("bg") or (school.name_i18n or {}).get("en"),
                    school_aliases=school_aliases,
                    discovery_hints=discovery_hints,
                )

            # 2) Deterministic non-existing candidates only
            if not resolved_url:
                candidates = self._collect_candidates(school, include_existing=False)
                resolved_url, method = self._pick_best_candidate(
                    candidates,
                    seen,
                    school_name=(school.name_i18n or {}).get("bg") or (school.name_i18n or {}).get("en"),
                    school_aliases=school_aliases,
                    discovery_hints=discovery_hints,
                )
        else:
            # Try deterministic candidates first (existing fields/attributes).
            candidates = self._collect_candidates(school)
            resolved_url, method = self._pick_best_candidate(
                candidates,
                seen,
                school_name=(school.name_i18n or {}).get("bg") or (school.name_i18n or {}).get("en"),
                school_aliases=school_aliases,
                discovery_hints=discovery_hints,
            )

            # Fall back to web search before guessed brand domains.
            if not resolved_url and use_search_fallback:
                searched = await self._search_candidates(school)
                search_candidates = [(url, "search") for url in searched]
                resolved_url, method = self._pick_best_candidate(
                    search_candidates,
                    seen,
                    school_name=(school.name_i18n or {}).get("bg") or (school.name_i18n or {}).get("en"),
                    school_aliases=school_aliases,
                    discovery_hints=discovery_hints,
                )

        if not resolved_url:
            if can_mark_no_website:
                school.scrape_status = "no_official_website"
                school.website_url = None
                await db.commit()
            return {
                "school_id": school.id,
                "found": False,
                "updated": False,
                "reason": "No candidate website found",
                "candidates_checked": len(seen),
                "terminal_status": "no_official_website" if can_mark_no_website else None,
                "search_candidates": searched,
            }

        updated = existing_url != resolved_url
        school.attributes = dict(school.attributes or {})
        school.attributes["website_candidate_url"] = resolved_url
        school.attributes["website_candidate_method"] = method or "unknown"
        school.attributes["website_candidate_checked_at"] = datetime.now(timezone.utc).isoformat()
        if updated:
            school.website_url = resolved_url
            # Any non-terminal failure state should re-enter validation with a fresh candidate.
            if school.scrape_status in {"failed_validate", "extraction_failed"}:
                school.scrape_status = "pending"

        await self._upsert_discovery_source_page(db, school.id, resolved_url, method or "unknown")
        await db.commit()

        return {
            "school_id": school.id,
            "found": True,
            "updated": updated,
            "website_url": resolved_url,
            "method": method,
            "candidates_checked": len(seen),
            "search_candidates": searched,
        }

    async def recover_failed_school(
        self,
        db: AsyncSession,
        school: School,
        max_attempts: int,
    ) -> dict:
        """Rediscover + revalidate URL for one failed school."""
        from app.scrapers.url_validator import ValidationResult, validate_school_url

        if school.scrape_status != "failed_validate":
            return {"school_id": school.id, "skipped": True, "reason": "not_failed_validate"}

        school_name = (school.name_i18n or {}).get("bg") or (school.name_i18n or {}).get("en")
        school_aliases = self._validation_aliases_for_school(school)
        discovery_hints = self._discovery_hints_for_school(school)
        max_attempts = max(1, int(max_attempts))

        discovery = await self.discover(
            db=db,
            school=school,
            use_search_fallback=True,
        )
        await db.refresh(school)

        if school.scrape_status == "no_official_website":
            return {"school_id": school.id, "terminal": True, "status": "no_official_website"}

        if not school.website_url:
            return {"school_id": school.id, "skipped": True, "reason": "no_website_url_after_discovery"}

        validation_result, _, reason = await validate_school_url(
            school_id=school.id,
            url=school.website_url,
            country_code=self.country_code,
            update_db=True,
            school_name=school_name,
            school_aliases=extract_validation_aliases(school.attributes),
        )
        await db.refresh(school)

        attempts = 1
        attempted_candidates: set[str] = set()
        normalized_current = self.validator.normalize_url(school.website_url or "")
        if normalized_current:
            attempted_candidates.add(normalized_current)

        if validation_result != ValidationResult.VALID:
            # Reuse the search candidates already fetched by discover() rather than
            # issuing a second search.  The provider may now be rate-limited (e.g.
            # Brave 429), so a fresh search would return nothing and the remaining
            # valid candidates from the first batch would be silently lost.
            candidate_pool: list[tuple[str, str]] = [
                (url, "search") for url in (discovery.get("search_candidates") or [])
            ]
            candidate_pool.extend(self._collect_candidates(school, include_existing=False))

            while attempts < max_attempts:
                # Pass a COPY of attempted_candidates so _pick_best_candidate doesn't
                # mark the entire pool as seen in one pass (which would leave nothing
                # for the next iteration).  Only the chosen URL is added back.
                seen_snapshot = set(attempted_candidates)
                next_url, _ = self._pick_best_candidate(
                    candidate_pool,
                    seen_snapshot,
                    school_name=school_name,
                    school_aliases=school_aliases,
                    discovery_hints=discovery_hints,
                )
                if not next_url:
                    break
                attempted_candidates.add(next_url)

                school.website_url = next_url
                school.scrape_status = "pending"
                await db.commit()
                await db.refresh(school)

                validation_result, _, reason = await validate_school_url(
                    school_id=school.id,
                    url=next_url,
                    country_code=self.country_code,
                    update_db=True,
                    school_name=school_name,
                    school_aliases=extract_validation_aliases(school.attributes),
                )
                attempts += 1
                await db.refresh(school)

                if validation_result == ValidationResult.VALID:
                    break

        if validation_result != ValidationResult.VALID and school.scrape_status == "pending":
            school.scrape_status = "failed_validate"
            await db.commit()
            await db.refresh(school)

        return {
            "school_id": school.id,
            "discovery_found": discovery.get("found", False),
            "discovery_updated": discovery.get("updated", False),
            "validation_result": validation_result.value,
            "status": school.scrape_status,
            "reason": reason,
            "attempts": attempts,
        }

    def _pick_best_candidate(
        self,
        candidates: list[tuple[str, str]],
        seen: set[str],
        school_name: str | None = None,
        school_aliases: list[str] | None = None,
        discovery_hints: list[str] | None = None,
    ) -> tuple[str | None, str | None]:
        """Return best acceptable normalized URL from candidate list."""
        accepted: list[tuple[str, str, int]] = []
        expected_names = self.validator._expected_school_names(school_name, school_aliases)
        school_tokens = self.validator._extract_expected_name_tokens(expected_names)
        hint_tokens = self._extract_discovery_hint_tokens(discovery_hints, school_tokens)
        host_frequency = self._build_host_frequency(candidates)

        for raw_url, source in candidates:
            normalized = self.validator.normalize_url(raw_url)
            if not normalized:
                continue
            # If original URL is already clearly non-official, don't expand to root.
            if self._is_non_official_candidate(normalized):
                continue
            expanded = [normalized]
            if source == "search":
                root = self._to_site_root(normalized)
                if root and root != normalized and self._should_promote_search_root(
                    normalized,
                    school_tokens,
                    hint_tokens,
                ):
                    expanded.insert(0, root)

            for candidate_url in expanded:
                if candidate_url in seen:
                    continue
                seen.add(candidate_url)

                if self.validator._is_blocked_domain(candidate_url):
                    continue
                if self._is_registry_url(candidate_url):
                    continue
                if self._is_non_official_candidate(candidate_url):
                    continue
                if source == "search" and self._is_non_viable_search_candidate(
                    candidate_url,
                    school_tokens,
                    hint_tokens,
                ):
                    continue

                score = self._score_candidate(
                    candidate_url,
                    source,
                    school_tokens,
                    host_frequency,
                    hint_tokens=hint_tokens,
                )
                accepted.append((candidate_url, source, score))

        if not accepted:
            return None, None

        # Pick highest score; tie-break by shortest URL for deterministic behavior.
        accepted.sort(key=lambda item: (item[2], -len(item[0])), reverse=True)
        best_url, best_source, _ = accepted[0]
        return best_url, best_source

    def _collect_candidates(
        self,
        school: School,
        include_existing: bool = True,
    ) -> list[tuple[str, str]]:
        """Collect URL candidates from school fields and attributes."""
        candidates: list[tuple[str, str]] = []
        school_name = (school.name_i18n or {}).get("bg") or (school.name_i18n or {}).get("en")
        school_tokens = self._extract_school_name_tokens(school_name)

        if include_existing and school.website_url:
            candidates.append((school.website_url, "existing"))
        if school.source_url and not self._is_registry_url(school.source_url):
            candidates.append((school.source_url, "source_url"))

        if isinstance(school.attributes, dict):
            self._collect_from_attributes(school.attributes, candidates, school_tokens)
        return candidates

    def _collect_from_attributes(
        self,
        payload: dict | list | str,
        candidates: list[tuple[str, str]],
        school_tokens: list[str],
    ) -> None:
        """Recursively collect URL candidates from JSON-like attributes."""
        if isinstance(payload, dict):
            for key, value in payload.items():
                key_str = str(key).lower()
                if isinstance(value, str):
                    if key_str in _WEBSITE_METADATA_KEYS:
                        continue
                    if key_str.endswith(_WEBSITE_METADATA_SUFFIXES):
                        continue
                    if key_str == "website_candidate_url":
                        continue
                    if any(hint in key_str for hint in _URL_HINTS) and self._looks_like_url_candidate(value):
                        candidates.append((value, f"attributes:{key_str}"))
                    elif any(hint in key_str for hint in _EMAIL_HINTS):
                        email_domain = self._domain_from_email(value)
                        if email_domain and self._is_school_aligned_email_domain(email_domain, school_tokens):
                            candidates.append((email_domain, f"email_domain:{key_str}"))
                else:
                    self._collect_from_attributes(value, candidates, school_tokens)
            return

        if isinstance(payload, list):
            for item in payload:
                self._collect_from_attributes(item, candidates, school_tokens)

    def _looks_like_url_candidate(self, value: str) -> bool:
        """Ignore metadata strings that happen to live under website-ish keys."""
        text = (value or "").strip()
        if not text:
            return False
        normalized = self.validator.normalize_url(text)
        if normalized:
            parsed = urlparse(normalized)
            hostname = (parsed.hostname or "").lower()
            if not hostname:
                return False
            if hostname == "localhost":
                return True
            if "." not in hostname:
                return False
            if not re.fullmatch(r"[a-z0-9.-]+", hostname):
                return False
            tld = hostname.rsplit(".", 1)[-1]
            return len(tld) >= 2 and tld.isalpha()
        lowered = text.lower()
        if any(marker in lowered for marker in ("http://", "https://", "www.")):
            return True
        if " " in text or ":" in text or "@" in text:
            return False
        return "." in text

    async def _search_candidates(self, school: School) -> list[str]:
        """Search for likely official website URLs using strict provider fallback order."""
        school_name = (school.name_i18n or {}).get("bg") or (school.name_i18n or {}).get("en")
        if not school_name:
            return []

        school_aliases = self._validation_aliases_for_school(school)
        discovery_hints = self._discovery_hints_for_school(school)
        queries = self._build_search_queries(
            school_name=school_name,
            city=school.city or "",
            school_aliases=school_aliases,
            discovery_hints=discovery_hints,
            locality_hints=self._location_search_hints_for_school(school),
        )
        if not queries:
            return []

        # Keep some headroom so simplified query variants can contribute candidates.
        max_collected = self.MAX_SEARCH_RESULTS * max(1, len(queries)) * 2

        for provider in self.search_providers:
            provider_candidates: list[str] = []
            provider_seen: set[str] = set()
            for query in queries:
                links = await self._search_provider(provider=provider, query=query, school_id=school.id)
                for link in links:
                    if not link or link in provider_seen:
                        continue
                    provider_seen.add(link)
                    provider_candidates.append(link)
                    if len(provider_candidates) >= max_collected:
                        break
                if len(provider_candidates) >= max_collected:
                    break

            if not provider_candidates:
                continue

            # True fallback: stop on the first provider that yields an acceptable candidate.
            probe_best, _ = self._pick_best_candidate(
                [(url, "search") for url in provider_candidates],
                seen=set(),
                school_name=school_name,
                school_aliases=school_aliases,
                discovery_hints=discovery_hints,
            )
            if probe_best:
                return provider_candidates

        return []

    async def _search_provider(self, provider: str, query: str, school_id: int) -> list[str]:
        """Execute one search query against a single provider when enabled."""
        if provider == "searxng":
            if self._is_provider_disabled("searxng"):
                return []
            return await self._search_searxng(query=query, school_id=school_id)
        if provider == "brave":
            if self._is_provider_disabled("brave"):
                return []
            return await self._search_brave(query=query, school_id=school_id)
        if provider == "duckduckgo":
            if self._is_provider_disabled("duckduckgo"):
                return []
            return await self._search_duckduckgo(query=query, school_id=school_id)
        return []

    @classmethod
    def reset_provider_state(cls) -> None:
        """Reset provider circuit-breaker state."""
        cls._DISABLED_UNTIL_MONOTONIC.clear()

    def _disable_provider(self, provider: str, reason: str) -> None:
        """Disable one provider temporarily (or permanently when timeout=0)."""
        if provider not in self.SUPPORTED_PROVIDERS:
            return
        if self.provider_disable_seconds > 0:
            WebsiteDiscoverer._DISABLED_UNTIL_MONOTONIC[provider] = (
                time.monotonic() + self.provider_disable_seconds
            )
            logger.warning(
                "%s search disabled for %ss: %s",
                provider.capitalize(),
                self.provider_disable_seconds,
                reason,
            )
            return

        WebsiteDiscoverer._DISABLED_UNTIL_MONOTONIC[provider] = None
        logger.warning("%s search disabled: %s", provider.capitalize(), reason)

    def _is_provider_disabled(self, provider: str) -> bool:
        """Check if a provider is currently disabled, auto-resetting after cooldown."""
        if provider not in self.SUPPORTED_PROVIDERS:
            return False

        if provider not in WebsiteDiscoverer._DISABLED_UNTIL_MONOTONIC:
            return False

        disabled_until = WebsiteDiscoverer._DISABLED_UNTIL_MONOTONIC[provider]
        if disabled_until is None:
            return True

        if time.monotonic() < disabled_until:
            return True

        WebsiteDiscoverer._DISABLED_UNTIL_MONOTONIC.pop(provider, None)
        logger.info("%s provider re-enabled after cooldown", provider)
        return False

    def _build_search_queries(
        self,
        school_name: str,
        city: str,
        school_aliases: list[str] | None = None,
        discovery_hints: list[str] | None = None,
        locality_hints: list[str] | None = None,
    ) -> list[str]:
        """Build de-duplicated search queries with targeted and broad fallback variants."""
        suffix = "официален сайт" if self.country_code == "bg" else "official website"
        city_value = " ".join((city or "").split())
        candidates = self._candidate_school_names_for_search(school_name, school_aliases, discovery_hints)

        queries: list[str] = []
        seen: set[str] = set()
        max_queries = 8
        locality_query_budget = max(1, max_queries // 2)
        locality_queries_added = 0

        def add_query(parts: list[str]) -> tuple[bool, bool]:
            query = " ".join(part for part in parts if part)
            query = " ".join(query.split())
            if not query:
                return False, False
            lowered = query.lower()
            if lowered in seen:
                return False, False
            seen.add(lowered)
            queries.append(query)
            return True, len(queries) >= max_queries

        # First pass: strongest intent signal with locality hints from the registered address.
        for locality_hint in locality_hints or []:
            if locality_queries_added >= locality_query_budget:
                break
            cleaned_hint = " ".join((locality_hint or "").split())
            if not cleaned_hint:
                continue
            for candidate_name in candidates:
                cleaned_name = " ".join((candidate_name or "").split())
                if not cleaned_name:
                    continue
                added, limit_reached = add_query([cleaned_name, cleaned_hint, suffix])
                if added:
                    locality_queries_added += 1
                if limit_reached:
                    break
                if locality_queries_added >= locality_query_budget:
                    break
            if len(queries) >= max_queries:
                break

        # Second pass: city-wide intent signal.
        for candidate_name in candidates:
            cleaned_name = " ".join((candidate_name or "").split())
            if not cleaned_name:
                continue

            _, limit_reached = add_query([cleaned_name, city_value, suffix])
            if limit_reached:
                break

        # Third pass: broader variants to recover cases where city/suffix hurts ranking.
        if len(queries) < max_queries:
            for candidate_name in candidates:
                cleaned_name = " ".join((candidate_name or "").split())
                if not cleaned_name:
                    continue
                _, limit_reached = add_query([cleaned_name])
                if limit_reached:
                    break

        return queries

    def _location_search_hints_for_school(self, school: School) -> list[str]:
        """Extract neighborhood/street hints from the primary location address."""
        locations = list(getattr(school, "locations", None) or [])
        if not locations:
            return []

        primary = sorted(locations, key=lambda loc: (not bool(getattr(loc, "is_primary", False)), getattr(loc, "id", 0)))[0]
        address_i18n = getattr(primary, "address_i18n", None) or {}
        candidates = [
            address_i18n.get("bg"),
            address_i18n.get("en"),
            getattr(primary, "district", None),
        ]

        hints: list[str] = []
        seen: set[str] = set()

        def add_hint(value: str | None) -> None:
            text = " ".join((value or "").replace("–", "-").split()).strip(" ,.-\"'")
            if not text:
                return
            key = text.casefold()
            if key in seen:
                return
            seen.add(key)
            hints.append(text)

        for raw_value in candidates:
            value = str(raw_value or "")
            for pattern in _ADDRESS_LOCALITY_PATTERNS:
                for match in pattern.finditer(value):
                    locality = match.group(1)
                    if self._looks_like_locality_hint(locality):
                        add_hint(locality)

            street_match = _ADDRESS_STREET_PATTERN.search(value)
            if street_match:
                street = street_match.group(1)
                if self._looks_like_locality_hint(street):
                    add_hint(street)

        return hints[:3]

    def _looks_like_locality_hint(self, value: str | None) -> bool:
        text = " ".join((value or "").replace("–", "-").split()).strip(" ,.-\"'")
        if len(text) < 3:
            return False
        lowered = text.casefold()
        if lowered in _LOCALITY_HINT_STOPWORDS:
            return False
        if lowered.isdigit():
            return False
        return any(ch.isalpha() for ch in text)

    def _candidate_school_names_for_search(
        self,
        school_name: str,
        school_aliases: list[str] | None = None,
        discovery_hints: list[str] | None = None,
    ) -> list[str]:
        """Generate school-name variants that improve search recall."""
        variants: list[str] = []
        seen: set[str] = set()
        raw_candidates = [*(school_aliases or []), school_name, *(discovery_hints or [])]
        for raw_name in raw_candidates:
            cleaned = self._sanitize_school_name_for_search(raw_name)
            simplified = self._simplify_school_name(cleaned)
            stripped_legal = self._strip_legal_suffixes(simplified)
            token_focus = " ".join(self._extract_school_name_tokens(stripped_legal)[:3])

            for candidate in [cleaned, simplified, stripped_legal, token_focus]:
                normalized = " ".join((candidate or "").split())
                if not normalized:
                    continue
                lowered = normalized.lower()
                if lowered in seen:
                    continue
                seen.add(lowered)
                variants.append(normalized)

        return variants

    def _validation_aliases_for_school(self, school: School) -> list[str]:
        """Return known aliases that help discovery/validation stay brand-aware."""
        aliases = extract_validation_aliases(school.attributes)
        moe_abbreviation = None
        if isinstance(school.attributes, dict):
            moe_abbreviation = school.attributes.get("moe_abbreviation")
        if isinstance(moe_abbreviation, str) and moe_abbreviation.strip():
            if all(moe_abbreviation.casefold() != alias.casefold() for alias in aliases):
                aliases.append(moe_abbreviation.strip())
        return aliases

    def _sanitize_school_name_for_search(self, school_name: str) -> str:
        """Remove quote punctuation that reduces search result quality."""
        text = school_name or ""
        text = re.sub(r"[\"'«»„“”]", " ", text)
        return " ".join(text.split())

    def _strip_legal_suffixes(self, school_name: str) -> str:
        """Drop trailing legal entity suffixes (e.g. OOD/EOOD) from search names."""
        tokens = [token for token in (school_name or "").split() if token]
        while tokens:
            token = tokens[-1].strip(".,() ").lower()
            if token not in _LEGAL_ENTITY_SUFFIXES:
                break
            tokens.pop()
        return " ".join(tokens)

    def _simplify_school_name(self, school_name: str) -> str:
        """Remove parenthetical descriptors that often hurt search relevance."""
        simplified = re.sub(r"\([^)]*\)", " ", school_name or "")
        simplified = re.sub(r"\[[^\]]*\]", " ", simplified)
        return " ".join(simplified.split())

    def _is_root_candidate(self, url: str) -> bool:
        """Return True when URL points to website root/home page."""
        parsed = urlparse(url)
        path = (parsed.path or "").strip().lower()
        if path in {"", "/"}:
            return True
        return path.rstrip("/") in {"/index", "/index.html", "/index.htm", "/home", "/home.html"}

    def _to_site_root(self, url: str) -> str | None:
        """Return canonical site-root URL for a candidate link."""
        normalized = self.validator.normalize_url(url)
        if not normalized:
            return None
        parsed = urlparse(normalized)
        if not parsed.netloc:
            return None
        return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")

    def _should_promote_search_root(
        self,
        url: str,
        school_tokens: list[str],
        hint_tokens: list[str] | None = None,
    ) -> bool:
        """Promote search-result roots only when the result already looks school-aligned."""
        return self._school_like_signal_count(url, school_tokens, hint_tokens) > 0

    def _is_non_viable_search_candidate(
        self,
        url: str,
        school_tokens: list[str],
        hint_tokens: list[str] | None = None,
    ) -> bool:
        """Reject search hits that are clearly media/social mentions rather than school sites."""
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        if host.startswith("www."):
            host = host[4:]

        signal_hits = self._school_like_signal_count(url, school_tokens, hint_tokens)
        if host in _SOCIAL_HOSTS:
            return True
        if host in _KNOWN_MEDIA_HOSTS and signal_hits == 0:
            return True
        if self._is_article_like_search_result(url) and signal_hits == 0:
            return True
        return False

    def _is_article_like_search_result(self, url: str) -> bool:
        """Detect article/story/forum URLs that should not become school websites."""
        parsed = urlparse(url)
        path = (parsed.path or "").lower()
        if any(snippet in path for snippet in _SEARCH_ARTICLE_PATH_SNIPPETS):
            return True
        segments = [segment for segment in path.split("/") if segment]
        if len(segments) >= 3:
            return True
        if re.search(r"/20\d{2}/\d{2}/", path):
            return True
        return False

    def _school_like_signal_count(
        self,
        url: str,
        school_tokens: list[str],
        hint_tokens: list[str] | None = None,
    ) -> int:
        """Count school/hint token matches present in a candidate URL."""
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        haystack = f"{host}{(parsed.path or '').lower()}{(parsed.query or '').lower()}"
        hits = 0

        for token in school_tokens:
            variants = [variant for variant in self._token_variants(token) if variant and len(variant) >= 2]
            if any(variant in haystack for variant in variants):
                hits += 1

        for token in hint_tokens or []:
            normalized = token.lower()
            if normalized and normalized in haystack:
                hits += 1

        return hits

    def _extract_school_name_tokens(self, school_name: str | None) -> list[str]:
        """Extract meaningful tokens from school name for candidate scoring."""
        return extract_school_name_tokens(school_name, limit=8)

    def _token_variants(self, token: str) -> tuple[str, ...]:
        """Return token variants used for host/path matching."""
        if token in _SCHOOL_ABBREV_VARIANTS:
            return _SCHOOL_ABBREV_VARIANTS[token]
        return (token, self._transliterate_bg(token))

    def _transliterate_bg(self, text: str) -> str:
        """Lightweight Cyrillic->Latin transliteration for URL matching."""
        mapping = {
            "а": "a", "б": "b", "в": "v", "г": "g", "д": "d",
            "е": "e", "ж": "zh", "з": "z", "и": "i", "й": "y",
            "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
            "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
            "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh",
            "щ": "sht", "ъ": "a", "ь": "", "ю": "yu", "я": "ya",
        }
        return "".join(mapping.get(ch, ch) for ch in text.lower())

    def _score_candidate(
        self,
        url: str,
        source: str,
        school_tokens: list[str],
        host_frequency: dict[str, int],
        hint_tokens: list[str] | None = None,
    ) -> int:
        """Rank candidate URLs so official-looking domains beat list directories."""
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        path = (parsed.path or "").lower()
        query = (parsed.query or "").lower()
        haystack = f"{host}{path}{query}"

        score = 0
        if source == "existing":
            score += 20
        elif source.startswith("email_domain:"):
            score += 30
        elif source.startswith("attributes:"):
            score += 15
        elif source == "search":
            score += 5
        if self._is_root_candidate(url):
            score += 25

        host_hits = 0
        path_hits = 0
        for token in school_tokens:
            variants = [v for v in self._token_variants(token) if v and len(v) >= 2]
            if any(variant in host for variant in variants):
                host_hits += 1
            elif any(variant in (path + query) for variant in variants):
                path_hits += 1
        score += host_hits * 18 + path_hits * 6
        if hint_tokens:
            hint_host_hits = 0
            hint_path_hits = 0
            for token in hint_tokens:
                if token in host:
                    hint_host_hits += 1
                elif token in (path + query):
                    hint_path_hits += 1
            score += hint_host_hits * 14 + hint_path_hits * 4
        score += max(0, host_frequency.get(host, 0) - 1) * 8

        if source == "search" and host_hits == 0 and path_hits == 0:
            score -= 8
        if self._is_search_directory_candidate(url):
            score -= 45
        if self._is_likely_directory_host(host):
            score -= 25
        return score

    def _discovery_hints_for_school(self, school: School) -> list[str]:
        """Extract non-authoritative hints that help search and ranking."""
        school_name = (school.name_i18n or {}).get("bg") or (school.name_i18n or {}).get("en")
        school_tokens = self._extract_school_name_tokens(school_name)
        hints: list[str] = []
        seen: set[str] = set()

        def add_hint(value: str) -> None:
            normalized = " ".join((value or "").split())
            if not normalized:
                return
            key = normalized.casefold()
            if key in seen:
                return
            seen.add(key)
            hints.append(normalized)

        for email in self._collect_email_values(school.attributes or {}):
            for hint in self._email_local_part_hints(email, school_tokens):
                add_hint(hint)

        return hints

    def _collect_email_values(self, payload: dict | list | str) -> list[str]:
        """Collect email strings from nested attribute payloads."""
        emails: list[str] = []

        def walk(value: dict | list | str) -> None:
            if isinstance(value, str):
                text = value.strip()
                if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", text):
                    emails.append(text)
                return
            if isinstance(value, dict):
                for nested in value.values():
                    walk(nested)
                return
            if isinstance(value, list):
                for nested in value:
                    walk(nested)

        walk(payload)
        return emails

    def _email_local_part_hints(self, email: str, school_tokens: list[str]) -> list[str]:
        """Extract brand-like hints from free-email local parts such as prolet_2006."""
        value = (email or "").strip().lower()
        if "@" not in value:
            return []

        local_part, domain = value.split("@", 1)
        if domain not in _FREE_EMAIL_DOMAINS:
            return []

        normalized_local = re.sub(r"[^a-z0-9]+", " ", self._transliterate_bg(local_part))
        tokens = re.findall(r"[a-z0-9]+", normalized_local)
        if not tokens:
            return []

        school_variants = {
            variant
            for token in school_tokens
            for variant in self._token_variants(token)
            if variant and len(variant) >= 3
        }
        filtered: list[str] = []
        has_school_aligned_alpha = False
        for token in tokens:
            if token in _FREE_EMAIL_LOCALPART_STOPWORDS:
                continue
            if token.isdigit():
                if len(token) == 4:
                    filtered.append(token)
                continue
            if len(token) < 3:
                continue
            if any(token in variant or variant in token for variant in school_variants):
                has_school_aligned_alpha = True
            filtered.append(token)

        if not has_school_aligned_alpha:
            return []

        alpha_tokens = [token for token in filtered if not token.isdigit()]
        digit_tokens = [token for token in filtered if token.isdigit()]
        if not alpha_tokens:
            return []

        hints: list[str] = []
        hints.append(" ".join(alpha_tokens[:2]))
        if digit_tokens:
            hints.append(" ".join([*alpha_tokens[:2], digit_tokens[0]]))
            hints.append("".join([*alpha_tokens[:2], digit_tokens[0]]))
        return hints

    def _extract_discovery_hint_tokens(
        self,
        discovery_hints: list[str] | None,
        school_tokens: list[str],
    ) -> list[str]:
        """Convert search/ranking hints into distinctive host/path tokens."""
        tokens: list[str] = []
        seen: set[str] = set(school_tokens)
        for hint in discovery_hints or []:
            for token in re.findall(r"[a-z0-9]+", self._transliterate_bg(hint or "")):
                if token in seen:
                    continue
                if token.isdigit():
                    if len(token) != 4:
                        continue
                elif len(token) < 3:
                    continue
                seen.add(token)
                tokens.append(token)
        return tokens

    def _build_host_frequency(self, candidates: list[tuple[str, str]]) -> dict[str, int]:
        """Count how often candidate hosts appear (repeated hosts are stronger signals)."""
        frequency: dict[str, int] = {}
        for raw_url, _ in candidates:
            normalized = self.validator.normalize_url(raw_url)
            if not normalized:
                continue
            host = urlparse(normalized).netloc.lower()
            if host.startswith("www."):
                host = host[4:]
            if not host:
                continue
            frequency[host] = frequency.get(host, 0) + 1
        return frequency

    def _is_search_directory_candidate(self, url: str) -> bool:
        """Detect list/directory-like URLs using generic path snippets."""
        parsed = urlparse(url)
        path = (parsed.path or "").lower()
        if any(snippet in path for snippet in _GENERIC_DIRECTORY_PATH_SNIPPETS):
            return True
        return False

    def _is_likely_directory_host(self, host: str) -> bool:
        """Detect likely index/listing hosts without hardcoding full domains."""
        lowered = (host or "").lower()
        if any(snippet in lowered for snippet in _GENERIC_DIRECTORY_HOST_SNIPPETS):
            return True
        if "detski" in lowered and ("gradin" in lowered or "yasl" in lowered):
            return True
        return False

    def _parse_providers(self, raw: str) -> list[str]:
        """Parse configured provider order from comma-separated settings."""
        providers = [item.strip().lower() for item in (raw or "").split(",") if item.strip()]
        ordered = [item for item in providers if item in self.SUPPORTED_PROVIDERS]
        return ordered or ["searxng", "brave"]

    async def _search_searxng(self, query: str, school_id: int) -> list[str]:
        """Query SearXNG JSON API."""
        if not self.searxng_base_url:
            return []

        endpoint = f"{self.searxng_base_url}{self.SEARXNG_ENDPOINT_PATH}"
        try:
            async with httpx.AsyncClient(
                timeout=self.search_timeout,
                follow_redirects=True,
                headers=self.SEARCH_HEADERS,
            ) as client:
                response = await client.get(
                    endpoint,
                    params={
                        "q": query,
                        "format": "json",
                        "language": "bg-BG" if self.country_code == "bg" else "en-US",
                    },
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {401, 403, 404, 429}:
                self._disable_provider("searxng", f"HTTP {exc.response.status_code}")
            else:
                logger.warning(
                    "SearXNG search failed for school %s (%s): %s",
                    school_id,
                    type(exc).__name__,
                    exc,
                )
            return []
        except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError) as exc:
            self._disable_provider("searxng", f"{type(exc).__name__}: {exc}")
            return []
        except Exception as exc:
            logger.warning(
                "SearXNG search failed for school %s (%s): %s",
                school_id,
                type(exc).__name__,
                exc,
            )
            return []

        payload = response.json()
        if not isinstance(payload, dict):
            return []
        results = payload.get("results")
        if not isinstance(results, list):
            return []

        links: list[str] = []
        seen: set[str] = set()
        for result in results:
            if not isinstance(result, dict):
                continue
            raw_url = result.get("url")
            if not isinstance(raw_url, str):
                continue
            cleaned = self._canonicalize_search_url(raw_url)
            if not cleaned or cleaned in seen:
                continue
            seen.add(cleaned)
            links.append(cleaned)
            if len(links) >= self.MAX_SEARCH_RESULTS:
                break
        return links

    async def _search_duckduckgo(self, query: str, school_id: int) -> list[str]:
        """Query DuckDuckGo HTML endpoint."""
        try:
            async with httpx.AsyncClient(
                timeout=self.search_timeout,
                follow_redirects=True,
                headers=self.SEARCH_HEADERS,
            ) as client:
                response = await client.get(self.DUCKDUCKGO_ENDPOINT, params={"q": query})
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {401, 403, 429}:
                self._disable_provider("duckduckgo", f"HTTP {exc.response.status_code}")
            else:
                logger.warning(
                    "DuckDuckGo search failed for school %s (%s): %s",
                    school_id,
                    type(exc).__name__,
                    exc,
                )
            return []
        except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError) as exc:
            logger.warning(
                "DuckDuckGo search connection error for school %s (%s): %s",
                school_id,
                type(exc).__name__,
                exc,
            )
            return []
        except Exception as exc:
            logger.warning(
                "DuckDuckGo search failed for school %s (%s): %s",
                school_id,
                type(exc).__name__,
                exc,
            )
            return []

        soup = BeautifulSoup(response.text, "html.parser")
        return self._extract_duckduckgo_links(soup)

    async def _search_brave(self, query: str, school_id: int) -> list[str]:
        """Query Brave HTML results page."""
        try:
            async with httpx.AsyncClient(
                timeout=self.search_timeout,
                follow_redirects=True,
                headers=self.SEARCH_HEADERS,
            ) as client:
                response = await client.get(self.BRAVE_ENDPOINT, params={"q": query})
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {401, 403, 429}:
                self._disable_provider("brave", f"HTTP {exc.response.status_code}")
            else:
                logger.warning(
                    "Brave search failed for school %s (%s): %s",
                    school_id,
                    type(exc).__name__,
                    exc,
                )
            return []
        except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError) as exc:
            logger.warning(
                "Brave search connection error for school %s (%s): %s",
                school_id,
                type(exc).__name__,
                exc,
            )
            return []
        except Exception as exc:
            logger.warning(
                "Brave search failed for school %s (%s): %s",
                school_id,
                type(exc).__name__,
                exc,
            )
            return []

        soup = BeautifulSoup(response.text, "html.parser")
        return self._extract_brave_links(soup)

    def _extract_duckduckgo_links(self, soup: BeautifulSoup) -> list[str]:
        """Extract candidate links from DuckDuckGo HTML results."""
        links: list[str] = []
        seen: set[str] = set()
        for anchor in soup.select("a[href]"):
            href = (anchor.get("href") or "").strip()
            candidate = self._extract_result_url(href)
            if not candidate:
                continue
            cleaned = self._canonicalize_search_url(candidate)
            if not cleaned or cleaned in seen:
                continue
            seen.add(cleaned)
            links.append(cleaned)
            if len(links) >= self.MAX_SEARCH_RESULTS:
                break
        return links

    def _extract_brave_links(self, soup: BeautifulSoup) -> list[str]:
        """Extract candidate links from Brave HTML results."""
        links: list[str] = []
        seen: set[str] = set()
        for anchor in soup.select("a[href]"):
            href = (anchor.get("href") or "").strip()
            if not href.startswith(("http://", "https://")):
                continue
            cleaned = self._canonicalize_search_url(href)
            if not cleaned or cleaned in seen:
                continue
            seen.add(cleaned)
            links.append(cleaned)
            if len(links) >= self.MAX_SEARCH_RESULTS:
                break
        return links

    def _extract_result_url(self, href: str) -> str | None:
        """Extract a direct result URL from DuckDuckGo-style links."""
        if not href:
            return None

        parsed = urlparse(href)
        if parsed.scheme in {"http", "https"}:
            return href

        if "uddg=" in href:
            qs = parse_qs(parsed.query)
            encoded = qs.get("uddg")
            if encoded:
                return unquote(encoded[0])
            match = re.search(r"uddg=([^&]+)", href)
            if match:
                return unquote(match.group(1))

        return None

    def _canonicalize_search_url(self, url: str) -> str | None:
        """Drop obvious search-engine/self links and noisy list-directory links."""
        normalized = self.validator.normalize_url(url)
        if not normalized:
            return None

        parsed = urlparse(normalized)
        host = parsed.netloc.lower()
        if "brave.com" in host or "duckduckgo.com" in host:
            return None

        if self._is_non_official_candidate(normalized):
            return None

        return normalized

    def _is_non_official_candidate(self, url: str) -> bool:
        """Reject known directory, document, and municipal listing URLs."""
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        if host.startswith("www."):
            host = host[4:]

        path = (parsed.path or "").lower()

        if host == "sofia.bg" or host.endswith(".sofia.bg"):
            return True

        if any(host == domain or host.endswith(f".{domain}") for domain in _NOISY_DIRECTORY_DOMAINS):
            return True

        if any(path.endswith(ext) for ext in _DOCUMENT_EXTENSIONS):
            return True

        if any(snippet in path for snippet in _NOISY_PATH_SNIPPETS):
            return True

        if re.search(r"/download(?:s)?(?:/|$|-|_)", path):
            return True

        # Reject hosts that look like aggregator/directory portals (e.g. spravochnik.framar.bg,
        # detskitegradini.com, obrazovatelen-register.com).
        if self._is_likely_directory_host(host):
            return True

        # BG-specific: reject directory listing entries where the URL path slug encodes the
        # school type + name (e.g. finansi.bg/chastna-detska-gradina-NAME or
        # yox.bg/частна-детска-градина-NAME after URL-decoding).
        decoded_path = unquote(path)
        last_segment = decoded_path.rstrip("/").rsplit("/", 1)[-1]
        if len(last_segment) > 20 and any(
            last_segment.startswith(prefix) for prefix in _BG_SCHOOL_TYPE_PATH_PREFIXES
        ):
            return True

        return False

    def _is_registry_url(self, url: str) -> bool:
        lower = (url or "").strip().lower()
        if not lower:
            return True
        if lower.startswith(_REGISTRY_SCHEMES):
            return True
        return any(domain in lower for domain in _REGISTRY_DOMAINS)

    def _domain_from_email(self, value: str) -> str | None:
        email = (value or "").strip().lower()
        if "@" not in email:
            return None
        domain = email.split("@", 1)[1]
        if not domain or "." not in domain:
            return None
        if domain in _FREE_EMAIL_DOMAINS:
            return None
        return domain

    def _is_school_aligned_email_domain(self, domain: str, school_tokens: list[str]) -> bool:
        """Keep email-domain candidates only when they likely belong to the school."""
        host = (domain or "").strip().lower()
        if not host:
            return False
        if self._is_registry_url(host):
            return False
        if host == "mon.bg" or host.endswith(".mon.bg"):
            return False
        if not school_tokens:
            return True

        for token in school_tokens:
            variants = [variant for variant in self._token_variants(token) if variant and len(variant) >= 2]
            if any(variant in host for variant in variants):
                return True

        # Fall back to generic school-related domain hints, e.g. school123.bg.
        return any(hint in host for hint in _EMAIL_DOMAIN_HINTS)

    async def _upsert_discovery_source_page(
        self,
        db: AsyncSession,
        school_id: int,
        website_url: str,
        method: str,
    ) -> None:
        """Persist provenance for discovered website URL."""
        now = datetime.now(timezone.utc)
        content_hash = BaseScraper.compute_hash(f"{website_url}|{method}")

        result = await db.execute(
            select(SourcePage).where(
                SourcePage.school_id == school_id,
                SourcePage.scrape_type == ScrapeType.DISCOVERY,
                SourcePage.source_url == website_url,
            )
        )
        source_page = result.scalar_one_or_none()

        if source_page:
            source_page.content_hash = content_hash
            source_page.page_category = "website_discovery"
            source_page.last_scraped_at = now
            source_page.last_changed_at = now
            source_page.scrape_count = (source_page.scrape_count or 0) + 1
            source_page.is_valid = None
            return

        db.add(
            SourcePage(
                school_id=school_id,
                scrape_type=ScrapeType.DISCOVERY,
                source_url=website_url,
                content_hash=content_hash,
                page_category="website_discovery",
                is_valid=None,
                last_scraped_at=now,
                last_changed_at=now,
                scrape_count=1,
            )
        )


async def discover_school_website(
    db: AsyncSession,
    school_id: int,
    country_code: str = "bg",
    use_search_fallback: bool = True,
) -> dict:
    """Run website discovery for a single school."""
    result = await db.execute(
        select(School)
        .options(selectinload(School.locations))
        .where(School.id == school_id)
    )
    school = result.scalar_one_or_none()

    if not school:
        return {"school_id": school_id, "found": False, "updated": False, "reason": "School not found"}

    discoverer = WebsiteDiscoverer(country_code=country_code)
    return await discoverer.discover(
        db=db,
        school=school,
        use_search_fallback=use_search_fallback,
    )
