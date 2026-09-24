from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from app.config import get_settings
from app.routers import schools, compare, countries

settings = get_settings()

app = FastAPI(
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
