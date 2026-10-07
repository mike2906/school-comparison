import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.models.school import School
from app.routers import compare, countries, schools

logger = logging.getLogger(__name__)
settings = get_settings()

READINESS_TIMEOUT_SECONDS = 3.0

@asynccontextmanager
async def lifespan(app: FastAPI):
    # In the background: the API answers (and passes its health checks) while this runs.
    warming = asyncio.create_task(schools.keep_cache_warm())
    yield
    warming.cancel()


app = FastAPI(
    lifespan=lifespan,
    title="School Comparison API",
    description="API for discovering, filtering, and comparing kindergartens and schools",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# The school list is ~2 MB of JSON; gzip cuts it to ~10 % for parents on mobile data.
app.add_middleware(GZipMiddleware, minimum_size=1024)

app.include_router(countries.router, prefix="/countries", tags=["countries"])
app.include_router(schools.router, prefix="/schools", tags=["schools"])
app.include_router(compare.router, prefix="/compare", tags=["compare"])


@app.get("/health")
async def health_check():
    return {"status": "healthy"}


@app.get("/ready")
async def readiness_check(db: AsyncSession = Depends(get_db)):
    """Ready only when the DB answers and a school snapshot is loaded.

    `/health` is liveness (no dependencies). An unreachable DB, a missing schema and an
    empty `schools` table all return 503, without error detail.
    """
    try:
        school_id = await asyncio.wait_for(
            db.scalar(select(School.id).limit(1)), timeout=READINESS_TIMEOUT_SECONDS
        )
    except Exception:
        logger.exception("Readiness check failed")
        school_id = None
    if school_id is None:
        return JSONResponse(status_code=503, content={"status": "not_ready"})
    return {"status": "ready"}
