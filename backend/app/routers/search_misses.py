import logging

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services import search_misses

logger = logging.getLogger(__name__)

router = APIRouter()


class SearchMiss(BaseModel):
    q: str = Field(max_length=200)


@router.post("", status_code=204)
async def record_search_miss(body: SearchMiss, db: AsyncSession = Depends(get_db)):
    """Count a name search that found no school (search_misses.py says what is kept)."""
    try:
        await search_misses.record_miss(db, body.q)
    except Exception:
        # Counting is best effort: a parent's page never waits on it or sees it fail.
        logger.warning("Could not count a search miss", exc_info=True)
    return Response(status_code=204)
