import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.country import Country
from app.schemas.country import CountryConfigResponse, CountryListResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("", response_model=list[CountryListResponse])
async def list_countries(db: AsyncSession = Depends(get_db)):
    """List all supported countries."""
    try:
        result = await db.execute(select(Country))
        return result.scalars().all()
    except Exception as e:
        logger.error(f"Error listing countries: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="An error occurred while fetching countries.",
        )


@router.get("/{code}", response_model=CountryConfigResponse)
async def get_country(code: str, db: AsyncSession = Depends(get_db)):
    """Get full country configuration including education system and map config."""
    try:
        result = await db.execute(select(Country).where(Country.code == code.lower()))
        country = result.scalar_one_or_none()
        if not country:
            raise HTTPException(status_code=404, detail="Country not found")
        return country
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting country {code}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="An error occurred while fetching country configuration.",
        )
