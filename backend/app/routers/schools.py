import asyncio
import logging
import time
from functools import partial
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from pydantic import TypeAdapter
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import async_session_maker, get_db
from app.models.country import Country
from app.models.exam_results import ExamResult
from app.models.school import School, SchoolLocation, SchoolLocationAgeGroupShift
from app.schemas.school import (
    RelatedSchoolResponse,
    SamePlaceSchoolResponse,
    SchoolDetailResponse,
    SchoolListResponse,
)
from app.services.country_service import get_valid_keys
from app.services.school_relations import continued_from, continues_to, same_place_by_school
from app.services.school_service import SchoolService
from app.utils.display_gating import NVO_MIN_PUPILS

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter()

# Serializing the whole city (projection and gates for ~700 schools) takes seconds of CPU,
# while the data only changes when a refresh is published. So the serialized responses the
# site asks for are kept in process and no visitor waits for a rebuild: the common ones are
# built at startup and rebuilt every TTL (`keep_cache_warm`), so they also pick up a newly
# restored snapshot; any other entry older than the TTL is still served while a background
# task rebuilds it for the next request.
LIST_CACHE_TTL_SECONDS = 600
LIST_CACHE_MAX_ENTRIES = 64
_list_cache: dict[tuple, tuple[float, bytes]] = {}
_filters_cache: dict[tuple, tuple[float, dict[str, list[str]]]] = {}
_build_lock = asyncio.Lock()
_refreshing: set[tuple] = set()
_background_tasks: set[asyncio.Task] = set()
_session_factory = async_session_maker
_list_response_adapter = TypeAdapter(list[SchoolListResponse])


def _store(cache: dict, key: tuple, value) -> None:
    # Hits move an entry to the end (`_cached_or_build`), so the first one is the least
    # recently used: a run of one-off requests cannot push out what visitors keep asking for.
    if key not in cache and len(cache) >= LIST_CACHE_MAX_ENTRIES:
        del cache[next(iter(cache))]
    cache[key] = (time.monotonic(), value)


async def _refresh(cache: dict, key: tuple, build) -> None:
    """Rebuild one entry with its own session; the old entry stays if this fails."""
    try:
        async with _build_lock:
            async with _session_factory() as db:
                value = await build(db)
            _store(cache, key, value)
    except Exception:
        logger.exception("Refreshing a cached schools response failed")
    finally:
        _refreshing.discard((id(cache), key))


async def _cached_or_build(cache: dict, key: tuple, build, db: AsyncSession):
    """The cached value for `key`, building it with `build(db)` only when there is none."""
    entry = cache.get(key)
    if entry is None:
        # One build at a time, so a burst of visitors on a cold entry waits for a single
        # build instead of each running its own.
        async with _build_lock:
            entry = cache.get(key)
            if entry is None:
                value = await build(db)
                _store(cache, key, value)
                return value
    built_at, value = entry
    if cache.get(key) is entry:
        cache[key] = cache.pop(key)
    marker = (id(cache), key)
    if time.monotonic() - built_at >= LIST_CACHE_TTL_SECONDS and marker not in _refreshing:
        _refreshing.add(marker)
        task = asyncio.create_task(_refresh(cache, key, build))
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
    return value


def _serialize_list(schools) -> bytes:
    # The handler returns a Response, which skips FastAPI's own response_model pass, so
    # serialize through the same allowlisted model here: this is the publish boundary.
    return _list_response_adapter.dump_json(
        _list_response_adapter.validate_python(schools, from_attributes=True),
        by_alias=True,
    )


async def _build_list_body(db: AsyncSession, **filters) -> bytes:
    schools = await SchoolService(db).list_schools_filtered(**filters)
    # Across the whole city, not only the filtered rows: a card names the school next
    # door even when the age filter hides it.
    try:
        same_place = await same_place_by_school(db, country_code=filters["country_code"], city=filters["city"])
    except Exception:
        # Optional grouping: the list still works without it.
        logger.warning("same_place failed for the list", exc_info=True)
        same_place = {}
    for school in schools:
        school.same_place = same_place.get(school.id, [])
    # Seconds of CPU: run it in a thread so other requests are still answered meanwhile.
    return await asyncio.to_thread(_serialize_list, schools)


