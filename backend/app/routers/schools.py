import asyncio
import gzip
import json
import logging
import time
from functools import partial
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from pydantic import TypeAdapter
from sqlalchemy import and_, func, or_, select
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
from app.scrapers.shared_site_check import level_family
from app.services import free_places
from app.services.country_service import get_valid_keys
from app.services.school_relations import continued_from, continues_to, same_place_by_school
from app.services.school_service import SchoolService
from app.utils import school_search
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
_list_cache: dict[tuple, tuple[float, bytes]] = {}  # gzipped bodies
_filters_cache: dict[tuple, tuple[float, dict[str, list[str]]]] = {}
# Name search and the school page also need the whole city (every school's names; the
# schools sharing a building), so those are kept the same way, per country and city.
_search_index_cache: dict[tuple, tuple[float, dict]] = {}
_same_place_cache: dict[tuple, tuple[float, dict[int, list[dict]]]] = {}
# Free kindergarten places come from a snapshot outside the DB (free_places): re-read every
# TTL, so the capture's new numbers arrive within minutes without a publish.
_free_places_cache: dict[tuple, tuple[float, bytes]] = {}
# School pages, one gzipped body each (about 6 KB), so a visit costs no query or projection.
# A publish restarts the API, which empties this cache.
# Cold builds share a few locks by id: a burst on one page waits for a single build, while
# different pages still build side by side and never behind a list build. None marks an id
# with no school, so a removed school drops out at its next refresh.
DETAIL_CACHE_MAX_ENTRIES = 2000
_detail_cache: dict[tuple, tuple[float, bytes | None]] = {}
_detail_build_locks = [asyncio.Lock() for _ in range(16)]
_build_lock = asyncio.Lock()
# The search index and same-place map are quick to build (a query and well under a second
# of projection): their cold builds take their own lock, so a search or school page never
# waits behind a list build.
_city_build_lock = asyncio.Lock()
# Requests with the advanced filters are built for that one request and not cached (the
# site applies those filters client-side). They take their own lock, so a run of them is
# built one at a time and never holds up a visitor's list, search or school page.
_uncached_build_lock = asyncio.Lock()
# Entries `warm_cache` keeps (the views the site asks for). A run of one-off requests with
# made-up filter values must not push them out of the cache.
_warm_keys: set[tuple] = set()
_refreshing: set[tuple] = set()
_background_tasks: set[asyncio.Task] = set()
_session_factory = async_session_maker
_list_response_adapter = TypeAdapter(list[SchoolListResponse])


def _store(cache: dict, key: tuple, value) -> None:
    # Hits move an entry to the end (`_cached_or_build`), so the first one is the least
    # recently used: a run of one-off requests cannot push out what visitors keep asking for.
    limit = DETAIL_CACHE_MAX_ENTRIES if cache is _detail_cache else LIST_CACHE_MAX_ENTRIES
    if key not in cache and len(cache) >= limit:
        victim = next((k for k in cache if (id(cache), k) not in _warm_keys), None)
        if victim is not None:
            del cache[victim]
    cache[key] = (time.monotonic(), value)


async def _refresh(cache: dict, key: tuple, build, *, warm: bool = False) -> None:
    """Rebuild one entry with its own session; the old entry stays if this fails."""
    if warm:
        _warm_keys.add((id(cache), key))
    try:
        async with _build_lock:
            async with _session_factory() as db:
                value = await build(db)
            _store(cache, key, value)
    except Exception:
        logger.exception("Refreshing a cached schools response failed")
    finally:
        _refreshing.discard((id(cache), key))


async def _cached_or_build(
    cache: dict, key: tuple, build, db: AsyncSession, lock: asyncio.Lock = _build_lock, max_age: float | None = None
):
    """The cached value for `key`, building it with `build(db)` only when there is none, or
    when it is older than `max_age` seconds (otherwise a stale entry is served while it
    refreshes, which also keeps it served when the database is down)."""

    def usable(entry) -> bool:
        return entry is not None and (max_age is None or time.monotonic() - entry[0] < max_age)

    entry = cache.get(key)
    if not usable(entry):
        # One build at a time, so a burst of visitors on a cold entry waits for a single
        # build instead of each running its own.
        async with lock:
            entry = cache.get(key)
            if not usable(entry):
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
    if _school_side_preschool(filters):
        # A school's preschool groups are often registered under its own kindergarten
        # next door (Д-р Петър Берон, Светлина): to a parent that is the school's preschool.
        kindergartens = await SchoolService(db).list_schools_filtered(**{**filters, "education_level": "kindergarten"})
        # The pin the school shares must be the one with the preschool groups, not
        # another branch of the kindergarten.
        schools = list(schools) + [
            kindergarten for kindergarten in kindergartens
            if any(
                level_family(other["education_level"]) == "school"
                and other["location_id"] in _preschool_location_ids(kindergarten)
                for other in same_place.get(kindergarten.id, [])
            )
        ]
    for school in schools:
        school.same_place = same_place.get(school.id, [])
    # Seconds of CPU: run it in a thread so other requests are still answered meanwhile.
    return await asyncio.to_thread(_serialize_list, schools)


