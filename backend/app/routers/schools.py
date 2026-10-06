import asyncio
import logging
import time
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from pydantic import TypeAdapter
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.models.exam_results import ExamResult
from app.models.school import School, SchoolLocation, SchoolLocationAgeGroupShift
from app.schemas.school import RelatedSchoolResponse, SchoolDetailResponse, SchoolListResponse
from app.services.school_relations import continues_to
from app.services.school_service import SchoolService

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter()

# Serializing the whole city (projection and gates for ~700 schools) takes seconds of CPU,
# while the data only changes when a refresh is published. Keep the serialized body of the
# requests the site makes for a few minutes; a restart (every deploy) clears it.
LIST_CACHE_TTL_SECONDS = 600
LIST_CACHE_MAX_ENTRIES = 32
_list_cache: dict[tuple, tuple[float, bytes]] = {}
_build_lock = asyncio.Lock()
_filters_cache: dict[tuple, tuple[float, dict[str, list[str]]]] = {}
_list_response_adapter = TypeAdapter(list[SchoolListResponse])


def _cached(cache: dict, key: tuple):
    entry = cache.get(key)
    if entry is None or time.monotonic() - entry[0] >= LIST_CACHE_TTL_SECONDS:
        return None
    return entry[1]


def _store(cache: dict, key: tuple, value) -> None:
    if len(cache) >= LIST_CACHE_MAX_ENTRIES:
        cache.clear()
    cache[key] = (time.monotonic(), value)


@router.get("", response_model=list[SchoolListResponse])
async def list_schools(
    country_code: str = Query("bg", description="Country code (ISO 3166-1 alpha-2)"),
    city: Optional[str] = Query("sofia", description="City scope; use 'all' for country-wide results"),
    age_group: Optional[str] = Query(None, description="Filter by age group"),
    school_type: Optional[str] = Query(None, description="Filter by school type (state, private, international)"),
    education_level: Optional[str] = Query(None, description="Filter by education level (nursery, kindergarten, primary, etc.)"),
    include_crossover: bool = Query(False, description="When filtering for preschool, include both kindergartens and primary schools with preschool programs"),
    language_focus: Optional[list[str]] = Query(None, description="Filter by language focus"),
    special_programs: Optional[list[str]] = Query(None, description="Filter by special programs"),
    facilities: Optional[list[str]] = Query(None, description="Filter by facilities"),
    teaching_approach: Optional[list[str]] = Query(None, description="Filter by teaching approach"),
    db: AsyncSession = Depends(get_db),
):
    """List all schools, optionally filtered by age group and/or school type."""
    try:
        # Map international to private (international is a subtype of private)
        if school_type == "international":
            school_type = "private"
        # The advanced filters are free-form lists (the site applies them client-side),
        # so those requests are not cached.
        cacheable = not any([language_focus, special_programs, facilities, teaching_approach])
        key = (
            country_code,
            SchoolService._normalize_city_filter(city),
            age_group,
            school_type,
            education_level,
            include_crossover,
        )
        if cacheable:
            body = _cached(_list_cache, key)
            if body is not None:
                return Response(content=body, media_type="application/json")

        # One build at a time, so a burst of visitors on a cold cache waits for a single
        # serialization instead of each running its own.
        async with _build_lock:
            if cacheable:
                body = _cached(_list_cache, key)
                if body is not None:
                    return Response(content=body, media_type="application/json")
            service = SchoolService(db)
            schools = await service.list_schools_filtered(
                country_code=country_code,
                city=city,
                age_group=age_group,
                school_type=school_type,
                education_level=education_level,
                include_crossover=include_crossover,
                language_focus=language_focus,
                special_programs=special_programs,
                facilities=facilities,
                teaching_approach=teaching_approach,
            )
            # Returning a Response skips FastAPI's own response_model pass, so serialize
            # through the same allowlisted model here: this is the publish boundary.
            body = _list_response_adapter.dump_json(
                _list_response_adapter.validate_python(schools, from_attributes=True),
                by_alias=True,
            )
            if cacheable:
                _store(_list_cache, key, body)
        return Response(content=body, media_type="application/json")
    except Exception as e:
        logger.error(f"Error listing schools: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="An error occurred while fetching schools. Please try again later."
        ) from e

# TODO: Rename endpoint to something more clear
@router.get("/counts", response_model=dict[str, int])
async def get_school_counts(
    country_code: str = Query("bg", description="Country code (ISO 3166-1 alpha-2)"),
    city: Optional[str] = Query("sofia", description="City scope; use 'all' for country-wide results"),
    db: AsyncSession = Depends(get_db),
):
    """Get counts of schools per age group."""
    try:
        query = (
            select(SchoolLocationAgeGroupShift.age_group, func.count(func.distinct(SchoolLocation.school_id)))
            .join(SchoolLocation, SchoolLocation.id == SchoolLocationAgeGroupShift.location_id)
            .join(School, SchoolLocation.school_id == School.id)
            .where(School.country_code == country_code)
            .where(SchoolService._listable_location_clause(SchoolLocation, city))
            .group_by(SchoolLocationAgeGroupShift.age_group)
        )
        city_clause = SchoolService._city_clause(city)
        if city_clause is not None:
            query = query.where(city_clause)
        result = await db.execute(query)
        rows = result.all()

        # Convert to dictionary with age_group string as key
        counts = {str(age_group): count for age_group, count in rows}
        return counts
    except Exception as e:
        logger.error(f"Error getting school counts: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="An error occurred while fetching school counts. Please try again later."
        ) from e


