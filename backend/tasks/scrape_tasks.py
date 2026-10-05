"""
Celery tasks for the scraping pipeline.

Task Graph:
    discover_schools (batch)
        ↓
    discover_websites_batch → discover_school_website (per school)
        ↓
    validate_urls_batch → validate_school_url (per school)
        ↓
    navigate_batch → navigate_school_website (per school)
        ↓
    extract_batch → extract_school_data (per school)
        ↓
    validate_batch → run_spot_checks
        ↓
    summarize_batch → summarize_school (per school)

Each stage is independently runnable and idempotent.
"""
import asyncio
import logging
import random
from typing import Optional

from celery import chain, group

from tasks import celery_app

logger = logging.getLogger(__name__)


# =============================================================================
# Helper Functions (Celery tasks must be sync, wrap async logic)
# =============================================================================

def run_async(coro):
    """Run an async coroutine in a sync Celery task."""
    return asyncio.run(coro)


def _should_wait_for_group(wait_for_completion: bool = False) -> bool:
    """Block on group results when explicitly requested or in eager mode."""
    if wait_for_completion:
        return True
    return bool(getattr(celery_app.conf, "task_always_eager", False))


# =============================================================================
# Stage 1: Discovery
# =============================================================================

@celery_app.task(
    bind=True,
    name="tasks.discover_schools",
    max_retries=3,
    default_retry_delay=60,
)
def discover_schools(
    self,
    country_code: str = "bg",
    adapter_name: Optional[str] = None,
    city: Optional[str] = "sofia",
    limit: Optional[int] = None,
):
    """
    Discover schools from government registries and other sources.

    This runs all adapters for the given country/city unless a specific
    adapter is specified.

    Args:
        country_code: Country code (e.g., "bg")
        adapter_name: Specific adapter to run (optional)
        city: City to filter by (optional)
        limit: Limit number of schools to discover (optional)

    Returns:
        Dict with discovery results
    """
    try:
        return run_async(_discover_schools_async(country_code, adapter_name, city, limit))
    except Exception as exc:
        logger.exception(f"Discovery failed: {exc}")
        raise self.retry(exc=exc) from exc


async def _discover_schools_async(country_code, adapter_name, city, limit):
    """Async implementation of discover_schools."""
    from app.database import async_session_maker
    from app.scrapers.sources import get_adapter, get_adapters_for_country

    async with async_session_maker() as db:
        if adapter_name:
            # Run specific adapter
            adapter_class = get_adapter(adapter_name)
            adapter = adapter_class(db=db)
            result = await adapter.run(limit=limit)
            return {
                "adapter": adapter_name,
                "created": result["created"],
                "updated": result["updated"],
                "skipped": result["skipped"],
            }
        else:
            # Run all adapters for country/city
            adapter_classes = get_adapters_for_country(country_code, city)

            total_created = 0
            total_updated = 0
            total_skipped = 0

            for adapter_class in adapter_classes:
                adapter = adapter_class(db=db)
                result = await adapter.run(limit=limit)

                total_created += result["created"]
                total_updated += result["updated"]
                total_skipped += result["skipped"]

            return {
                "country": country_code,
                "city": city,
                "adapters": len(adapter_classes),
                "created": total_created,
                "updated": total_updated,
                "skipped": total_skipped,
            }


# =============================================================================
# Stage 2: Website Discovery
# =============================================================================

