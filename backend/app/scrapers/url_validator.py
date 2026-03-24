"""
URL Validator (Stage 2 of scraping pipeline)

Validates school website URLs before proceeding to navigation/extraction stages.
Uses heuristic keyword matching (~90% of cases) with optional LLM validation
for ambiguous cases.
"""
import logging
from collections.abc import Mapping
from typing import Optional
from enum import Enum
import asyncio
import re
from urllib.parse import unquote, urlparse
import httpx
from bs4 import BeautifulSoup

from app.ai.client import create_agent
from app.config import get_settings
from app.scrapers.school_tokens import extract_school_name_tokens
from app.utils.transliteration import transliterate_bulgarian
from pydantic import BaseModel

logger = logging.getLogger(__name__)

TIMEOUT_FAILURE_ATTR_KEY = "url_validation_timeout_failures"


class ValidationResult(str, Enum):
    """URL validation result."""
    VALID = "valid"  # URL loads and appears to be a school website
    INVALID = "invalid"  # URL doesn't load or clearly not a school
    AMBIGUOUS = "ambiguous"  # Needs LLM validation


class URLValidationOutput(BaseModel):
    """Output from LLM URL validation."""
    is_school_website: bool
    matches_expected_school: bool = True
    confidence: float  # 0.0-1.0
    reason: str


