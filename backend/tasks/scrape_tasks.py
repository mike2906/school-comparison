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
    validate_batch + run_spot_checks
        ↓
    summarize_batch → summarize_school (per school)

Each stage is independently runnable and idempotent.
"""
import asyncio
import logging
from typing import Optional
from celery import group, chain
from celery.exceptions import Retry

from tasks import celery_app

logger = logging.getLogger(__name__)


# =============================================================================
# Helper Functions (Celery tasks must be sync, wrap async logic)
# =============================================================================

def run_async(coro):
    """Run an async coroutine in a sync Celery task."""
    return asyncio.run(coro)


def _should_wait_for_group() -> bool:
    """Only block on group results in eager (synchronous) mode."""
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
        raise self.retry(exc=exc)


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
        if _should_wait_for_group():
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
        raise self.retry(exc=exc)


async def _get_schools_for_website_discovery(country_code, city, limit):
    """Get school IDs that are eligible for website discovery."""
    from app.database import async_session_maker
    from app.models import School
    from sqlalchemy import select

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
        raise self.retry(exc=exc)


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
        if _should_wait_for_group():
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
        raise self.retry(exc=exc)


async def _get_schools_for_url_validation(country_code, city, limit):
    """Get school IDs that need URL validation."""
    from app.database import async_session_maker
    from app.models import School
    from sqlalchemy import select

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
        raise self.retry(exc=exc)


async def _validate_school_url_async(school_id, country_code):
    """Async implementation of validate_school_url."""
    from app.database import async_session_maker
    from app.models import School
    from app.scrapers.url_validator import validate_school_url as validate_url
    from sqlalchemy import select

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
):
    """Navigate and classify website pages for validated schools."""
    try:
        school_ids = run_async(_get_schools_for_navigation(country_code, city, limit, include_navigated))

        if not school_ids:
            return {"message": "No schools to navigate", "processed": 0}

        job = group(navigate_school_website.s(school_id, country_code) for school_id in school_ids)
        result = job.apply_async()
        if _should_wait_for_group():
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
        raise self.retry(exc=exc)


async def _get_schools_for_navigation(country_code, city, limit, include_navigated: bool = False):
    """Get validated school IDs eligible for website navigation."""
    from app.database import async_session_maker
    from app.models import School
    from sqlalchemy import select

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
        raise self.retry(exc=exc)


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
        if _should_wait_for_group():
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
        raise self.retry(exc=exc)


async def _get_schools_for_extraction(country_code, city, limit):
    """Get school IDs eligible for extraction."""
    from app.database import async_session_maker
    from app.models import School
    from sqlalchemy import select

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
        raise self.retry(exc=exc)


async def _extract_school_async(school_id, country_code):
    """Async implementation of extract_school_data."""
    from app.database import async_session_maker
    from app.scrapers.extractor import extract_school

    async with async_session_maker() as db:
        return await extract_school(db=db, school_id=school_id, country_code=country_code)


# =============================================================================
# Stage 6: Data Validation (Placeholder - Phase 2)
# =============================================================================

@celery_app.task(name="tasks.validate_batch")
def validate_batch(country_code: str = "bg", city: Optional[str] = "sofia"):
    """Validate extracted data (Phase 2)."""
    logger.info("Validate batch - Phase 2 implementation")
    return {"message": "Not implemented yet - Phase 2"}


@celery_app.task(name="tasks.run_spot_checks")
def run_spot_checks(country_code: str = "bg", sample_size: int = 10, city: Optional[str] = "sofia"):
    """Run spot-check validation with capable model (Phase 2)."""
    logger.info("Spot checks - Phase 2 implementation")
    return {"message": "Not implemented yet - Phase 2"}


# =============================================================================
# Stage 7: Summarization (Placeholder - Phase 2)
# =============================================================================

@celery_app.task(name="tasks.summarize_batch")
def summarize_batch(country_code: str = "bg", city: Optional[str] = "sofia", limit: Optional[int] = None):
    """Generate summaries for schools (Phase 2)."""
    logger.info("Summarize batch - Phase 2 implementation")
    return {"message": "Not implemented yet - Phase 2"}


@celery_app.task(
    name="tasks.summarize_school",
    rate_limit="10/m",  # LLM API rate limit
)
def summarize_school(school_id: int):
    """Generate summary for a single school (Phase 2)."""
    logger.info(f"Summarize school {school_id} - Phase 2 implementation")
    return {"school_id": school_id, "message": "Not implemented yet - Phase 2"}


# =============================================================================
# Independent Tasks
# =============================================================================

@celery_app.task(name="tasks.scrape_nvo_results")
def scrape_nvo_results(country_code: str = "bg", year: Optional[int] = None, city: Optional[str] = "sofia"):
    """Scrape NVO exam results from government platform (Phase 3)."""
    logger.info("NVO scraping - Phase 3 implementation")
    return {"message": "Not implemented yet - Phase 3"}


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
        discover_websites_batch.si(country_code=country_code, city=city, limit=limit),
        validate_urls_batch.si(country_code=country_code, city=city, limit=limit),
        navigate_batch.si(country_code=country_code, city=city, limit=limit),
        extract_batch.si(country_code=country_code, city=city, limit=limit),
        # Phase 2 stages would be added here:
        # group(validate_batch.si(country_code=country_code, city=city), run_spot_checks.si(country_code=country_code, city=city)),
        # summarize_batch.si(country_code=country_code, city=city, limit=limit),
    )

    result = pipeline.apply_async()

    return {
        "pipeline_id": result.id,
        "message": "Pipeline started (Stages 1-5 enabled; 6-7 are Phase 2)",
    }


@celery_app.task(name="tasks.run_stage")
def run_stage(
    stage: str,
    country_code: str = "bg",
    city: Optional[str] = "sofia",
    school_ids: Optional[list[int]] = None,
    limit: Optional[int] = None,
):
    """
    Run a specific pipeline stage.

    Args:
        stage: Stage name (discover, discover-websites, validate-urls, navigate, extract, validate-data, summarize)
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
    }

    if stage not in stage_map:
        return {"error": f"Unknown stage: {stage}"}

    task = stage_map[stage]

    # Run the task
    result = task.apply_async(
        kwargs={
            "country_code": country_code,
            "city": city,
            "limit": limit,
        }
    )

    return {
        "stage": stage,
        "task_id": result.id,
        "message": f"Stage '{stage}' started",
    }