@celery_app.task(
    bind=True,
    name="tasks.discover_websites_batch",
    max_retries=3,
)
def discover_websites_batch(
    self,
    country_code: str = "bg",
    city: Optional[str] = "sofia",
    limit: Optional[int] = None,
    wait_for_completion: bool = False,
):
    """
    Discover/normalize website URLs for schools before URL validation.

    This is a fan-out task that creates individual website discovery tasks.
    """
    try:
        school_ids = run_async(_get_schools_for_website_discovery(country_code, city, limit))

        if not school_ids:
            return {"message": "No schools to discover websites for", "processed": 0}

        job = group(discover_school_website_task.s(school_id, country_code) for school_id in school_ids)
        result = job.apply_async()
        if _should_wait_for_group(wait_for_completion):
            results = result.get()
            discovered_count = sum(1 for item in results if item.get("found"))
            updated_count = sum(1 for item in results if item.get("updated"))
            unchanged_count = len(results) - updated_count
            return {
                "processed": len(school_ids),
                "found": discovered_count,
                "updated": updated_count,
                "unchanged": unchanged_count,
            }

        return {
            "processed": len(school_ids),
            "job_id": result.id,
        }
    except Exception as exc:
        logger.exception(f"Website discovery batch failed: {exc}")
        raise self.retry(exc=exc) from exc


async def _get_schools_for_website_discovery(country_code, city, limit):
    """Get school IDs that are eligible for website discovery."""
    from sqlalchemy import select

    from app.database import async_session_maker
    from app.models import School

    async with async_session_maker() as db:
        query = select(School.id).where(
            School.country_code == country_code,
            School.scrape_status.in_(["pending", "failed_validate"]),
        )

        if city:
            query = query.where(School.city == city)

        if limit:
            query = query.limit(limit)

        result = await db.execute(query)
        return [row[0] for row in result.all()]


@celery_app.task(
    bind=True,
    name="tasks.discover_school_website",
    max_retries=3,
    default_retry_delay=30,
    rate_limit="20/m",
)
def discover_school_website_task(self, school_id: int, country_code: str = "bg"):
    """Discover/normalize website URL for a single school."""
    try:
        return run_async(_discover_school_website_async(school_id, country_code))
    except Exception as exc:
        logger.exception(f"Website discovery failed for school {school_id}: {exc}")
        raise self.retry(exc=exc) from exc


async def _discover_school_website_async(school_id, country_code):
    """Async implementation of discover_school_website."""
    from app.database import async_session_maker
    from app.scrapers.website_discovery import discover_school_website

    async with async_session_maker() as db:
        return await discover_school_website(db=db, school_id=school_id, country_code=country_code)


# =============================================================================
# Stage 3: URL Validation
# =============================================================================

@celery_app.task(
    bind=True,
    name="tasks.validate_urls_batch",
    max_retries=3,
)
def validate_urls_batch(
    self,
    country_code: str = "bg",
    city: Optional[str] = "sofia",
    limit: Optional[int] = None,
    wait_for_completion: bool = False,
):
    """
    Validate URLs for all schools in pending or failed_validate status.

    This is a fan-out task that creates individual validation tasks.

    Args:
        country_code: Country code
        city: City to filter by
        limit: Limit number of schools to process

    Returns:
        Dict with validation results
    """
    try:
        # Get school IDs that need validation
        school_ids = run_async(_get_schools_for_url_validation(country_code, city, limit))

        if not school_ids:
            return {"message": "No schools to validate", "processed": 0}

        # Create a group of validation tasks (fan-out pattern)
        job = group(validate_school_url.s(school_id, country_code) for school_id in school_ids)
        result = job.apply_async()
        if _should_wait_for_group(wait_for_completion):
            results = result.get()
            valid_count = sum(1 for r in results if r.get("valid"))
            invalid_count = sum(1 for r in results if not r.get("valid"))
            return {
                "processed": len(school_ids),
                "valid": valid_count,
                "invalid": invalid_count,
            }

        return {
            "processed": len(school_ids),
            "job_id": result.id,
        }

    except Exception as exc:
        logger.exception(f"URL validation batch failed: {exc}")
        raise self.retry(exc=exc) from exc