class URLValidator:
    """
    Validates school website URLs.

    Process:
    1. HTTP check: Does the URL load? (status 200)
    2. Heuristic check: Does the page contain school-related keywords?
    3. LLM check (if ambiguous): Ask cheap model "Is this a school website?"

    About 90% of URLs pass heuristics without LLM calls, saving costs.
    """

    # Timeout for HTTP requests
    HTTP_TIMEOUT = 4.0

    # Keywords that indicate a school website (locale-specific)
    # These should be loaded from sources/{country_code}/keywords.json
    DEFAULT_SCHOOL_KEYWORDS = {
        "bg": [
            "училище",
            "детска градина",
            "гимназия",
            "прогимназия",
            "ученик",
            "учител",
            "прием",
            "записване",
            "образование",
            "клас",
            "паралелка",
        ],
        "en": [
            "school",
            "kindergarten",
            "student",
            "teacher",
            "admission",
            "enrollment",
            "education",
            "class",
            "grade",
        ],
    }

    # Domains to immediately reject (social media, forums, etc.)
    # TODO: Consolidate directory/blocked-domain heuristics with website_discovery to avoid drift.
    BLOCKED_DOMAINS = [
        "facebook.com",
        "instagram.com",
        "bg-mamma.com",
        "twitter.com",
        "linkedin.com",
        "youtube.com",
    ]
    BROWSER_MARKDOWN_NOISE_MARKERS = (
        "бисквит",
        "cookie",
        "consent",
        "cmplz",
        "privacy policy",
        "политика за поверителност",
        "политика за бисквит",
        "manage services",
        "manage vendors",
        "revisit consent",
        "skip to content",
        "приемам",
        "отказ",
        "конфигурирай",
        "запазване на настройките",
        "reject all",
        "accept all",
        "remember my choice",
        "functional",
        "analytics",
        "marketing",
        "performance",
        "статистически",
        "маркетингови",
    )

    # Generic directory/listing markers (pattern-based, not per-domain denylist).
    DIRECTORY_PATH_MARKERS = (
        "firm",
        "firms",
        "company",
        "companies",
        "listing",
        "listings",
        "directory",
        "catalog",
        "profile",
        "business",
        "spravochnik",  # Bulgarian: справочник = reference directory
        "item",         # registry/marketplace listing path (/item/school-name)
    )
    DIRECTORY_TEXT_MARKERS = (
        "добави фирма",
        "подобни фирми",
        "още фирми",
        "каталог",
        "каталог на фирми",
        "business directory",
        "company profile",
        "add company",
        "related companies",
        "all companies",
    )
    GENERIC_IDENTITY_LABEL_PREFIXES = (
        "начало",
        "към ",
        "за училището",
        "училището",
        "история на училището",
        "патрон на училището",
        "патронен празник",
        "училищен план",
        "иновативно училище",
        "стратегия на",
    )
    GENERIC_IDENTITY_LABEL_WORDS = {
        "частна",
        "детска",
        "градина",
        "училище",
        "гимназия",
        "основно",
        "средно",
        "профилирана",
        "професионална",
        "английска",
        "language",
        "english",
    }

    def __init__(self, country_code: str = "bg"):
        """
        Initialize the URL validator.

        Args:
            country_code: Country code for locale-specific keywords
        """
        self.country_code = country_code
        self.keywords = self._load_keywords(country_code)
        settings = get_settings()
        self.http_timeout = max(1.0, float(settings.url_validation_http_timeout_seconds))
        self.retry_http_timeout = max(self.http_timeout, float(settings.url_validation_retry_http_timeout_seconds))
        self.llm_timeout = max(5.0, float(settings.url_validation_llm_timeout_seconds))

    def _load_keywords(self, country_code: str) -> list[str]:
        """
        Load school keywords for the given country.

        In production, this would load from:
        backend/app/scrapers/sources/{country_code}/keywords.json

        Args:
            country_code: Country code (e.g., "bg")

        Returns:
            List of school-related keywords
        """
        # For now, use default keywords
        # TODO: Load from JSON file when we create the keywords.json files
        return self.DEFAULT_SCHOOL_KEYWORDS.get(country_code, self.DEFAULT_SCHOOL_KEYWORDS["en"])

    def normalize_url(self, url: str) -> Optional[str]:
        """
        Normalize website URL so HTTP client can fetch it reliably.

        Handles common data issues:
        - Missing scheme: `www.school.bg` -> `https://www.school.bg`
        - Malformed scheme: `http//school.bg` -> `http://school.bg`
        - Protocol-relative URLs: `//school.bg` -> `https://school.bg`
        """
        if not url:
            return None

        cleaned = url.strip().strip("\"' ")
        if not cleaned:
            return None

        lower = cleaned.lower()
        if lower.startswith("http//"):
            cleaned = "http://" + cleaned[6:]
        elif lower.startswith("https//"):
            cleaned = "https://" + cleaned[7:]
        elif cleaned.startswith("//"):
            cleaned = f"https:{cleaned}"
        elif "://" not in cleaned:
            cleaned = f"https://{cleaned.lstrip('/')}"

        try:
            parsed = urlparse(cleaned)
        except ValueError:
            return None
        if parsed.scheme not in {"http", "https"}:
            return None

        # Handle edge case like "https:school.bg" (missing //).
        if not parsed.netloc and parsed.path:
            fixed = f"{parsed.scheme}://{parsed.path.lstrip('/')}"
            try:
                parsed = urlparse(fixed)
            except ValueError:
                return None
            cleaned = fixed

        if not parsed.netloc:
            return None

        if any(char.isspace() for char in parsed.netloc):
            compact_netloc = "".join(parsed.netloc.split())
            if not compact_netloc:
                return None
            cleaned = cleaned.replace(parsed.netloc, compact_netloc, 1)
            try:
                parsed = urlparse(cleaned)
            except ValueError:
                return None
            if not parsed.netloc or any(char.isspace() for char in parsed.netloc):
                return None

        return cleaned

    async def validate_url(
        self,
        url: str,
        use_llm_fallback: bool = True,
        school_name: Optional[str] = None,
        school_aliases: Optional[list[str]] = None,
    ) -> tuple[ValidationResult, Optional[str], Optional[str]]:
        """
        Validate a school website URL.

        Args:
            url: URL to validate
            use_llm_fallback: Whether to use LLM for ambiguous cases
            school_name: Optional expected school name for official-site checks

        Returns:
            Tuple of (result, final_url, reason)
            - result: ValidationResult enum
            - final_url: Final URL after redirects (or None if invalid)
            - reason: Human-readable reason for the result

        Example:
            >>> validator = URLValidator("bg")
            >>> result, final_url, reason = await validator.validate_url("https://school.bg")
            >>> if result == ValidationResult.VALID:
            ...     print(f"Valid school website: {final_url}")
        """
        normalized_url = self.normalize_url(url)
        if not normalized_url:
            return ValidationResult.INVALID, None, "Invalid URL format"

        # Step 1: Check if domain is blocked
        if self._is_blocked_domain(normalized_url):
            return ValidationResult.INVALID, None, "Blocked domain (social media/forum)"

        first_result = await self._validate_with_http_timeout(
            normalized_url=normalized_url,
            timeout_seconds=self.http_timeout,
            use_llm_fallback=use_llm_fallback,
            school_name=school_name,
            school_aliases=school_aliases,
        )

        result, _, reason = first_result
        should_retry_timeout = (
            result == ValidationResult.INVALID
            and _is_timeout_reason(reason)
            and self.retry_http_timeout > self.http_timeout
        )
        if not should_retry_timeout:
            return first_result

        retry_result = await self._validate_with_http_timeout(
            normalized_url=normalized_url,
            timeout_seconds=self.retry_http_timeout,
            use_llm_fallback=use_llm_fallback,
            school_name=school_name,
            school_aliases=school_aliases,
        )
        retry_status, retry_final_url, retry_reason = retry_result

        if retry_status == ValidationResult.INVALID and _is_timeout_reason(retry_reason):
            return retry_result
        return (
            retry_status,
            retry_final_url,
            f"{retry_reason} (after timeout retry at {self.retry_http_timeout:.1f}s)",
        )

    async def _validate_with_http_timeout(
        self,
        normalized_url: str,
        timeout_seconds: float,
        use_llm_fallback: bool,
        school_name: Optional[str],
        school_aliases: Optional[list[str]],
    ) -> tuple[ValidationResult, Optional[str], Optional[str]]:
        """Run one validation pass with a specific HTTP timeout."""
        try:
            async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=True) as client:
                response = await client.get(normalized_url)
                final_url = str(response.url)  # Final URL after redirects

                if response.status_code >= 400:
                    browser_fallback = await self._validate_with_browser_fallback(
                        normalized_url=normalized_url,
                        status_code=response.status_code,
                        use_llm_fallback=use_llm_fallback,
                        school_name=school_name,
                        school_aliases=school_aliases,
                    )
                    if browser_fallback is not None:
                        return browser_fallback
                    return ValidationResult.INVALID, None, f"HTTP error: {response.status_code}"

                html_content = response.text
                soup = BeautifulSoup(html_content, "html.parser")

                if self._is_bot_protection_page(soup):
                    canonical_final_url = self._canonicalize_bot_protection_final_url(
                        normalized_url=normalized_url,
                        final_url=final_url,
                    )
                    return (
                        ValidationResult.VALID,
                        canonical_final_url,
                        "Bot protection detected - browser navigation required",
                    )

                title_text = soup.title.string if soup.title and soup.title.string else ""
                h1_text = " ".join(tag.get_text(" ", strip=True) for tag in soup.find_all("h1")[:2])
                return await self._evaluate_page_content(
                    text_content=soup.get_text(separator=" ", strip=True),
                    final_url=final_url,
                    use_llm_fallback=use_llm_fallback,
                    school_name=school_name,
                    school_aliases=school_aliases,
                    title_text=title_text,
                    h1_text=h1_text,
                    page_identity_labels=self._extract_page_identity_labels(soup),
                    soup=soup,
                )
        except httpx.TimeoutException:
            return ValidationResult.INVALID, None, "Connection timeout"
        except httpx.HTTPError as exc:
            return ValidationResult.INVALID, None, f"HTTP error: {exc}"
        except Exception as exc:
            logger.error("Error validating URL %s: %s", normalized_url, exc)
            return ValidationResult.INVALID, None, f"Validation error: {exc}"

    async def _validate_with_browser_fallback(
        self,
        *,
        normalized_url: str,
        status_code: int,
        use_llm_fallback: bool,
        school_name: Optional[str],
        school_aliases: Optional[list[str]],
    ) -> tuple[ValidationResult, Optional[str], Optional[str]] | None:
        """Use the browser stack only for blocked responses that httpx cannot inspect."""
        if status_code not in {401, 403, 429}:
            return None

        try:
            from app.scrapers.navigator import WebsiteNavigator

            navigator = WebsiteNavigator(country_code=self.country_code)
            final_url, pages = await navigator.discover_pages(normalized_url)
        except Exception as exc:
            logger.warning("Browser fallback failed for %s: %s", normalized_url, exc)
            return None

        content_pages = [page for page in pages if (page.markdown or "").strip()]
        if not content_pages:
            return None

        snippets: list[str] = []
        page_identity_labels: list[str] = []
        for page in content_pages[:3]:
            markdown = self._clean_browser_markdown(page.markdown or "")
            snippets.append(markdown[:4000])
            page_identity_labels.extend(self._extract_markdown_identity_labels(markdown))

        return await self._evaluate_page_content(
            text_content="\n\n".join(snippets),
            final_url=final_url or normalized_url,
            use_llm_fallback=use_llm_fallback,
            school_name=school_name,
            school_aliases=school_aliases,
            page_identity_labels=page_identity_labels[:8],
            soup=None,
        )

    async def _evaluate_page_content(
        self,
        *,
        text_content: str,
        final_url: str,
        use_llm_fallback: bool,
        school_name: Optional[str],
        school_aliases: Optional[list[str]],
        title_text: str = "",
        h1_text: str = "",
        page_identity_labels: Optional[list[str]] = None,
        soup: Optional[BeautifulSoup] = None,
    ) -> tuple[ValidationResult, Optional[str], Optional[str]]:
        text_content = (text_content or "").lower()
        page_identity_labels = page_identity_labels or []
        page_context = f"{title_text} {h1_text} {text_content[:4000]}".lower()
        expected_names = self._expected_school_names(school_name, school_aliases)
        identity_context = " ".join([title_text, h1_text, *page_identity_labels[:8]]).lower()

        keyword_count = sum(1 for keyword in self.keywords if keyword.lower() in text_content)
        name_tokens = self._extract_expected_name_tokens(expected_names)
        name_token_hits = self._count_school_name_token_hits(identity_context, name_tokens)
        min_name_token_hits = self._minimum_required_name_token_hits(name_tokens)
        explicit_name_match = self._has_explicit_expected_name_match(identity_context, expected_names)
        if explicit_name_match:
            min_name_token_hits = 1
        directory_signals = self._count_directory_signals(final_url, text_content, soup)
        identity_mismatch = self._find_identity_mismatch(page_identity_labels, expected_names)

        if directory_signals >= 1 and name_tokens and name_token_hits < min_name_token_hits:
            return (
                ValidationResult.INVALID,
                None,
                f"Directory-like listing signals with missing school-name match ({directory_signals})",
            )

        if directory_signals >= 2:
            return (
                ValidationResult.INVALID,
                None,
                f"Directory-like listing signals detected ({directory_signals})",
            )

        if identity_mismatch:
            return (
                ValidationResult.INVALID,
                None,
                f"Website identity mismatch: {identity_mismatch}",
            )

        if name_tokens and name_token_hits < min_name_token_hits:
            if use_llm_fallback:
                return await self._llm_validate(
                    text_content,
                    final_url,
                    school_name=school_name,
                    school_aliases=school_aliases,
                    page_identity_labels=page_identity_labels,
                )
            if name_token_hits == 0:
                return ValidationResult.INVALID, None, "Expected school name not found on page"
            return (
                ValidationResult.INVALID,
                None,
                f"Weak expected-school name match on page ({name_token_hits}/{min_name_token_hits} token hits)",
            )

        if name_tokens and name_token_hits >= min_name_token_hits and keyword_count >= 2:
            return (
                ValidationResult.VALID,
                final_url,
                f"Strong expected-school match with {keyword_count} school terms",
            )

        if keyword_count >= 3:
            if directory_signals > 0:
                if use_llm_fallback:
                    return await self._llm_validate(
                        text_content,
                        final_url,
                        school_name=school_name,
                        school_aliases=school_aliases,
                        page_identity_labels=page_identity_labels,
                    )
                return (
                    ValidationResult.AMBIGUOUS,
                    final_url,
                    f"Ambiguous: listing-like signal detected ({directory_signals})",
                )
            return (
                ValidationResult.VALID,
                final_url,
                f"Keyword match: {keyword_count} school terms; name tokens matched: {name_token_hits}",
            )

        if keyword_count == 0:
            if use_llm_fallback:
                return await self._llm_validate(
                    text_content,
                    final_url,
                    school_name=school_name,
                    school_aliases=school_aliases,
                    page_identity_labels=page_identity_labels,
                )
            return ValidationResult.INVALID, None, "No school-related keywords found"

        if use_llm_fallback:
            return await self._llm_validate(
                text_content,
                final_url,
                school_name=school_name,
                school_aliases=school_aliases,
                page_identity_labels=page_identity_labels,
            )
        return (
            ValidationResult.AMBIGUOUS,
            final_url,
            f"Ambiguous: only {keyword_count} keyword(s) found",
        )

    def _extract_markdown_identity_labels(self, markdown_text: str) -> list[str]:
        labels: list[str] = []
        seen: set[str] = set()
        for raw_line in (markdown_text or "").splitlines():
            line = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", raw_line)
            line = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line)
            line = line.strip(" #>*-`|")
            line = re.sub(r"\s+", " ", line).strip()
            lowered = line.lower()
            if len(line) < 4 or len(line) > 120:
                continue
            if any(marker in lowered for marker in ("бисквит", "cookie", "consent", "privacy", "gdpr")):
                continue
            key = lowered
            if key in seen:
                continue
            seen.add(key)
            labels.append(line)
            if len(labels) >= 8:
                break
        return labels

    def _clean_browser_markdown(self, markdown_text: str) -> str:
        cleaned_lines: list[str] = []
        for raw_line in (markdown_text or "").splitlines():
            line = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", raw_line)
            line = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line)
            line = re.sub(r"\s+", " ", line).strip()
            lowered = line.lower()
            if not line:
                continue
            if any(marker in lowered for marker in self.BROWSER_MARKDOWN_NOISE_MARKERS):
                continue
            cleaned_lines.append(line)
        return "\n".join(cleaned_lines)

    # Known bot-protection challenge URL path fragments (case-insensitive).
    # A <meta http-equiv="refresh"> pointing to any of these indicates the page
    # is a captcha/challenge gate, not actual school content.
    _BOT_CHALLENGE_PATHS = (
        "/.well-known/sgcaptcha",   # Sucuri WAF
        "/.well-known/captcha",
        "/cdn-cgi/challenge",       # Cloudflare
        "/cdn-cgi/l/chk_jschl",    # Cloudflare JS challenge
    )

    def _is_bot_protection_page(self, soup: BeautifulSoup) -> bool:
        """Return True when the page is a bot-protection / captcha challenge gate.

        These pages return a 2xx status but contain no real content — only a
        meta-refresh redirect to a challenge endpoint.  Marking them VALID lets
        the school proceed to the navigation stage where Playwright/crawl4ai can
        handle the JS challenge and access the real site content.
        """
        for meta in soup.find_all("meta"):
            if (meta.get("http-equiv") or "").strip().lower() != "refresh":
                continue
            content = (meta.get("content") or "").lower()
            if any(path in content for path in self._BOT_CHALLENGE_PATHS):
                return True
        return False

    def _is_bot_challenge_url(self, url: str) -> bool:
        """Return True when URL path points to a known bot-challenge endpoint."""
        parsed = urlparse(url)
        path = (parsed.path or "").lower()
        return any(path.startswith(marker) for marker in self._BOT_CHALLENGE_PATHS)

    def _canonicalize_bot_protection_final_url(self, normalized_url: str, final_url: Optional[str]) -> str:
        """Keep canonical site URL when challenge endpoints are encountered."""
        if final_url and self._is_bot_challenge_url(final_url):
            parsed = urlparse(final_url)
            if parsed.scheme and parsed.netloc:
                canonical = self.normalize_url(f"{parsed.scheme}://{parsed.netloc}")
                if canonical:
                    return canonical
            return normalized_url
        return final_url or normalized_url

    def _is_blocked_domain(self, url: str) -> bool:
        """
        Check if URL is from a blocked domain.

        Args:
            url: URL to check

        Returns:
            True if domain is blocked
        """
        url_lower = url.lower()
        return any(domain in url_lower for domain in self.BLOCKED_DOMAINS)

    def _extract_school_name_tokens(self, school_name: Optional[str]) -> list[str]:
        """Extract distinctive tokens from school name for ownership checks."""
        return extract_school_name_tokens(school_name, limit=8)

    def _expected_school_names(
        self,
        school_name: Optional[str],
        school_aliases: Optional[list[str]],
    ) -> list[str]:
        names: list[str] = []
        seen: set[str] = set()

        def add_name(value: Optional[str]) -> None:
            text = (value or "").strip()
            if not text:
                return
            key = text.casefold()
            if key in seen:
                return
            seen.add(key)
            names.append(text)

        add_name(school_name)
        if school_name and self.country_code == "bg":
            add_name(transliterate_bulgarian(school_name))
        for alias in school_aliases or []:
            add_name(alias)
        return names

    def _extract_expected_name_tokens(self, expected_names: list[str]) -> list[str]:
        tokens: list[str] = []
        seen: set[str] = set()
        for name in expected_names:
            for token in self._extract_school_name_tokens(name):
                if token in seen:
                    continue
                seen.add(token)
                tokens.append(token)
        return tokens

    def _count_school_name_token_hits(self, page_context: str, school_tokens: list[str]) -> int:
        """Return number of expected school-name tokens present in page context."""
        if not school_tokens:
            return 0
        return sum(1 for token in school_tokens if token in page_context)

    def _minimum_required_name_token_hits(self, school_tokens: list[str]) -> int:
        """Require two hits when multiple distinctive ownership tokens exist."""
        return 2 if len(school_tokens) >= 2 else 1

    def _has_explicit_expected_name_match(self, page_context: str, expected_names: list[str]) -> bool:
        """Return True when an expected name or alias appears verbatim on the page."""
        normalized_page = " ".join((page_context or "").casefold().split())
        for name in expected_names:
            normalized_name = " ".join((name or "").casefold().split()).strip("\"' ")
            if len(normalized_name) < 4:
                continue
            if normalized_name in normalized_page:
                return True
        return False

    def _extract_page_identity_labels(self, soup: BeautifulSoup) -> list[str]:
        labels: list[str] = []
        seen: set[str] = set()

        def add_label(value: Optional[str]) -> None:
            text = " ".join((value or "").split()).strip()
            if not text:
                return
            key = text.casefold()
            if key in seen:
                return
            seen.add(key)
            labels.append(text)

        if soup.title and soup.title.string:
            add_label(soup.title.string)

        for tag in soup.find_all(["h1", "h2"])[:6]:
            add_label(tag.get_text(" ", strip=True))

        for img in soup.find_all("img")[:10]:
            add_label(img.get("alt"))
            add_label(img.get("title"))

        for anchor in soup.find_all("a", href=True)[:30]:
            add_label(anchor.get("title"))
            add_label(anchor.get_text(" ", strip=True))

        return labels[:20]

    def _find_identity_mismatch(self, page_identity_labels: list[str], expected_names: list[str]) -> Optional[str]:
        expected_tokens = set(self._extract_expected_name_tokens(expected_names))
        if not expected_tokens:
            return None

        school_markers = (
            "училище",
            "градина",
            "гимназ",
            "чоу",
            "чдг",
            "чсу",
            "чну",
            "school",
            "kindergarten",
            "academy",
        )
        mismatch_candidates: list[str] = []
        for label in page_identity_labels:
            lowered = label.lower()
            label_tokens = set(extract_school_name_tokens(label, limit=8))
            if label_tokens & expected_tokens:
                token_hits = len(label_tokens & expected_tokens)
                if token_hits >= 2 or self._has_explicit_expected_name_match(label, expected_names):
                    return None
            if self._is_generic_identity_label(label):
                continue
            if not any(marker in lowered for marker in school_markers):
                continue
            if not label_tokens:
                continue
            if label_tokens & expected_tokens:
                return None
            # English-only labels are allowed to proceed to LLM ownership check.
            if re.search(r"[A-Za-z]", label) and not re.search(r"[А-Яа-я]", label):
                continue
            mismatch_candidates.append(label)

        if mismatch_candidates:
            return mismatch_candidates[0]
        return None

    def _is_generic_identity_label(self, label: str) -> bool:
        normalized = " ".join((label or "").casefold().replace("–", " ").replace("-", " ").split())
        if not normalized:
            return True
        if any(normalized.startswith(prefix) for prefix in self.GENERIC_IDENTITY_LABEL_PREFIXES):
            return True
        words = normalized.split()
        if words and len(words) <= 3 and all(word in self.GENERIC_IDENTITY_LABEL_WORDS for word in words):
            return True
        return len(words) >= 9

    # BG-specific: school-type prefixes that appear in directory listing URL slugs.
    # Any URL whose last path segment starts with one of these is a directory entry,
    # not an official school website.
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

    def _count_directory_signals(self, url: str, text_content: str, soup: Optional[BeautifulSoup]) -> int:
        """Count generic listing-directory signals from URL and page content."""
        parsed = urlparse(url)
        path = (parsed.path or "").lower()
        query = (parsed.query or "").lower()
        signals = 0

        marker_pattern = r"(?:^|/|[-_])(" + "|".join(re.escape(m) for m in self.DIRECTORY_PATH_MARKERS) + r")(?:[-_0-9/]|$)"
        if re.search(marker_pattern, path):
            signals += 1
        if any(marker in query for marker in ("firm", "company", "listing", "directory", "catalog", "profile")):
            signals += 1
        if any(marker in text_content for marker in self.DIRECTORY_TEXT_MARKERS):
            signals += 1

        # BG-specific: URL path slug encodes school type + name (directory entry pattern)
        decoded_path = unquote(path)
        last_segment = decoded_path.rstrip("/").rsplit("/", 1)[-1]
        if len(last_segment) > 20 and any(
            last_segment.startswith(prefix) for prefix in self._BG_SCHOOL_TYPE_PATH_PREFIXES
        ):
            signals += 1

        listing_links = 0
        if soup is not None:
            for anchor in soup.find_all("a", href=True):
                href = (anchor.get("href") or "").lower()
                if re.search(marker_pattern, href):
                    listing_links += 1
                    if listing_links >= 4:
                        signals += 1
                        break
        return signals

    async def _llm_validate(
        self,
        page_text: str,
        url: str,
        school_name: Optional[str] = None,
        school_aliases: Optional[list[str]] = None,
        page_identity_labels: Optional[list[str]] = None,
    ) -> tuple[ValidationResult, Optional[str], Optional[str]]:
        """
        Use LLM to validate if a page is a school website.

        This is only called for ambiguous cases where heuristics are unclear.

        Args:
            page_text: Text content of the page
            url: URL being validated

        Returns:
            Tuple of (result, final_url, reason)
        """
        # Create agent with cheap model
        system_prompt = """You are a website classifier. Determine both:
1. whether a webpage is a school website
2. whether it matches the expected school identity

Consider it a school website if it contains:
- Information about students, teachers, classes, grades
- Admission/enrollment information
- School schedules, curriculum, events
- Contact information for a school

Do NOT consider it a school website if it's:
- A social media page
- A forum or discussion site
- A news article about schools
- A directory listing schools
- A government education portal (unless it's a specific school's page)

Be strict:
- Only set is_school_website=true if you're confident it's an actual school's website
- Only set matches_expected_school=true if the page appears to belong to the expected school or one of its explicit aliases
- If the page is clearly a different school's website, set matches_expected_school=false"""

        # Truncate page text to avoid huge token counts
        truncated_text = page_text[:2000]  # ~500 words
        expected_name = school_name or "unknown"
        aliases = [alias for alias in (school_aliases or []) if alias.strip()]
        aliases_text = ", ".join(aliases[:8]) if aliases else "none"
        identity_text = " | ".join(page_identity_labels[:8]) if page_identity_labels else "none"
        prompt = (
            f"URL: {url}\n"
            f"Expected school name: {expected_name}\n\n"
            f"Expected aliases: {aliases_text}\n\n"
            f"Page identity labels: {identity_text}\n\n"
            f"Page content:\n{truncated_text}\n\n"
            "Return structured output for both school-website detection and expected-school match."
        )

        try:
            output = await self._run_llm_validation_tier(
                tier="cheap",
                system_prompt=system_prompt,
                prompt=prompt,
            )
        except Exception as cheap_error:
            if self._is_model_or_provider_error(cheap_error):
                logger.warning(
                    "Cheap LLM validation failed for %s (%s). Retrying once with cheap tier.",
                    url,
                    cheap_error,
                )
                try:
                    output = await self._run_llm_validation_tier(
                        tier="cheap",
                        system_prompt=system_prompt,
                        prompt=prompt,
                    )
                except Exception as retry_error:
                    logger.error(f"LLM validation failed for {url}: {retry_error}")
                    # On repeated LLM failure, mark as ambiguous rather than invalid.
                    return (
                        ValidationResult.AMBIGUOUS,
                        url,
                        f"LLM validation failed after retry: {str(retry_error)}",
                    )
            else:
                logger.error(f"LLM validation failed for {url}: {cheap_error}")
                return (
                    ValidationResult.AMBIGUOUS,
                    url,
                    f"LLM validation failed: {str(cheap_error)}",
                )

        if output.is_school_website and output.matches_expected_school and output.confidence >= 0.7:
            return (
                ValidationResult.VALID,
                url,
                f"LLM validation: {output.reason} (confidence: {output.confidence:.2f})",
            )

        if output.is_school_website and not output.matches_expected_school:
            return (
                ValidationResult.INVALID,
                None,
                f"LLM validation: expected-school mismatch - {output.reason} (confidence: {output.confidence:.2f})",
            )

        return (
            ValidationResult.INVALID,
            None,
            f"LLM validation: {output.reason} (confidence: {output.confidence:.2f})",
        )

    async def _run_llm_validation_tier(
        self,
        tier: str,
        system_prompt: str,
        prompt: str,
    ) -> URLValidationOutput:
        """Run one LLM classification pass for the provided tier."""
        agent = create_agent(
            tier=tier,  # type: ignore[arg-type]
            system_prompt=system_prompt,
            result_type=URLValidationOutput,
        )
        result = await asyncio.wait_for(agent.run(prompt), timeout=self.llm_timeout)
        return self._parse_llm_output(result)

    def _parse_llm_output(self, result) -> URLValidationOutput:
        """Read structured output across pydantic-ai versions."""
        raw_output = getattr(result, "output", None)
        if isinstance(raw_output, URLValidationOutput):
            return raw_output
        if isinstance(raw_output, dict):
            return URLValidationOutput.model_validate(raw_output)

        raw_data = getattr(result, "data", None)
        if isinstance(raw_data, URLValidationOutput):
            return raw_data
        if raw_data is None:
            raise ValueError("LLM validation result missing structured data")
        return URLValidationOutput.model_validate(raw_data)

    def _is_model_or_provider_error(self, exc: Exception) -> bool:
        """Detect model/provider failures that deserve one retry."""
        message = str(exc).lower()
        markers = (
            "not a valid model id",
            "openrouter",
            "provider",
            "authentication",
            "failed to authenticate",
            "chat completions endpoint",
            "status_code",
            "finish_reason",
        )
        return any(marker in message for marker in markers)


