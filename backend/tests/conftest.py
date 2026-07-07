"""Test fixtures with SQLite."""
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from app.database import Base, get_db
from app.main import app
from app.models.country import Country
from app.models.school import School, SchoolLocation, SchoolLocationAgeGroupShift

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


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
        map_config={"center": [42.6977, 23.3219], "bounds": [[42.55, 23.15], [42.85, 23.55]], "default_zoom": 12, "geocoding": {"country_codes": "bg", "city_suffix": ", София, България"}},
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