async def _get_schools_for_url_validation(country_code, city, limit):
    """Get school IDs that need URL validation."""
    from sqlalchemy import select

    from app.database import async_session_maker
    from app.models import School

    async with async_session_maker() as db:
        query = select(School.id).where(
            School.country_code == country_code,
            School.scrape_status.in_(["pending", "failed_validate"]),
            School.website_url.isnot(None),
        )

        if city:
            query = query.where(School.city == city)

        if limit:
            query = query.limit(limit)

        result = await db.execute(query)
        return [row[0] for row in result.all()]


@celery_app.task(
    bind=True,
    name="tasks.validate_school_url",
    max_retries=3,
    default_retry_delay=30,
    rate_limit="20/m",  # 20 URLs per minute (different domains)
)
def validate_school_url(self, school_id: int, country_code: str = "bg"):
    """
    Validate a single school's website URL.

    Args:
        school_id: School ID
        country_code: Country code

    Returns:
        Dict with validation result
    """
    try:
        return run_async(_validate_school_url_async(school_id, country_code))
    except Exception as exc:
        logger.exception(f"URL validation failed for school {school_id}: {exc}")
        raise self.retry(exc=exc) from exc


async def _validate_school_url_async(school_id, country_code):
    """Async implementation of validate_school_url."""
    from sqlalchemy import select

    from app.database import async_session_maker
    from app.models import School
    from app.scrapers.url_validator import extract_validation_aliases
    from app.scrapers.url_validator import validate_school_url as validate_url

    async with async_session_maker() as db:
        result = await db.execute(select(School).where(School.id == school_id))
        school = result.scalar_one_or_none()

        if not school or not school.website_url:
            return {"school_id": school_id, "valid": False, "reason": "No URL"}

        validation_result, final_url, reason = await validate_url(
            school_id=school_id,
            url=school.website_url,
            country_code=country_code,
            update_db=True,
            school_name=(school.name_i18n or {}).get("bg") or (school.name_i18n or {}).get("en"),
            school_aliases=extract_validation_aliases(school.attributes),
        )

        return {
            "school_id": school_id,
            "valid": validation_result.value == "valid",
            "reason": reason,
            "final_url": final_url,
        }


# =============================================================================
# Stage 4: Navigation
# =============================================================================

@celery_app.task(
    bind=True,
    name="tasks.navigate_batch",
    max_retries=3,
)
def navigate_batch(
    self,
    country_code: str = "bg",
    city: Optional[str] = "sofia",
    limit: Optional[int] = None,
    include_navigated: bool = False,
    wait_for_completion: bool = False,
):
    """Navigate and classify website pages for validated schools."""
    try:
        school_ids = run_async(_get_schools_for_navigation(country_code, city, limit, include_navigated))

        if not school_ids:
            return {"message": "No schools to navigate", "processed": 0}

        job = group(navigate_school_website.s(school_id, country_code) for school_id in school_ids)
        result = job.apply_async()
        if _should_wait_for_group(wait_for_completion):
            results = result.get()
            success_count = sum(1 for item in results if item.get("success"))
            failed_count = len(results) - success_count
            pages_found = sum(item.get("pages_found", 0) for item in results if item.get("success"))
            return {
                "processed": len(school_ids),
                "successful": success_count,
                "failed": failed_count,
                "pages_found": pages_found,
            }

        return {
            "processed": len(school_ids),
            "job_id": result.id,
        }
    except Exception as exc:
        logger.exception(f"Navigation batch failed: {exc}")
        raise self.retry(exc=exc) from exc


async def _get_schools_for_navigation(country_code, city, limit, include_navigated: bool = False):
    """Get validated school IDs eligible for website navigation."""
    from sqlalchemy import select

    from app.database import async_session_maker
    from app.models import School

    async with async_session_maker() as db:
        statuses = ["validated", "navigated"] if include_navigated else ["validated"]
        query = select(School.id).where(
            School.country_code == country_code,
            School.scrape_status.in_(statuses),
            School.website_url.isnot(None),
        )

        if city:
            query = query.where(School.city == city)

        if limit:
            query = query.limit(limit)

        result = await db.execute(query)
        return [row[0] for row in result.all()]