def _is_timeout_reason(reason: Optional[str]) -> bool:
    """Return True when the validation failure is due to timeout."""
    return "timeout" in (reason or "").lower()


def _should_clear_website_derived_data(result: ValidationResult, reason: Optional[str]) -> bool:
    """Return True when invalidation is strong enough to purge website-derived fields."""
    if result != ValidationResult.INVALID:
        return False
    lowered = (reason or "").lower()
    strong_markers = (
        "website identity mismatch",
        "expected-school mismatch",
        "directory-like listing signals",
    )
    return any(marker in lowered for marker in strong_markers)


def _clear_website_derived_school_data(school) -> None:
    """Remove fields that were derived from an invalidated website."""
    school.summary_i18n = {}
    attrs = dict(school.attributes or {})
    attrs.pop("display_name_i18n", None)
    attrs.pop("extracted", None)
    attrs.pop("extracted_i18n", None)
    school.attributes = attrs


def extract_validation_aliases(attributes: object) -> list[str]:
    """Collect known school aliases for ownership validation."""
    if not isinstance(attributes, Mapping):
        return []

    aliases: list[str] = []
    seen: set[str] = set()

    def add_alias(value: object) -> None:
        text = str(value or "").strip()
        if not text:
            return
        key = text.casefold()
        if key in seen:
            return
        seen.add(key)
        aliases.append(text)

    display_name_i18n = attributes.get("display_name_i18n")
    if isinstance(display_name_i18n, Mapping):
        for value in display_name_i18n.values():
            add_alias(value)

    name_aliases = attributes.get("name_aliases")
    if isinstance(name_aliases, list):
        for value in name_aliases:
            add_alias(value)

    return aliases


