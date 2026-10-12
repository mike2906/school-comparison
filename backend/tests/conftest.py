"""Test fixtures with SQLite."""
import datetime
import types

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, get_db
from app.main import app
from app.models.country import Country
from app.models.school import School, SchoolLocation, SchoolLocationAgeGroupShift
from app.routers import schools as schools_router

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

# The date a test sees through `frozen_today`. Pricing code drops fee tables whose academic
# year is over, so a test page naming "2025/2026" only means the same thing on a fixed date.
FROZEN_TODAY = datetime.date(2026, 10, 10)


class _FrozenDate(datetime.date):
    @classmethod
    def today(cls):
        return cls(FROZEN_TODAY.year, FROZEN_TODAY.month, FROZEN_TODAY.day)


class _FrozenDateTime(datetime.datetime):
    @classmethod
    def now(cls, tz=None):
        moment = cls(FROZEN_TODAY.year, FROZEN_TODAY.month, FROZEN_TODAY.day, 12, tzinfo=datetime.timezone.utc)
        return moment.astimezone(tz) if tz else moment.replace(tzinfo=None)


@pytest.fixture
def frozen_today(monkeypatch):
    """Pin the clock that the price extraction and evidence code reads to FROZEN_TODAY."""
    from app.scrapers import extractor_helpers, price_evidence
    from app.utils import academic_year

    clock = types.SimpleNamespace(**vars(datetime))
    clock.date = _FrozenDate
    clock.datetime = _FrozenDateTime
    monkeypatch.setattr(extractor_helpers, "datetime", clock)
    monkeypatch.setattr(price_evidence, "datetime", clock)
    monkeypatch.setattr(academic_year, "date", _FrozenDate)
    return FROZEN_TODAY


@pytest.fixture(autouse=True)
def clear_school_list_cache():
    """Each test has its own database, so a cached list or filters response must not outlive the test."""
    for state in (schools_router._list_cache, schools_router._filters_cache, schools_router._refreshing):
        state.clear()
    yield
    for state in (schools_router._list_cache, schools_router._filters_cache, schools_router._refreshing):
        state.clear()


@pytest_asyncio.fixture
async def async_engine():
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(async_engine):
    async_session_maker = async_sessionmaker(
        async_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with async_session_maker() as session:
        yield session


@pytest_asyncio.fixture
async def sample_schools(db_session):
    """Sample schools for testing."""
    schools = []
    for i in range(3):
        school = School(
            name_i18n={"bg": f"Училище {i+1}", "en": f"School {i+1}"},
            country_code="bg",
            school_type="state",
            education_level="primary",
            city="sofia",
        )
        db_session.add(school)
        schools.append(school)
    await db_session.commit()

    # Refresh to get IDs
    for school in schools:
        await db_session.refresh(school)

    return schools


@pytest_asyncio.fixture
async def client(db_session):
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def seeded_db(db_session):
    """Seed database with test schools."""
    # Seed country first (FK dependency)
    bg = Country(
        code="bg",
        name_i18n={"bg": "България", "en": "Bulgaria"},
        education_config={"age_groups": [], "education_levels": [], "school_types": [], "shifts": [], "exam_types": [], "exam_subjects": [], "admission_systems": {}, "grade_scale": {"min": 2, "max": 6}, "grade_to_points_table": {}, "age_calculation_method": "enrollment_year_minus_birth_year"},
        map_config={"center": [42.6977, 23.3219], "bounds": [[42.50, 23.10], [42.86, 23.60]], "default_zoom": 12, "geocoding": {"country_codes": "bg", "city_suffix": ", София, България"}},
        supported_languages=["bg", "en"],
        default_language="bg",
        default_currency="BGN",
    )
    db_session.add(bg)
    await db_session.flush()

    # State kindergarten with multiple locations
    kg1 = School(
        name_i18n={"bg": "ДГ №1 Щастливо детство", "en": "KG #1 Happy Childhood"},
        country_code="bg",
        school_type="state",
        education_level="kindergarten",
        city="sofia",
        summary_i18n={
            "bg": {"short": "Държавна детска градина в центъра на София.", "long": "Държавна детска градина в центъра на София."},
            "en": {"short": "State kindergarten in central Sofia.", "long": "State kindergarten in central Sofia."},
        },
    )
    db_session.add(kg1)
    await db_session.flush()

    loc1 = SchoolLocation(
        school_id=kg1.id,
        address_i18n={"bg": "ул. Иван Вазов 15, София", "en": "15 Ivan Vazov St, Sofia"},
        lat=42.6977,
        lng=23.3219,
        is_primary=True,
    )
    loc2 = SchoolLocation(
        school_id=kg1.id,
        address_i18n={"bg": "ул. Граф Игнатиев 20, София", "en": "20 Graf Ignatiev St, Sofia"},
        lat=42.6900,
        lng=23.3300,
        is_primary=False,
    )
    db_session.add_all([loc1, loc2])
    await db_session.flush()

    # Add age group shifts for locations
    shift1 = SchoolLocationAgeGroupShift(
        location_id=loc1.id,
        age_group="first",
        shift="morning",
        has_organised_groups=True,
    )
    shift2 = SchoolLocationAgeGroupShift(
        location_id=loc2.id,
        age_group="preschool",
        shift="full_day",
        has_organised_groups=True,
    )
    db_session.add_all([shift1, shift2])

    # Private kindergarten
    kg2 = School(
        name_i18n={"bg": "Частна ДГ Слънчице", "en": "Private KG Sunshine"},
        country_code="bg",
        school_type="private",
        education_level="kindergarten",
        city="sofia",
        website_url="https://example.com",
        summary_i18n={
            "bg": {"short": "Модерна частна детска градина.", "long": "Модерна частна детска градина."},
            "en": {"short": "Modern private kindergarten.", "long": "Modern private kindergarten."},
        },
    )
    db_session.add(kg2)
    await db_session.flush()

    loc3 = SchoolLocation(
        school_id=kg2.id,
        address_i18n={"bg": "бул. Витоша 100, София", "en": "100 Vitosha Blvd, Sofia"},
        lat=42.6800,
        lng=23.3150,
        is_primary=True,
    )
    db_session.add(loc3)
    await db_session.flush()

    shift3 = SchoolLocationAgeGroupShift(
        location_id=loc3.id,
        age_group="first",
        shift="full_day",
    )
    db_session.add(shift3)

    # State primary school
    school1 = School(
        name_i18n={"bg": "23 СУ Фредерик Жолио-Кюри", "en": "23 SU Frederic Joliot-Curie"},
        country_code="bg",
        school_type="state",
        education_level="primary",
        city="sofia",
        summary_i18n={
            "bg": {"short": "Популярно столично училище.", "long": "Популярно столично училище."},
            "en": {"short": "Popular Sofia school.", "long": "Popular Sofia school."},
        },
    )
    db_session.add(school1)
    await db_session.flush()

    loc4 = SchoolLocation(
        school_id=school1.id,
        address_i18n={"bg": "ул. Сан Стефано 40, София", "en": "40 San Stefano St, Sofia"},
        lat=42.6850,
        lng=23.3400,
        is_primary=True,
    )
    db_session.add(loc4)
    await db_session.flush()

    shift4 = SchoolLocationAgeGroupShift(
        location_id=loc4.id,
        age_group="grade_1_4",
        shift="morning",
        has_organised_groups=True,
    )
    db_session.add(shift4)

    await db_session.commit()
    return db_session


@pytest_asyncio.fixture
async def seeded_client(seeded_db):
    """Client with seeded database."""
    async def override_get_db():
        yield seeded_db

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()