@celery_app.task(
    bind=True,
    name="tasks.navigate_school_website",
    max_retries=3,
    default_retry_delay=60,
    rate_limit="3/m",  # Keep conservative while crawler is simple.
)
def navigate_school_website(self, school_id: int, country_code: str = "bg"):
    """Navigate a single school website and persist discovered pages."""
    try:
        return run_async(_navigate_school_website_async(school_id, country_code))
    except Exception as exc:
        logger.exception(f"Navigation failed for school {school_id}: {exc}")
        raise self.retry(exc=exc) from exc


async def _navigate_school_website_async(school_id: int, country_code: str = "bg"):
    """Async implementation of navigate_school_website."""
    from app.database import async_session_maker
    from app.scrapers.navigator import navigate_school

    async with async_session_maker() as db:
        return await navigate_school(db=db, school_id=school_id, country_code=country_code)


# =============================================================================
# Stage 5: Extraction
# =============================================================================


@celery_app.task(
    bind=True,
    name="tasks.extract_batch",
    max_retries=3,
)
def extract_batch(
    self,
    country_code: str = "bg",
    city: Optional[str] = "sofia",
    limit: Optional[int] = None,
    wait_for_completion: bool = False,
):
    """
    Extract structured data from school websites (Phase 2).

    This is a fan-out task that creates individual extraction tasks.
    """
    try:
        school_ids = run_async(_get_schools_for_extraction(country_code, city, limit))

        if not school_ids:
            return {"message": "No schools to extract", "processed": 0}

        job = group(extract_school_data.s(school_id, country_code) for school_id in school_ids)
        result = job.apply_async()
        if _should_wait_for_group(wait_for_completion):
            results = result.get()
            extracted_count = sum(1 for item in results if item.get("status") == "extracted")
            failed_count = len(results) - extracted_count
            pricing_items = sum(item.get("pricing_count", 0) for item in results)
            return {
                "processed": len(school_ids),
                "extracted": extracted_count,
                "failed": failed_count,
                "pricing_items": pricing_items,
            }

        return {
            "processed": len(school_ids),
            "job_id": result.id,
        }
    except Exception as exc:
        logger.exception(f"Extraction batch failed: {exc}")
        raise self.retry(exc=exc) from exc


async def _get_schools_for_extraction(country_code, city, limit):
    """Get school IDs eligible for extraction."""
    from sqlalchemy import select

    from app.database import async_session_maker
    from app.models import School

    async with async_session_maker() as db:
        # Retry failed extractions by default in next batch run
        query = select(School.id).where(
            School.country_code == country_code,
            School.scrape_status.in_(["navigated", "extraction_failed"]),
            School.website_url.isnot(None),
        )

        if city:
            query = query.where(School.city == city)

        if limit:
            query = query.limit(limit)

        result = await db.execute(query)
        return [row[0] for row in result.all()]


@celery_app.task(
    bind=True,
    name="tasks.extract_school_data",
    max_retries=3,
    default_retry_delay=60,
    rate_limit="10/m",  # LLM API rate limit
)
def extract_school_data(self, school_id: int, country_code: str = "bg"):
    """Extract data from a single school."""
    try:
        return run_async(_extract_school_async(school_id, country_code))
    except Exception as exc:
        logger.exception(f"Extraction failed for school {school_id}: {exc}")
        raise self.retry(exc=exc) from exc


async def _extract_school_async(school_id, country_code):
    """Async implementation of extract_school_data."""
    from app.database import async_session_maker
    from app.scrapers.extractor import extract_school

    async with async_session_maker() as db:
        return await extract_school(db=db, school_id=school_id, country_code=country_code)


# =============================================================================
# Stage 6: Data Validation
# =============================================================================