def _update_timeout_failure_state(
    attributes: object,
    result: ValidationResult,
    reason: Optional[str],
    threshold: int,
) -> tuple[dict, int, bool]:
    """
    Track consecutive timeout failures in school attributes.

    Returns:
        (updated_attributes, timeout_count, reached_terminal_threshold)
    """
    attrs = dict(attributes) if isinstance(attributes, dict) else {}
    raw_count = attrs.get(TIMEOUT_FAILURE_ATTR_KEY, 0)
    try:
        count = int(raw_count)
    except (TypeError, ValueError):
        count = 0

    timed_out = result == ValidationResult.INVALID and _is_timeout_reason(reason)

    if timed_out:
        count += 1
    elif result in {ValidationResult.INVALID, ValidationResult.VALID, ValidationResult.AMBIGUOUS}:
        count = 0

    attrs[TIMEOUT_FAILURE_ATTR_KEY] = count
    reached_terminal_threshold = timed_out and count >= max(1, threshold)
    return attrs, count, reached_terminal_threshold


async def validate_school_url(
    school_id: int,
    url: str,
    country_code: str = "bg",
    update_db: bool = True,
    school_name: Optional[str] = None,
    school_aliases: Optional[list[str]] = None,
) -> tuple[ValidationResult, Optional[str], Optional[str]]:
    """
    Validate a school's website URL and optionally update the database.

    This is the main entry point for URL validation in the pipeline.

    Args:
        school_id: School ID
        url: URL to validate
        country_code: Country code for locale-specific validation
        update_db: Whether to update SourcePage and School in the database
        school_name: Optional expected school name for official-site checks

    Returns:
        Tuple of (result, final_url, reason)

    Example:
        >>> result, final_url, reason = await validate_school_url(
        ...     school_id=1,
        ...     url="https://school.bg",
        ...     country_code="bg",
        ... )
    """
    validator = URLValidator(country_code=country_code)
    normalized_url = validator.normalize_url(url)
    result, final_url, reason = await validator.validate_url(
        url,
        school_name=school_name,
        school_aliases=school_aliases,
    )

    if update_db:
        # Persist normalized input URL when available to reduce duplicate source pages.
        source_url = normalized_url or url
        await _update_validation_result(school_id, source_url, result, final_url, reason)

    return result, final_url, reason


