import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import MutableHeaders

from app.config import get_settings
from app.database import get_db
from app.models.school import School
from app.routers import compare, countries, schools, search_misses

logger = logging.getLogger(__name__)
settings = get_settings()

READINESS_TIMEOUT_SECONDS = 3.0

# Every GET answer is public data that only changes when a snapshot is published, so
# Cloudflare may keep it for a few minutes (the cache rule in infra/main.tf) and most
# visits never reach the server. After a publish the edge serves the old answer for at
# most `s-maxage`; the site build asks past it (scripts/prerender.js).
EDGE_CACHE_CONTROL = "public, max-age=60, s-maxage=300"
# Only these routes' answers are public data; any other GET (health, docs, a later route)
# is marked no-store.
PUBLIC_PATH_PREFIXES = ("/schools", "/countries", "/compare")


class PublicCacheHeaders:
    """Cache headers on public GET answers; errors and every other GET are marked no-store.

    The edge does not key its cache on the Origin header, so a kept answer must suit every
    origin: it allows any origin instead of echoing the request's. The data is public and
    the site's requests carry no credentials.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in ("GET", "HEAD"):
            await self.app(scope, receive, send)
            return
        path = scope["path"]
        cacheable = any(path == prefix or path.startswith(prefix + "/") for prefix in PUBLIC_PATH_PREFIXES)

        async def send_with_cache_headers(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                # The edge keeps a GET answer with no Cache-Control for hours: say no-store.
                if cacheable and message["status"] == 200:
                    headers["Cache-Control"] = EDGE_CACHE_CONTROL
                    headers["Access-Control-Allow-Origin"] = "*"
                    if "access-control-allow-credentials" in headers:
                        del headers["Access-Control-Allow-Credentials"]
                    vary = [
                        value.strip()
                        for line in headers.getlist("vary")
                        for value in line.split(",")
                        if value.strip().lower() not in ("", "origin")
                    ]
                    if "vary" in headers:
                        del headers["Vary"]
                    if vary:
                        headers["Vary"] = ", ".join(vary)
                else:
                    headers["Cache-Control"] = "no-store"
            await send(message)

        await self.app(scope, receive, send_with_cache_headers)


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
# Last, so it is outermost and sees the CORS and gzip headers it adjusts.
app.add_middleware(PublicCacheHeaders)

app.include_router(countries.router, prefix="/countries", tags=["countries"])
app.include_router(schools.router, prefix="/schools", tags=["schools"])
app.include_router(compare.router, prefix="/compare", tags=["compare"])
app.include_router(search_misses.router, prefix="/search-misses", tags=["search-misses"])


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