@celery_app.task(
    bind=True,
    name="tasks.validate_batch",
    max_retries=3,
)
def validate_batch(
    self,
    country_code: str = "bg",
    city: Optional[str] = "sofia",
    limit: Optional[int] = None,
    force_validate: bool = False,
    wait_for_completion: bool = False,
):
    """Validate extracted data for all extracted schools."""
    try:
        school_ids = run_async(_get_schools_for_validation(country_code, city, limit, force_validate))

        if not school_ids:
            return {"message": "No schools to validate", "processed": 0}

        job = group(validate_school_data_task.s(school_id, country_code) for school_id in school_ids)
        result = job.apply_async()
        if _should_wait_for_group(wait_for_completion):
            results = result.get(timeout=600, propagate=False)
            ok_count = 0
            needs_review_count = 0
            failed_count = 0
            for item in results:
                if isinstance(item, Exception):
                    failed_count += 1
                    continue
                if item.get("status") == "ok":
                    ok_count += 1
                elif item.get("status") == "needs_review":
                    needs_review_count += 1
                else:
                    failed_count += 1
            return {
                "processed": len(school_ids),
                "ok": ok_count,
                "needs_review": needs_review_count,
                "failed": failed_count,
            }

        return {
            "processed": len(school_ids),
            "job_id": result.id,
        }
    except Exception as exc:
        logger.exception("Validation batch failed: %s", exc)
        raise self.retry(exc=exc) from exc


async def _get_schools_for_validation(
    country_code: str,
    city: Optional[str],
    limit: Optional[int],
    force_validate: bool = False,
) -> list[int]:
    """Get school IDs eligible for Stage 6 deterministic validation."""
    from sqlalchemy import select

    from app.database import async_session_maker
    from app.models import School
    from app.scrapers.validator import has_current_validation_report

    async with async_session_maker() as db:
        query = select(School.id, School.attributes).where(
            School.country_code == country_code,
            School.scrape_status.in_(["extracted", "summarized"]),
        )

        if city:
            query = query.where(School.city == city)

        if limit:
            query = query.limit(limit)

        result = await db.execute(query)
        rows = result.all()
        if force_validate:
            return [row[0] for row in rows]
        return [row[0] for row in rows if not has_current_validation_report(row[1], schema_version=1)]


@celery_app.task(
    bind=True,
    name="tasks.validate_school_data",
    max_retries=2,
    default_retry_delay=30,
    rate_limit="15/m",
)
def validate_school_data_task(self, school_id: int, country_code: str = "bg"):
    """Run Stage 6 deterministic validation for one school."""
    try:
        return run_async(_validate_school_data_async(school_id, country_code))
    except Exception as exc:
        logger.exception("Validation failed for school %s: %s", school_id, exc)
        raise self.retry(exc=exc) from exc


async def _validate_school_data_async(school_id: int, country_code: str = "bg"):
    """Async implementation of validate_school_data_task."""
    from app.database import async_session_maker
    from app.scrapers.validator import validate_school_data

    async with async_session_maker() as db:
        return await validate_school_data(
            db=db,
            school_id=school_id,
            country_code=country_code,
            run_spot_check=False,
        )