async def _build_list_entry(db: AsyncSession, **filters) -> bytes:
    """The list body gzipped, once here instead of on every request (~4 MB of JSON)."""
    body = await _build_list_body(db, **filters)
    return await asyncio.to_thread(gzip.compress, body)


def _accepts_gzip(request: Request) -> bool:
    # The same test GZipMiddleware uses for every other response.
    return "gzip" in request.headers.get("accept-encoding", "")


def _gzipped_json_response(request: Request, gzipped: bytes) -> Response:
    # GZipMiddleware passes a response that already has a Content-Encoding.
    if _accepts_gzip(request):
        return Response(
            content=gzipped,
            media_type="application/json",
            headers={"Content-Encoding": "gzip", "Vary": "Accept-Encoding"},
        )
    # Rare: the edge always asks for gzip. GZipMiddleware adds the Vary.
    return Response(content=gzip.decompress(gzipped), media_type="application/json")


def _preschool_location_ids(school) -> set[int]:
    return {
        location.id for location in school.locations
        if any(row.age_group == "preschool" for row in location.age_group_shifts)
    }


def _school_side_preschool(filters) -> bool:
    return (
        filters.get("age_group") == "preschool"
        and filters.get("education_level") == "primary"
        and not filters.get("include_crossover")
    )


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
    """Build what the search page asks for first: the list, its filters, each age group,
    the name search index and the shared buildings the school page shows."""
    try:
        async with _session_factory() as db:
            country = await db.get(Country, country_code)
        age_groups = get_valid_keys(country.education_config, "age_groups") if country else []
        place_key = _filters_key(country_code, city)
        await _refresh(_filters_cache, place_key, partial(_build_filters, country_code=country_code, city=city), warm=True)
        await _refresh(
            _search_index_cache, place_key, partial(_build_search_index, country_code=country_code, city=city), warm=True
        )
        await _refresh(
            _same_place_cache, place_key, partial(_build_same_place, country_code=country_code, city=city), warm=True
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
            await _refresh(_list_cache, _list_key(**filters), partial(_build_list_entry, **filters), warm=True)
    except Exception:
        logger.exception("Warming the schools cache failed")


def default_list_warm(country_code: str = "bg", city: str = "sofia") -> bool:
    """Whether this process has the whole-city list built, the first thing every visitor asks for."""
    return _list_key(country_code, city, None, None, None, False) in _list_cache


async def keep_cache_warm() -> None:
    while True:
        await warm_cache()
        await asyncio.sleep(LIST_CACHE_TTL_SECONDS)


async def _build_filters(db: AsyncSession, *, country_code, city) -> dict[str, list[str]]:
    return await SchoolService(db).get_available_filters(country_code=country_code, city=city)


async def _build_search_index(db: AsyncSession, *, country_code, city) -> dict:
    return await SchoolService(db).search_index(country_code=country_code, city=city)


async def _build_same_place(db: AsyncSession, *, country_code, city) -> dict[int, list[dict]]:
    return await same_place_by_school(db, country_code=country_code, city=city)


@router.get("", response_model=list[SchoolListResponse])
async def list_schools(
    request: Request,
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
        # so those requests are not cached; they are built one at a time on their own lock.
        if any([language_focus, special_programs, facilities, teaching_approach]):
            async with _uncached_build_lock:
                body = await _build_list_body(
                    db,
                    **filters,
                    language_focus=language_focus,
                    special_programs=special_programs,
                    facilities=facilities,
                    teaching_approach=teaching_approach,
                )
        else:
            gzipped = await _cached_or_build(
                _list_cache,
                _list_key(**filters),
                partial(_build_list_entry, **filters),
                db,
            )
            return _gzipped_json_response(request, gzipped)
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


async def _build_free_places(db: AsyncSession, *, country_code, city) -> bytes:
    payload: dict = {"as_of": None, "schools": {}}
    if (country_code, city) == ("bg", "sofia"):
        snapshot = await free_places.fetch_snapshot(settings.free_places_url)
        meta = snapshot.get("meta") or {}
        payload.update({key: meta.get(key) for key in ("as_of", "retrieved_at", "source_url")})
        if free_places.snapshot_is_fresh(snapshot):
            schools = (await db.execute(select(School).where(School.country_code == "bg"))).scalars().all()
            by_school, unmatched = free_places.places_by_school(snapshot, schools)
            if unmatched:
                logger.info("Free places: %d ИСОДЗ buildings match no school", unmatched)
            payload["schools"] = {str(school_id): entries for school_id, entries in by_school.items()}
        else:
            logger.warning("Free places snapshot from %s is too old to show", meta.get("retrieved_at"))
    return await asyncio.to_thread(gzip.compress, json.dumps(payload, ensure_ascii=False).encode())


@router.get("/free-places")
async def get_free_places(
    request: Request,
    country_code: str = Query("bg", description="Country code (ISO 3166-1 alpha-2)"),
    city: Optional[str] = Query("sofia", description="City"),
    db: AsyncSession = Depends(get_db),
):
    """Today's free places in municipal kindergartens and ПГУ, per school id (aggregates
    from ИСОДЗ; empty when the latest snapshot is too old)."""
    try:
        gzipped = await _cached_or_build(
            _free_places_cache,
            (country_code, city),
            partial(_build_free_places, country_code=country_code, city=city),
            db,
            _city_build_lock,
            # If GitHub stays unreachable, stop serving the last answer after a few hours.
            max_age=6 * 3600,
        )
        return _gzipped_json_response(request, gzipped)
    except Exception as e:
        logger.error(f"Error getting free places: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="An error occurred while fetching free places. Please try again later."
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
        if not school_search.query_tokens(q):
            return []
        index = await _cached_or_build(
            _search_index_cache,
            _filters_key(country_code, city),
            partial(_build_search_index, country_code=country_code, city=city),
            db,
            _city_build_lock,
        )
        service = SchoolService(db)
        schools = await service.search_schools(
            search_query=q, country_code=country_code, city=city, limit=10, index=index
        )
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
            # NVO points and ДЗИ grades (2–6) sit under their own exam types and are
            # never averaged together.
            .where(
                or_(
                    and_(ExamResult.exam_type != "dzi", ExamResult.metric == "average_score"),
                    and_(ExamResult.exam_type == "dzi", ExamResult.metric == "average_grade"),
                )
            )
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

            # Grades differ in the second decimal (4.45 is "Добър", 4.50 "Много добър").
            averages[exam_type][year][row.subject] = round(average_value, 2 if exam_type == "dzi" else 1)

        # Also calculate overall average per exam type (across all years and subjects).
        # Not for ДЗИ: a mean over a dozen different subjects means nothing.
        overall = {}
        for exam_type, years_data in averages.items():
            if exam_type == "dzi":
                continue
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


class _PartialDetail(Exception):
    """A school page built without one of its optional links: shown, but never cached, so a
    passing failure does not stick (and a refresh keeps the complete page it had)."""

    def __init__(self, body: bytes):
        super().__init__("a school page link failed")
        self.body = body


async def _build_detail_body(db: AsyncSession, *, school_id: int) -> bytes | None:
    school = await SchoolService(db).get_school_with_details(school_id)
    if not school:
        return None

    response = SchoolDetailResponse.model_validate(school)
    complete = True
    try:
        link = await continues_to(db, school)
    except Exception:
        # The link is optional; fail closed without failing the detail page.
        logger.warning("continues_to failed for school %s", school_id, exc_info=True)
        link = None
        complete = False
    if link is not None:
        response.continues_to = RelatedSchoolResponse.model_validate(link)
    try:
        response.continued_from = [RelatedSchoolResponse.model_validate(row) for row in await continued_from(db, school)]
    except Exception:
        logger.warning("continued_from failed for school %s", school_id, exc_info=True)
        complete = False
    try:
        same_place = await _cached_or_build(
            _same_place_cache,
            _filters_key(school.country_code, school.city),
            partial(_build_same_place, country_code=school.country_code, city=school.city),
            db,
            _city_build_lock,
        )
        response.same_place = [SamePlaceSchoolResponse.model_validate(row) for row in same_place.get(school.id, [])]
    except Exception:
        logger.warning("same_place failed for school %s", school_id, exc_info=True)
        complete = False
    # As FastAPI would serialize it through response_model (the publish boundary).
    body = gzip.compress(response.model_dump_json(by_alias=True).encode())
    if not complete:
        raise _PartialDetail(body)
    return body


@router.get("/{school_id}", response_model=SchoolDetailResponse)
async def get_school(
    request: Request,
    school_id: Annotated[int, Path(gt=0, description="School ID (must be positive)")],
    db: AsyncSession = Depends(get_db),
):
    """Get detailed information about a specific school."""
    try:
        try:
            gzipped = await _cached_or_build(
                _detail_cache,
                (school_id,),
                partial(_build_detail_body, school_id=school_id),
                db,
                _detail_build_locks[school_id % len(_detail_build_locks)],
                # Not warmed like the list: a page nobody opened for a while is rebuilt, so
                # it never shows last week's fees or an offer that has ended.
                max_age=2 * LIST_CACHE_TTL_SECONDS,
            )
        except _PartialDetail as partial_page:
            gzipped = partial_page.body
        if gzipped is None:
            raise HTTPException(status_code=404, detail="School not found")
        return _gzipped_json_response(request, gzipped)
    except HTTPException:
        # Re-raise HTTP exceptions (like 404)
        raise
    except Exception as e:
        logger.error(f"Error getting school {school_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="An error occurred while fetching school details. Please try again later."
        ) from e
