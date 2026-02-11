"""
URL Validator (Stage 2 of scraping pipeline)

Validates school website URLs before proceeding to navigation/extraction stages.
Uses heuristic keyword matching (~90% of cases) with optional LLM validation
for ambiguous cases.
"""
import logging
from typing import Optional
from enum import Enum
import httpx
from bs4 import BeautifulSoup

from app.ai.client import create_agent
from pydantic import BaseModel

logger = logging.getLogger(__name__)


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
    HTTP_TIMEOUT = 10.0

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
    BLOCKED_DOMAINS = [
        "facebook.com",
        "instagram.com",
        "bg-mamma.com",
        "twitter.com",
        "linkedin.com",
        "youtube.com",
    ]

    def __init__(self, country_code: str = "bg"):
        """
        Initialize the URL validator.

        Args:
            country_code: Country code for locale-specific keywords
        """
        self.country_code = country_code
        self.keywords = self._load_keywords(country_code)

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

    async def validate_url(
        self,
        url: str,
        use_llm_fallback: bool = True,
    ) -> tuple[ValidationResult, Optional[str], Optional[str]]:
        """
        Validate a school website URL.

        Args:
            url: URL to validate
            use_llm_fallback: Whether to use LLM for ambiguous cases

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
        # Step 1: Check if domain is blocked
        if self._is_blocked_domain(url):
            return ValidationResult.INVALID, None, "Blocked domain (social media/forum)"

        # Step 2: HTTP check
        try:
            async with httpx.AsyncClient(timeout=self.HTTP_TIMEOUT, follow_redirects=True) as client:
                response = await client.get(url)
                final_url = str(response.url)  # Final URL after redirects

                # Check status code
                if response.status_code >= 400:
                    return (
                        ValidationResult.INVALID,
                        None,
                        f"HTTP error: {response.status_code}",
                    )

                # Step 3: Heuristic keyword check
                html_content = response.text
                soup = BeautifulSoup(html_content, "html.parser")

                # Extract text content
                text_content = soup.get_text(separator=" ", strip=True).lower()

                # Count keyword matches
                keyword_count = sum(1 for keyword in self.keywords if keyword.lower() in text_content)

                # Decision based on keyword density
                if keyword_count >= 3:
                    # Strong signal - likely a school website
                    return (
                        ValidationResult.VALID,
                        final_url,
                        f"Keyword match: {keyword_count} school terms found",
                    )
                elif keyword_count == 0:
                    # No school keywords - likely not a school
                    if use_llm_fallback:
                        # Still check with LLM to be sure
                        return await self._llm_validate(text_content, final_url)
                    else:
                        return (
                            ValidationResult.INVALID,
                            None,
                            "No school-related keywords found",
                        )
                else:
                    # Ambiguous (1-2 keywords) - needs LLM validation
                    if use_llm_fallback:
                        return await self._llm_validate(text_content, final_url)
                    else:
                        return (
                            ValidationResult.AMBIGUOUS,
                            final_url,
                            f"Ambiguous: only {keyword_count} keyword(s) found",
                        )

        except httpx.TimeoutException:
            return ValidationResult.INVALID, None, "Connection timeout"
        except httpx.HTTPError as e:
            return ValidationResult.INVALID, None, f"HTTP error: {str(e)}"
        except Exception as e:
            logger.error(f"Error validating URL {url}: {e}")
            return ValidationResult.INVALID, None, f"Validation error: {str(e)}"

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

    async def _llm_validate(
        self,
        page_text: str,
        url: str,
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
        try:
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

            agent = create_agent(
                tier="cheap",
                system_prompt=system_prompt,
                result_type=URLValidationOutput,
            )

            # Truncate page text to avoid huge token counts
            truncated_text = page_text[:2000]  # ~500 words

            # Run agent
            result = await agent.run(
                f"URL: {url}\n\nPage content:\n{truncated_text}\n\nIs this a school website?"
            )

            output: URLValidationOutput = result.data

            if output.is_school_website and output.confidence >= 0.7:
                return (
                    ValidationResult.VALID,
                    url,
                    f"LLM validation: {output.reason} (confidence: {output.confidence:.2f})",
                )
            else:
                return (
                    ValidationResult.INVALID,
                    None,
                    f"LLM validation: {output.reason} (confidence: {output.confidence:.2f})",
                )

        except Exception as e:
            logger.error(f"LLM validation failed for {url}: {e}")
            # On LLM failure, mark as ambiguous rather than invalid
            return (
                ValidationResult.AMBIGUOUS,
                url,
                f"LLM validation failed: {str(e)}",
            )


async def validate_school_url(
    school_id: int,
    url: str,
    country_code: str = "bg",
    update_db: bool = True,
) -> tuple[ValidationResult, Optional[str], Optional[str]]:
    """
    Validate a school's website URL and optionally update the database.

    This is the main entry point for URL validation in the pipeline.

    Args:
        school_id: School ID
        url: URL to validate
        country_code: Country code for locale-specific validation
        update_db: Whether to update SourcePage and School in the database

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
    result, final_url, reason = await validator.validate_url(url)

    if update_db:
        await _update_validation_result(school_id, url, result, final_url, reason)

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
            source_page.is_valid = result == ValidationResult.VALID
        else:
            # Create new SourcePage
            from app.scrapers.base import BaseScraper

            source_page = SourcePage(
                school_id=school_id,
                scrape_type=ScrapeType.WEBSITE,
                source_url=url,
                content_hash=BaseScraper.compute_hash(""),  # Empty for now
                is_valid=result == ValidationResult.VALID,
            )
            db.add(source_page)

        # Update school
        school_result = await db.execute(select(School).where(School.id == school_id))
        school = school_result.scalar_one_or_none()

        if school:
            # Update website_url if redirected
            if final_url and final_url != url:
                school.website_url = final_url

            # Update scrape_status based on validation result
            if result == ValidationResult.VALID:
                if school.scrape_status == "pending":
                    school.scrape_status = "validated"
            elif result == ValidationResult.INVALID:
                school.scrape_status = "failed_validate"

        await db.commit()

        logger.info(f"School {school_id}: URL validation {result.value} - {reason}")