def _list_key(country_code, city, age_group, school_type, education_level, include_crossover) -> tuple:
    return (
        country_code,
        SchoolService._normalize_city_filter(city),
        age_group,
        school_type,
        education_level,
        include_crossover,
    )


def _filters_key(country_code, city) -> tuple:
    return (country_code, SchoolService._normalize_city_filter(city))


async def warm_cache(country_code: str = "bg", city: str = "sofia") -> None:
    """Build what the search page asks for first: the list, its filters, each age group."""
    try:
        async with _session_factory() as db:
            country = await db.get(Country, country_code)
        age_groups = get_valid_keys(country.education_config, "age_groups") if country else []
        await _refresh(
            _filters_cache,
            _filters_key(country_code, city),
            partial(_build_filters, country_code=country_code, city=city),
        )
        variants = [{"age_group": age_group} for age_group in [None, *age_groups]]
        if "preschool" in age_groups:
            # The preschool view also asks "where": kindergarten, school, or both.
            variants += [
                {"age_group": "preschool", "education_level": "kindergarten"},
                {"age_group": "preschool", "education_level": "primary"},
                {"age_group": "preschool", "include_crossover": True},
            ]
        for variant in variants:
            filters = {
                "country_code": country_code,
                "city": city,
                "age_group": None,
                "school_type": None,
                "education_level": None,
                "include_crossover": False,
                **variant,
            }
            await _refresh(_list_cache, _list_key(**filters), partial(_build_list_body, **filters))
    except Exception:
        logger.exception("Warming the schools cache failed")


async def keep_cache_warm() -> None:
    while True:
        await warm_cache()
        await asyncio.sleep(LIST_CACHE_TTL_SECONDS)


async def _build_filters(db: AsyncSession, *, country_code, city) -> dict[str, list[str]]:
    return await SchoolService(db).get_available_filters(country_code=country_code, city=city)


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
        filters = dict(
            country_code=country_code,
            city=city,
            age_group=age_group,
            school_type=school_type,
            education_level=education_level,
            include_crossover=include_crossover,
        )
        # The advanced filters are free-form lists (the site applies them client-side),
        # so those requests are not cached.
        if any([language_focus, special_programs, facilities, teaching_approach]):
            body = await _build_list_body(
                db,
                **filters,
                language_focus=language_focus,
                special_programs=special_programs,
                facilities=facilities,
                teaching_approach=teaching_approach,
            )
        else:
            body = await _cached_or_build(
                _list_cache,
                _list_key(**filters),
                partial(_build_list_body, **filters),
                db,
            )
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
        return await _cached_or_build(
            _filters_cache,
            _filters_key(country_code, city),
            partial(_build_filters, country_code=country_code, city=city),
            db,
        )
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
    city: Optional[str] = Query("sofia", description="City scope; use 'all' for country-wide results"),
    db: AsyncSession = Depends(get_db),
):
    """
    Calculate average exam scores across the city's schools for each exam type.
    Returns averages grouped by exam_type, year, and subject.

    The UI labels this the Sofia schools' average, not a national one: it is the mean of
    the schools we hold, so it is scoped to one city to keep that label true.
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
            # Results withheld for too few pupils do not feed the benchmark either.
            .where(or_(ExamResult.pupil_count.is_(None), ExamResult.pupil_count >= NVO_MIN_PUPILS))
            .group_by(ExamResult.exam_type, ExamResult.year, ExamResult.subject)
            .order_by(ExamResult.exam_type, ExamResult.year)
        )

        city_clause = SchoolService._city_clause(city)
        if city_clause is not None:
            query = query.where(city_clause)

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
        try:
            response.continued_from = [RelatedSchoolResponse.model_validate(row) for row in await continued_from(db, school)]
        except Exception:
            logger.warning("continued_from failed for school %s", school_id, exc_info=True)
        try:
            same_place = await same_place_by_school(db, country_code=school.country_code, city=school.city)
            response.same_place = [SamePlaceSchoolResponse.model_validate(row) for row in same_place.get(school.id, [])]
        except Exception:
            logger.warning("same_place failed for school %s", school_id, exc_info=True)
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