@celery_app.task(
    bind=True,
    name="tasks.run_spot_checks",
    max_retries=2,
)
def run_spot_checks(
    self,
    country_code: str = "bg",
    sample_size: Optional[int] = None,
    city: Optional[str] = "sofia",
    limit: Optional[int] = None,
    wait_for_completion: bool = False,
):
    """Run sampled spot-check validation with capable model."""
    try:
        school_ids = run_async(_sample_school_ids_for_spot_checks(country_code, city, limit, sample_size))
        if not school_ids:
            return {
                "message": "No schools selected for spot-checks (requires current Stage 6 validation reports)",
                "processed": 0,
            }

        job = group(run_spot_check_task.s(school_id, country_code) for school_id in school_ids)
        result = job.apply_async()
        if _should_wait_for_group(wait_for_completion):
            results = result.get(timeout=600, propagate=False)
            checked_count = 0
            discrepancy_count = 0
            contradiction_count = 0
            omission_count = 0
            unsupported_count = 0
            failed_count = 0
            for item in results:
                if isinstance(item, Exception):
                    failed_count += 1
                    continue
                if item.get("status") == "checked":
                    checked_count += 1
                    if item.get("has_discrepancy"):
                        discrepancy_count += 1
                    kind_counts = item.get("kind_counts") or {}
                    contradiction_count += int(kind_counts.get("contradiction", 0))
                    omission_count += int(kind_counts.get("omission", 0))
                    unsupported_count += int(kind_counts.get("unsupported", 0))
                else:
                    failed_count += 1
            discrepancy_rate = (discrepancy_count / checked_count) if checked_count else 0.0
            contradiction_rate = (contradiction_count / checked_count) if checked_count else 0.0

            from app.config import get_settings

            threshold = float(get_settings().spot_check_discrepancy_threshold)
            if checked_count and contradiction_rate > threshold:
                logger.info(
                    "Spot-check monitoring alert: contradiction rate %.1f%% exceeded advisory threshold %.1f%%",
                    contradiction_rate * 100,
                    threshold * 100,
                )

            return {
                "processed": len(school_ids),
                "checked": checked_count,
                "discrepancies": discrepancy_count,
                "contradictions": contradiction_count,
                "omissions": omission_count,
                "unsupported": unsupported_count,
                "failed": failed_count,
                "discrepancy_rate": round(discrepancy_rate, 4),
                "contradiction_rate": round(contradiction_rate, 4),
            }

        return {
            "processed": len(school_ids),
            "job_id": result.id,
        }
    except Exception as exc:
        logger.exception("Spot-check batch failed: %s", exc)
        raise self.retry(exc=exc) from exc


async def _sample_school_ids_for_spot_checks(
    country_code: str,
    city: Optional[str],
    limit: Optional[int],
    sample_size: Optional[int],
) -> list[int]:
    """Choose already-validated school IDs for sampled spot-checks."""
    from sqlalchemy import select

    from app.config import get_settings
    from app.database import async_session_maker
    from app.models import School
    from app.scrapers.validator import has_current_validation_report

    configured_sample_size = int(get_settings().spot_check_sample_size)
    effective_sample_size = configured_sample_size if sample_size is None else int(sample_size)

    async with async_session_maker() as db:
        query = select(School.id, School.attributes).where(
            School.country_code == country_code,
            School.scrape_status.in_(["extracted", "summarized"]),
        )

        if city:
            query = query.where(School.city == city)

        if limit:
            query = query.limit(limit)

        result = await db.execute(query)
        rows = result.all()
        school_ids = sorted(
            row[0] for row in rows if has_current_validation_report(row[1], schema_version=1)
        )

    if effective_sample_size == 0:
        return []
    if effective_sample_size < 0:
        # -1 sentinel means "all extracted/summarized schools from this selection".
        return school_ids
    if effective_sample_size >= len(school_ids):
        return school_ids
    return sorted(random.sample(school_ids, effective_sample_size))


@celery_app.task(
    bind=True,
    name="tasks.run_spot_check_school",
    max_retries=2,
    default_retry_delay=30,
    rate_limit="10/m",
)
def run_spot_check_task(self, school_id: int, country_code: str = "bg"):
    """Run Stage 6 capable-model spot-check for one school."""
    try:
        return run_async(_run_spot_check_async(school_id, country_code))
    except Exception as exc:
        logger.exception("Spot-check failed for school %s: %s", school_id, exc)
        raise self.retry(exc=exc) from exc


async def _run_spot_check_async(school_id: int, country_code: str = "bg"):
    """Async implementation of run_spot_check_task."""
    from app.database import async_session_maker
    from app.scrapers.validator import run_spot_check_for_school

    async with async_session_maker() as db:
        return await run_spot_check_for_school(db=db, school_id=school_id, country_code=country_code)