@router.get("/filters", response_model=dict[str, list[str]])
async def get_available_filters(
    country_code: str = Query("bg", description="Country code (ISO 3166-1 alpha-2)"),
    city: Optional[str] = Query("sofia", description="City scope; use 'all' for country-wide results"),
    db: AsyncSession = Depends(get_db),
):
    """Get available advanced filter options based on school attributes."""
    try:
        # Same projection cost and same change rate as the list, so the same short cache.
        key = (country_code, SchoolService._normalize_city_filter(city))
        filters = _cached(_filters_cache, key)
        if filters is not None:
            return filters
        async with _build_lock:
            filters = _cached(_filters_cache, key)
            if filters is None:
                service = SchoolService(db)
                filters = await service.get_available_filters(country_code=country_code, city=city)
                _store(_filters_cache, key, filters)
        return filters
    except Exception as e:
        logger.error(f"Error getting available filters: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="An error occurred while fetching available filters. Please try again later."
        ) from e


@router.get("/search", response_model=list[SchoolListResponse])
async def search_schools(
    q: str = Query(
        ...,
        min_length=2,
        max_length=100,
        description="Search query for school name"
    ),
    country_code: str = Query("bg", description="Country code (ISO 3166-1 alpha-2)"),
    city: Optional[str] = Query("sofia", description="City scope; use 'all' for country-wide results"),
    db: AsyncSession = Depends(get_db),
):
    """Search schools by name."""
    try:
        service = SchoolService(db)
        schools = await service.search_schools(search_query=q, country_code=country_code, city=city, limit=10)
        return schools
    except Exception as e:
        logger.error(f"Error searching schools: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="An error occurred while searching schools. Please try again later."
        ) from e


@router.get("/exam-averages")
async def get_exam_averages(
    country_code: str = Query("bg", description="Country code (ISO 3166-1 alpha-2)"),
    db: AsyncSession = Depends(get_db),
):
    """
    Calculate average exam scores across all schools for each exam type.
    Returns averages grouped by exam_type, year, and subject.
    """
    try:
        # Query canonical school-level average scores only.
        query = (
            select(
                ExamResult.exam_type,
                ExamResult.year,
                ExamResult.subject,
                func.avg(ExamResult.value).label('average')
            )
            .join(School, ExamResult.school_id == School.id)
            .where(School.country_code == country_code)
            .where(ExamResult.metric == "average_score")
            .group_by(ExamResult.exam_type, ExamResult.year, ExamResult.subject)
            .order_by(ExamResult.exam_type, ExamResult.year)
        )

        result = await db.execute(query)
        rows = result.fetchall()

        # Transform into nested structure
        averages = {}
        for row in rows:
            exam_type = row.exam_type
            year = row.year
            average_value = float(row.average)

            if exam_type not in averages:
                averages[exam_type] = {}
            if year not in averages[exam_type]:
                averages[exam_type][year] = {}

            averages[exam_type][year][row.subject] = round(average_value, 1)

        # Also calculate overall average per exam type (across all years and subjects)
        overall = {}
        for exam_type, years_data in averages.items():
            all_values = []
            for year_data in years_data.values():
                all_values.extend(year_data.values())
            if all_values:
                overall[exam_type] = round(sum(all_values) / len(all_values), 1)

        return {
            "by_year": averages,
            "overall": overall
        }

    except Exception as e:
        logger.error(f"Error calculating exam averages: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="An error occurred while calculating exam averages. Please try again later."
        ) from e


@router.get("/{school_id}", response_model=SchoolDetailResponse)
async def get_school(
    school_id: Annotated[int, Path(gt=0, description="School ID (must be positive)")],
    db: AsyncSession = Depends(get_db),
):
    """Get detailed information about a specific school."""
    try:
        service = SchoolService(db)
        school = await service.get_school_with_details(school_id)

        if not school:
            raise HTTPException(status_code=404, detail="School not found")

        response = SchoolDetailResponse.model_validate(school)
        try:
            link = await continues_to(db, school)
        except Exception:
            # The link is optional; fail closed without failing the detail page.
            logger.warning("continues_to failed for school %s", school_id, exc_info=True)
            link = None
        if link is not None:
            response.continues_to = RelatedSchoolResponse.model_validate(link)
        return response
    except HTTPException:
        # Re-raise HTTP exceptions (like 404)
        raise
    except Exception as e:
        logger.error(f"Error getting school {school_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="An error occurred while fetching school details. Please try again later."
        ) from e
