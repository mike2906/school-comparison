import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.schemas.school import SchoolResponse
from app.services.school_service import SchoolService

logger = logging.getLogger(__name__)
settings = get_settings()
router = APIRouter()


@router.get("", response_model=list[SchoolResponse])
async def compare_schools(
    ids: Annotated[str, Query(
        ...,
        description=f"Comma-separated list of school IDs to compare (max {settings.max_schools_to_compare})",
        pattern=r"^[0-9,\s]+$"
    )],
    db: AsyncSession = Depends(get_db),
):
    """Get detailed information for multiple schools for comparison."""
    try:
        # Parse and validate IDs
        id_strings = [id_str.strip() for id_str in ids.split(",") if id_str.strip()]

        if not id_strings:
            raise HTTPException(
                status_code=400,
                detail="No school IDs provided. Please provide at least one valid school ID."
            )

        # Convert to integers and validate
        try:
            school_ids = [int(id_str) for id_str in id_strings]
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="Invalid school IDs. All IDs must be positive integers."
            )

        # Validate all IDs are positive
        if any(id_val <= 0 for id_val in school_ids):
            raise HTTPException(
                status_code=400,
                detail="Invalid school IDs. All IDs must be positive integers."
            )

        # Limit number of schools to compare
        if len(school_ids) > settings.max_schools_to_compare:
            school_ids = school_ids[:settings.max_schools_to_compare]
            logger.info(f"Truncated comparison to {settings.max_schools_to_compare} schools")

        # Fetch schools using service
        service = SchoolService(db)
        schools = await service.get_schools_by_ids(school_ids)

        # Check if any schools were found
        if not schools:
            raise HTTPException(
                status_code=404,
                detail="No schools found with the provided IDs."
            )

        # Warn if some IDs weren't found
        found_ids = {school.id for school in schools}
        missing_ids = set(school_ids) - found_ids
        if missing_ids:
            logger.warning(f"Schools not found for IDs: {missing_ids}")

        return schools

    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    except Exception as e:
        logger.error(f"Error comparing schools: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="An error occurred while fetching schools for comparison. Please try again later."
        )