# =============================================================================
# Stage 7: Summarization
# =============================================================================

@celery_app.task(
    bind=True,
    name="tasks.summarize_batch",
    max_retries=2,
)
def summarize_batch(
    self,
    country_code: str = "bg",
    city: Optional[str] = "sofia",
    limit: Optional[int] = None,
    wait_for_completion: bool = False,
):
    """Generate summaries for eligible schools."""
    try:
        school_ids = run_async(_get_schools_for_summarization(country_code, city, limit))
        if not school_ids:
            return {"message": "No schools to summarize", "processed": 0}

        job = group(summarize_school.s(school_id, country_code) for school_id in school_ids)
        result = job.apply_async()
        if _should_wait_for_group(wait_for_completion):
            results = result.get(timeout=600, propagate=False)
            summarized_count = 0
            skipped_count = 0
            failed_count = 0
            input_tokens = 0
            output_tokens = 0
            token_cost_usd = 0.0
            for item in results:
                if isinstance(item, Exception):
                    failed_count += 1
                    continue
                status = item.get("status")
                if status == "summarized":
                    summarized_count += 1
                    input_tokens += int(item.get("input_tokens", 0) or 0)
                    output_tokens += int(item.get("output_tokens", 0) or 0)
                    token_cost_usd += float(item.get("token_cost_usd", 0.0) or 0.0)
                elif status == "skipped":
                    skipped_count += 1
                else:
                    failed_count += 1
            return {
                "processed": len(school_ids),
                "summarized": summarized_count,
                "skipped": skipped_count,
                "failed": failed_count,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "token_cost_usd": round(token_cost_usd, 6),
            }

        return {
            "processed": len(school_ids),
            "job_id": result.id,
        }
    except Exception as exc:
        logger.exception("Summarization batch failed: %s", exc)
        raise self.retry(exc=exc) from exc


@celery_app.task(
    bind=True,
    name="tasks.summarize_school",
    max_retries=2,
    default_retry_delay=30,
    rate_limit="10/m",  # LLM API rate limit
)
def summarize_school(self, school_id: int, country_code: str = "bg"):
    """Generate summary for a single school."""
    try:
        return run_async(_summarize_school_async(school_id, country_code))
    except Exception as exc:
        logger.exception("Summarization failed for school %s: %s", school_id, exc)
        raise self.retry(exc=exc) from exc


async def _get_schools_for_summarization(
    country_code: str,
    city: Optional[str],
    limit: Optional[int],
) -> list[int]:
    """Get school IDs eligible for Stage 7 summarization."""
    from app.database import async_session_maker
    from app.scrapers.summarizer import get_schools_requiring_summary

    async with async_session_maker() as db:
        schools = await get_schools_requiring_summary(
            db=db,
            country_code=country_code,
            city=city,
            limit=limit,
        )
        return [school.id for school in schools]


async def _summarize_school_async(school_id: int, country_code: str = "bg"):
    """Async implementation of summarize_school task."""
    from app.database import async_session_maker
    from app.scrapers.summarizer import summarize_school as summarize_school_stage

    async with async_session_maker() as db:
        return await summarize_school_stage(
            db=db,
            school_id=school_id,
            country_code=country_code,
        )


# =============================================================================
# Independent Tasks
# =============================================================================

@celery_app.task(name="tasks.scrape_nvo_results")
def scrape_nvo_results(
    country_code: str = "bg",
    year: Optional[int] = None,
    city: Optional[str] = "sofia",
    history_years: int = 5,
    exam_types: Optional[list[str]] = None,
    school_ids: Optional[list[int]] = None,
):
    """Import official NVO exam results."""
    return run_async(
        _scrape_nvo_results_async(
            country_code=country_code,
            year=year,
            city=city,
            history_years=history_years,
            exam_types=exam_types,
            school_ids=school_ids,
        )
    )