async def _update_validation_result(
    school_id: int,
    url: str,
    result: ValidationResult,
    final_url: Optional[str],
    reason: str,
):
    """
    Update database with validation result.

    Updates:
    - SourcePage: is_valid field
    - School: website_url (if redirected)
    - School: scrape_status (if invalid)

    Args:
        school_id: School ID
        url: Original URL
        result: Validation result
        final_url: Final URL after redirects
        reason: Validation reason
    """
    from app.database import async_session_maker
    from app.models import School, SourcePage, ScrapeType
    from sqlalchemy import select

    async with async_session_maker() as db:
        settings = get_settings()
        timeout_terminal_threshold = max(1, int(settings.url_validation_timeout_terminal_threshold))

        # Update or create SourcePage
        source_page_result = await db.execute(
            select(SourcePage).where(
                SourcePage.school_id == school_id,
                SourcePage.source_url == url,
                SourcePage.scrape_type == ScrapeType.WEBSITE,
            )
        )
        source_page = source_page_result.scalar_one_or_none()

        if source_page:
            if result == ValidationResult.AMBIGUOUS:
                source_page.is_valid = None
            else:
                source_page.is_valid = result == ValidationResult.VALID
        else:
            # Create new SourcePage
            from app.scrapers.base import BaseScraper

            source_page = SourcePage(
                school_id=school_id,
                scrape_type=ScrapeType.WEBSITE,
                source_url=url,
                content_hash=BaseScraper.compute_hash(""),  # Empty for now
                is_valid=None if result == ValidationResult.AMBIGUOUS else result == ValidationResult.VALID,
            )
            db.add(source_page)

        # Update school
        school_result = await db.execute(select(School).where(School.id == school_id))
        school = school_result.scalar_one_or_none()

        if school:
            school.attributes = dict(school.attributes or {})
            updated_attrs, timeout_count, timeout_terminal = _update_timeout_failure_state(
                attributes=school.attributes,
                result=result,
                reason=reason,
                threshold=timeout_terminal_threshold,
            )
            school.attributes = updated_attrs
            candidate_url = final_url or url
            school.attributes["website_candidate_url"] = candidate_url
            school.attributes["website_candidate_reason"] = reason
            if result == ValidationResult.AMBIGUOUS:
                school.attributes["url_validation_ambiguous"] = True
                school.attributes["url_validation_ambiguous_reason"] = reason
            else:
                school.attributes.pop("url_validation_ambiguous", None)
                school.attributes.pop("url_validation_ambiguous_reason", None)
            if "mismatch" in (reason or "").lower():
                school.attributes["website_mismatch_reason"] = reason
            else:
                school.attributes.pop("website_mismatch_reason", None)

            # Keep canonical final URL on school record, including normalized scheme updates.
            if final_url and school.website_url != final_url:
                school.website_url = final_url

            if _should_clear_website_derived_data(result, reason):
                invalid_hosts = {
                    parsed.netloc.lower()
                    for parsed in (
                        urlparse(value)
                        for value in [url, final_url, school.website_url]
                        if value
                    )
                    if parsed.netloc
                }
                if invalid_hosts:
                    invalid_pages = await db.execute(
                        select(SourcePage).where(
                            SourcePage.school_id == school_id,
                            SourcePage.scrape_type == ScrapeType.WEBSITE,
                        )
                    )
                    for page in invalid_pages.scalars():
                        page_host = urlparse(page.source_url or "").netloc.lower()
                        if page_host in invalid_hosts:
                            page.is_valid = False

            # Update scrape_status based on validation result
            if result == ValidationResult.VALID:
                school.attributes["validated_website_url"] = final_url or url
                if school.scrape_status in ("pending", "failed_validate", "no_official_website"):
                    school.scrape_status = "validated"
            elif result == ValidationResult.INVALID:
                if _should_clear_website_derived_data(result, reason):
                    _clear_website_derived_school_data(school)
                school.attributes.pop("validated_website_url", None)
                if timeout_terminal:
                    school.scrape_status = "no_official_website"
                    school.website_url = None
                    logger.info(
                        "School %s marked no_official_website after %s consecutive timeout failures",
                        school_id,
                        timeout_count,
                    )
                else:
                    school.scrape_status = "failed_validate"
                    school.website_url = None
            elif result == ValidationResult.AMBIGUOUS:
                school.attributes.pop("validated_website_url", None)
                school.scrape_status = "failed_validate"
                school.website_url = None

            school.attributes = dict(school.attributes or {})

        await db.commit()

        logger.info(f"School {school_id}: URL validation {result.value} - {reason}")
