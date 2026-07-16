from typing import Optional, Annotated
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Path
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.models.school import School, SchoolLocation, SchoolLocationAgeGroupShift
from app.models.exam_results import ExamResult
from app.schemas.school import SchoolResponse, SchoolListResponse
from app.services.school_service import SchoolService

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter()


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
        service = SchoolService(db)
        # Map international to private (international is a subtype of private)
        if school_type == "international":
            school_type = "private"
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
        return schools
    except Exception as e:
        logger.error(f"Error listing schools: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="An error occurred while fetching schools. Please try again later."
        )

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
        )


@router.get("/filters", response_model=dict[str, list[str]])
async def get_available_filters(
    country_code: str = Query("bg", description="Country code (ISO 3166-1 alpha-2)"),
    city: Optional[str] = Query("sofia", description="City scope; use 'all' for country-wide results"),
    db: AsyncSession = Depends(get_db),
):
    """Get available advanced filter options based on school attributes."""
    try:
        service = SchoolService(db)
        return await service.get_available_filters(country_code=country_code, city=city)
    except Exception as e:
        logger.error(f"Error getting available filters: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="An error occurred while fetching available filters. Please try again later."
        )


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
        )


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
        )


@router.get("/{school_id}", response_model=SchoolResponse)
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

        return school
    except HTTPException:
        # Re-raise HTTP exceptions (like 404)
        raise
    except Exception as e:
        logger.error(f"Error getting school {school_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="An error occurred while fetching school details. Please try again later."
        )