async def _scrape_nvo_results_async(
    *,
    country_code: str = "bg",
    year: Optional[int] = None,
    city: Optional[str] = "sofia",
    history_years: int = 5,
    exam_types: Optional[list[str]] = None,
    school_ids: Optional[list[int]] = None,
):
    """Async implementation of NVO import task."""
    from app.database import async_session_maker
    from app.scrapers.nvo_results import import_nvo_results

    async with async_session_maker() as db:
        return await import_nvo_results(
            db=db,
            country_code=country_code,
            city=city,
            year=year,
            history_years=history_years,
            exam_types=exam_types,
            school_ids=school_ids,
        )


# =============================================================================
# Orchestration Tasks
# =============================================================================

@celery_app.task(name="tasks.run_full_pipeline")
def run_full_pipeline(
    country_code: str = "bg",
    city: Optional[str] = "sofia",
    limit: Optional[int] = None,
):
    """
    Run the full scraping pipeline (all stages).

    This creates a chain of tasks that run sequentially.

    Args:
        country_code: Country code
        city: City to filter by
        limit: Limit number of schools to process

    Returns:
        Pipeline execution ID
    """
    # Create a chain of stage tasks.
    # Use immutable signatures (.si) so each task receives only explicit kwargs,
    # not the previous task's return payload.
    pipeline = chain(
        discover_schools.si(country_code=country_code, city=city, limit=limit),
        discover_websites_batch.si(
            country_code=country_code, city=city, limit=limit, wait_for_completion=True
        ),
        validate_urls_batch.si(
            country_code=country_code, city=city, limit=limit, wait_for_completion=True
        ),
        navigate_batch.si(
            country_code=country_code, city=city, limit=limit, wait_for_completion=True
        ),
        extract_batch.si(
            country_code=country_code, city=city, limit=limit, wait_for_completion=True
        ),
        validate_batch.si(
            country_code=country_code, city=city, limit=limit, wait_for_completion=True
        ),
        run_spot_checks.si(
            country_code=country_code, city=city, limit=limit, wait_for_completion=True
        ),
        summarize_batch.si(
            country_code=country_code, city=city, limit=limit, wait_for_completion=True
        ),
    )

    result = pipeline.apply_async()

    return {
        "pipeline_id": result.id,
        "message": "Pipeline started (Stages 1-7 enabled)",
    }


@celery_app.task(name="tasks.run_stage")
def run_stage(
    stage: str,
    country_code: str = "bg",
    city: Optional[str] = "sofia",
    school_ids: Optional[list[int]] = None,
    limit: Optional[int] = None,
    year: Optional[int] = None,
    history_years: int = 5,
    exam_types: Optional[list[str]] = None,
):
    """
    Run a specific pipeline stage.

    Args:
        stage: Stage name (discover, discover-websites, validate-urls, navigate, extract, validate-data, summarize, nvo)
        country_code: Country code
        city: City to filter by
        school_ids: Specific school IDs to process (optional)
        limit: Limit number of schools to process

    Returns:
        Stage execution result
    """
    stage_map = {
        "discover": discover_schools,
        "discover-websites": discover_websites_batch,
        "validate-urls": validate_urls_batch,
        "navigate": navigate_batch,
        "extract": extract_batch,
        "validate-data": validate_batch,
        "summarize": summarize_batch,
        "nvo": scrape_nvo_results,
    }

    if stage not in stage_map:
        return {"error": f"Unknown stage: {stage}"}

    task = stage_map[stage]

    # Run the task
    if stage == "nvo":
        kwargs = {
            "country_code": country_code,
            "city": city,
            "year": year,
            "history_years": history_years,
            "exam_types": exam_types,
            "school_ids": school_ids,
        }
    else:
        kwargs = {
            "country_code": country_code,
            "city": city,
            "limit": limit,
        }

    result = task.apply_async(kwargs=kwargs)

    return {
        "stage": stage,
        "task_id": result.id,
        "message": f"Stage '{stage}' started",
    }
