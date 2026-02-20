"""
URL Validator (Stage 2 of scraping pipeline)

Validates school website URLs before proceeding to navigation/extraction stages.
Uses heuristic keyword matching (~90% of cases) with optional LLM validation
for ambiguous cases.
"""
import logging
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

        parsed = urlparse(cleaned)
        if parsed.scheme not in {"http", "https"}:
            return None

        # Handle edge case like "https:school.bg" (missing //).
        if not parsed.netloc and parsed.path:
            fixed = f"{parsed.scheme}://{parsed.path.lstrip('/')}"
            parsed = urlparse(fixed)
            cleaned = fixed

        if not parsed.netloc:
            return None

        if any(char.isspace() for char in parsed.netloc):
            compact_netloc = "".join(parsed.netloc.split())
            if not compact_netloc:
                return None
            cleaned = cleaned.replace(parsed.netloc, compact_netloc, 1)
            parsed = urlparse(cleaned)
            if not parsed.netloc or any(char.isspace() for char in parsed.netloc):
                return None

        return cleaned

    async def validate_url(
        self,
        url: str,
        use_llm_fallback: bool = True,
        school_name: Optional[str] = None,
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
    ) -> tuple[ValidationResult, Optional[str], Optional[str]]:
        """Run one validation pass with a specific HTTP timeout."""
        try:
            async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=True) as client:
                response = await client.get(normalized_url)
                final_url = str(response.url)  # Final URL after redirects

                if response.status_code >= 400:
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

                text_content = soup.get_text(separator=" ", strip=True).lower()
                title_text = soup.title.string if soup.title and soup.title.string else ""
                h1_text = " ".join(tag.get_text(" ", strip=True) for tag in soup.find_all("h1")[:2])
                page_context = f"{title_text} {h1_text} {text_content[:4000]}".lower()

                keyword_count = sum(1 for keyword in self.keywords if keyword.lower() in text_content)
                name_tokens = self._extract_school_name_tokens(school_name)
                name_token_hits = self._count_school_name_token_hits(page_context, name_tokens)
                directory_signals = self._count_directory_signals(final_url, text_content, soup)

                # If page looks like a directory and doesn't reference the expected school,
                # reject immediately to avoid false positives from broad listing portals.
                if directory_signals >= 1 and name_tokens and name_token_hits == 0:
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

                if name_tokens and name_token_hits == 0:
                    if use_llm_fallback:
                        return await self._llm_validate(text_content, final_url, school_name=school_name)
                    return ValidationResult.INVALID, None, "Expected school name not found on page"

                if keyword_count >= 3:
                    if directory_signals > 0:
                        if use_llm_fallback:
                            return await self._llm_validate(text_content, final_url, school_name=school_name)
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
                        return await self._llm_validate(text_content, final_url, school_name=school_name)
                    return ValidationResult.INVALID, None, "No school-related keywords found"

                if use_llm_fallback:
                    return await self._llm_validate(text_content, final_url, school_name=school_name)
                return (
                    ValidationResult.AMBIGUOUS,
                    final_url,
                    f"Ambiguous: only {keyword_count} keyword(s) found",
                )
        except httpx.TimeoutException:
            return ValidationResult.INVALID, None, "Connection timeout"
        except httpx.HTTPError as exc:
            return ValidationResult.INVALID, None, f"HTTP error: {exc}"
        except Exception as exc:
            logger.error("Error validating URL %s: %s", normalized_url, exc)
            return ValidationResult.INVALID, None, f"Validation error: {exc}"

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

    def _count_school_name_token_hits(self, page_context: str, school_tokens: list[str]) -> int:
        """Return number of expected school-name tokens present in page context."""
        if not school_tokens:
            return 0
        return sum(1 for token in school_tokens if token in page_context)

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

    def _count_directory_signals(self, url: str, text_content: str, soup: BeautifulSoup) -> int:
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
        system_prompt = """You are a website classifier. Determine if a webpage is a school website.

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

Be strict - only return true if you're confident it's an actual school's website."""

        # Truncate page text to avoid huge token counts
        truncated_text = page_text[:2000]  # ~500 words
        expected_name = school_name or "unknown"
        prompt = (
            f"URL: {url}\n"
            f"Expected school name: {expected_name}\n\n"
            f"Page content:\n{truncated_text}\n\n"
            "Is this the school's official website (not a directory/listing/profile page)?"
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
                    "Cheap LLM validation failed for %s (%s). Retrying with medium tier.",
                    url,
                    cheap_error,
                )
                try:
                    output = await self._run_llm_validation_tier(
                        tier="medium",
                        system_prompt=system_prompt,
                        prompt=prompt,
                    )
                except Exception as medium_error:
                    logger.error(f"LLM validation failed for {url}: {medium_error}")
                    # On repeated LLM failure, mark as ambiguous rather than invalid.
                    return (
                        ValidationResult.AMBIGUOUS,
                        url,
                        f"LLM validation failed after retry: {str(medium_error)}",
                    )
            else:
                logger.error(f"LLM validation failed for {url}: {cheap_error}")
                return (
                    ValidationResult.AMBIGUOUS,
                    url,
                    f"LLM validation failed: {str(cheap_error)}",
                )

        if output.is_school_website and output.confidence >= 0.7:
            return (
                ValidationResult.VALID,
                url,
                f"LLM validation: {output.reason} (confidence: {output.confidence:.2f})",
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
        """Detect model/provider failures that deserve one retry with fallback tier."""
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
            updated_attrs, timeout_count, timeout_terminal = _update_timeout_failure_state(
                attributes=school.attributes,
                result=result,
                reason=reason,
                threshold=timeout_terminal_threshold,
            )
            school.attributes = updated_attrs
            if result == ValidationResult.AMBIGUOUS:
                school.attributes["url_validation_ambiguous"] = True
                school.attributes["url_validation_ambiguous_reason"] = reason
            else:
                school.attributes.pop("url_validation_ambiguous", None)
                school.attributes.pop("url_validation_ambiguous_reason", None)

            # Keep canonical final URL on school record, including normalized scheme updates.
            if final_url and school.website_url != final_url:
                school.website_url = final_url

            # Update scrape_status based on validation result
            if result == ValidationResult.VALID:
                if school.scrape_status in ("pending", "failed_validate", "no_official_website"):
                    school.scrape_status = "validated"
            elif result == ValidationResult.INVALID:
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
            elif result == ValidationResult.AMBIGUOUS:
                if school.scrape_status in ("pending", "failed_validate", "no_official_website"):
                    school.scrape_status = "validated"

        await db.commit()

        logger.info(f"School {school_id}: URL validation {result.value} - {reason}")
