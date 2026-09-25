"""UF44: name-match guard, host-building official points, retrying legacy rejections."""
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import School, SchoolLocation
from app.services.geocoding.base import GeocodingResult
from app.services.geocoding.bg.address_match import same_building
from app.services.geocoding.service import (
    NAME_MATCH_ADDRESS_MISMATCH,
    REGISTER_ADDRESS_CONFLICT,
    GeocodingService,
    geocode_failure_is_terminal,
    same_school_shared_points,
)
from app.services.geocoding.write_gate import OFFICIAL_COORDS_TAG


class _CompositeLike:
    """GeoJSON name match first; a separate Nominatim tier behind it."""

    provider_name = "composite-mock"

    def __init__(self, name_match: GeocodingResult, nominatim: GeocodingResult):
        self.geocode = AsyncMock(return_value=name_match)
        self.nominatim_provider = type("Nom", (), {"geocode": AsyncMock(return_value=nominatim)})()
        self.geojson_provider = type("Geo", (), {"geocode": AsyncMock()})()


def _name_match(lat: float, lng: float, register_address: str) -> GeocodingResult:
    return GeocodingResult(
        lat=lat,
        lng=lng,
        success=True,
        provider="geojson_bg",
        method="geojson_name_match",
        precision="approximate",
        formatted_address=register_address,
    )


NO_RESULTS = GeocodingResult(success=False, error="No results found", provider="nominatim")


async def _school(db: AsyncSession, name: str, school_type: str = "state") -> School:
    school = School(
        name_i18n={"bg": name},
        country_code="bg",
        city="sofia",
        school_type=school_type,
        education_level="kindergarten",
    )
    db.add(school)
    await db.flush()
    return school


@pytest.mark.parametrize(
    ("first", "second", "expected"),
    [
        # Register vs location spellings of one building.
        ('район Студентски, ул. "Йозеф Валдхард" № 3', "УЛ.ЙОЗЕФ ВАЛДХАРД 3, 1700 СТОЛИЧНА", True),
        ('Район Красно село, бул. "Ген. М. Д. Скобелев" № 58', "БУЛ. ГЕН. Д. М. СКОБЕЛЕВ 58, 1606 СТОЛИЧНА", True),
        ("район Връбница, ж. к. Обеля 2, ул. 106 № 3", "УЛ.106 № 3, 1326 СТОЛИЧНА", True),
        ('район Младост, ж. к. "Младост 2", до бл. 227', "Ж.К.МЛАДОСТ2 |ДО БЛ. 227, 1799 СТОЛИЧНА", True),
        # A tenant in a host building.
        ('район Витоша, кв. Княжево, ул. "Средорек" № 3, ет. 1 и ет. 4 на ПГЕХ', 'ул."Средорек" № 3', True),
        ('гр. София 1505, кв. "Редута", ул. "Кадемлия" 15, в сградата на ПГ по транспорт', 'София, ул. "Кадемлия" №15', True),
        # Another building of the school, or another house on the same street.
        ('гр. София, кв. "Малашевци", ул. "Училищна", №10', 'гр. София, ул. "Железопътна", №22', False),
        ('бул. "Симеоновско шосе" № 59', "УЛ. ЙОЗЕФ ВАЛДХАРД 3, 1700 СТОЛИЧНА", False),
        ('ж. к. Редута, ул. "Детелин войвода" № 10', "Ж.К. РЕДУТА | УЛ. КАЛИМАНЦИ 43, 1505 СТОЛИЧНА", False),
        ("ул. Христо Ботев № 5", "ул. Христо Смирненски № 5", False),
        # A block without a comma before it is still the house number.
        ("ж.к. Младост 1 бл. 15", "ж.к. Младост 1, бл. 15", True),
        ("ж.к. Младост 1 бл. 15", "ж.к. Младост 11 бл. 5", False),
        ("ул. Бигла 56", "УЛ. БИГЛА 52, 1164 СТОЛИЧНА", False),
        # Nothing to compare: no house number, or a street on one side only.
        ('ж.к. Гоце Делчев, бл. 257Аа', "УЛ. МАЙОР ПАВЕЛ ПАВЛОВ №6, 1404 СТОЛИЧНА", False),
        ("ж. к. Люлин 6", "Ж.К. ЛЮЛИН-6 | УЛ. НИКОЛА ПОПОВ, 1336 СТОЛИЧНА", False),
        ('кв. Драгалевци, бул. "Черни връх" № 189д', "КВ. ДРАГАЛЕВЦИ | БУЛ. ЧЕРНИ ВРЪХ №, 1415 СТОЛИЧНА", False),
    ],
)
def test_same_building(first, second, expected):
    assert same_building(first, second) is expected
    assert same_building(second, first) is expected


def test_same_building_unnumbered_needs_the_same_place():
    assert not same_building("кв.Кремиковци", "КВ.КРЕМИКОВЦИ, 1849 СТОЛИЧНА")
    assert same_building("кв.Кремиковци", "КВ.КРЕМИКОВЦИ, 1849 СТОЛИЧНА", allow_unnumbered=True)
    assert not same_building('ж.к."Дружба" 1', "Ж.К.ДРУЖБА 2, 1592 СТОЛИЧНА", allow_unnumbered=True)


def test_legacy_name_match_rejection_is_not_terminal():
    """UF44: the GeoJSON tier losing its point must not block the address tiers forever."""
    assert not geocode_failure_is_terminal({
        "status": "rejected",
        "provider": "geojson_bg",
        "method": "geojson_name_match",
        "rejection_reason": "duplicate_geojson_name_match_different_address",
    })


@pytest.mark.asyncio
class TestNameMatchGuard:
    async def test_second_building_does_not_get_the_register_point(self, db_session: AsyncSession):
        """ДГ №149 shape (location 3119): the register point is the main building's."""
        school = await _school(db_session, "ДГ №149 Зорница")
        main = SchoolLocation(
            school_id=school.id,
            address_i18n={"bg": 'гр. София, ул. "Железопътна", №22'},
            lat=42.72086565,
            lng=23.34408384,
            location_tags=[OFFICIAL_COORDS_TAG],
            geocode_meta={"status": "accepted", "method": "municipal_point", "precision": "exact"},
            is_primary=True,
        )
        second = SchoolLocation(
            school_id=school.id,
            address_i18n={"bg": 'гр. София, кв. "Малашевци", ул. "Училищна", №10'},
            is_primary=False,
        )
        db_session.add_all([main, second])
        await db_session.commit()
        provider = _CompositeLike(
            _name_match(42.72090, 23.34410, "УЛ. ЖЕЛЕЗОПЪТНА 22, 1000 СТОЛИЧНА"),
            NO_RESULTS,
        )

        result = await GeocodingService(db=db_session, provider=provider).geocode_location(second)

        provider.nominatim_provider.geocode.assert_awaited_once()
        assert result.success is False
        await db_session.refresh(second)
        assert second.lat is None and second.lng is None
        assert second.geocode_meta["rejection_reason"] == "No results found"

    async def test_sibling_already_near_the_point_blocks_the_name_match(
        self, db_session: AsyncSession
    ):
        """Even when the register address reads like this location's, a sibling there wins."""
        school = await _school(db_session, "ДГ №149 Зорница")
        main = SchoolLocation(
            school_id=school.id,
            address_i18n={"bg": 'гр. София, ул. "Железопътна", №22'},
            lat=42.72086565,
            lng=23.34408384,
            is_primary=True,
        )
        second = SchoolLocation(
            school_id=school.id,
            address_i18n={"bg": 'гр. София, кв. "Малашевци", ул. "Училищна", №10'},
            is_primary=False,
        )
        db_session.add_all([main, second])
        await db_session.commit()
        provider = _CompositeLike(
            _name_match(42.72090, 23.34410, "КВ. МАЛАШЕВЦИ | УЛ. УЧИЛИЩНА 10, 1000 СТОЛИЧНА"),
            NO_RESULTS,
        )

        await GeocodingService(db=db_session, provider=provider).geocode_location(second)

        provider.nominatim_provider.geocode.assert_awaited_once()
        await db_session.refresh(second)
        assert second.lat is None

    async def test_point_taken_falls_back_to_the_address(self, db_session: AsyncSession):
        """A point held elsewhere sends the location to Nominatim in the same run."""
        other = await _school(db_session, "Частно училище Светлина", "private")
        kindergarten = await _school(db_session, "Частна детска градина Светлина", "private")
        holder = SchoolLocation(
            school_id=other.id,
            address_i18n={"bg": 'бул. "Симеоновско шосе" № 59'},
            lat=42.64872,
            lng=23.33691,
            is_primary=True,
        )
        location = SchoolLocation(
            school_id=kindergarten.id,
            address_i18n={"bg": 'район Студентски, ул. "Йозеф Валдхард" № 3'},
            geocode_meta={
                "status": "rejected",
                "provider": "geojson_bg",
                "rejection_reason": "duplicate_geojson_name_match_different_address",
            },
            is_primary=True,
        )
        db_session.add_all([holder, location])
        await db_session.commit()
        provider = _CompositeLike(
            _name_match(42.64872, 23.33691, "УЛ. ЙОЗЕФ ВАЛДХАРД 3, 1700 СТОЛИЧНА"),
            GeocodingResult(
                lat=42.6491,
                lng=23.3372,
                success=True,
                provider="nominatim",
                method="nominatim_address",
                precision="exact",
            ),
        )

        # Not forced: the legacy rejection no longer blocks a routine run.
        result = await GeocodingService(db=db_session, provider=provider).geocode_location(location)

        assert result.success is True
        await db_session.refresh(location)
        assert (location.lat, location.lng) == (42.6491, 23.3372)
        assert location.geocode_meta["method"] == "nominatim_fallback"

    async def test_matching_register_address_keeps_the_name_match(self, db_session: AsyncSession):
        school = await _school(db_session, "Частна детска градина Букара", "private")
        location = SchoolLocation(
            school_id=school.id,
            address_i18n={"bg": 'кв. Бояна, ул. "Букара" № 15'},
            is_primary=True,
        )
        db_session.add(location)
        await db_session.commit()
        provider = _CompositeLike(_name_match(42.65669, 23.24947, "УЛ.БУКАРА 15, 1619 СТОЛИЧНА"), NO_RESULTS)

        result = await GeocodingService(db=db_session, provider=provider).geocode_location(location)

        assert result.success is True
        provider.nominatim_provider.geocode.assert_not_awaited()
        assert location.geocode_meta["method"] == "geojson_name_match"

    async def test_without_an_address_tier_the_mismatch_is_recorded(self, db_session: AsyncSession):
        school = await _school(db_session, "ДГ №149 Зорница")
        location = SchoolLocation(
            school_id=school.id,
            address_i18n={"bg": 'гр. София, кв. "Малашевци", ул. "Училищна", №10'},
            is_primary=True,
        )
        # Not geocoded yet: a sibling counts even without a pin.
        main = SchoolLocation(
            school_id=school.id,
            address_i18n={"bg": 'гр. София, ул. "Железопътна", №22'},
            is_primary=False,
        )
        db_session.add_all([location, main])
        await db_session.commit()
        provider = type("GeoJSONOnly", (), {"geocode": AsyncMock()})()
        provider.provider_name = "mock"
        provider.geocode.return_value = _name_match(42.7209, 23.3441, "УЛ. ЖЕЛЕЗОПЪТНА 22, 1000 СТОЛИЧНА")

        result = await GeocodingService(db=db_session, provider=provider).geocode_location(location)

        assert result.error == NAME_MATCH_ADDRESS_MISMATCH
        assert location.lat is None
        assert not geocode_failure_is_terminal(location.geocode_meta)


    async def test_single_location_school_keeps_the_register_point(self, db_session: AsyncSession):
        """Stored addresses of some state schools are older than the register's (17 СУ)."""
        school = await _school(db_session, "17 СУ Дамян Груев")
        location = SchoolLocation(
            school_id=school.id,
            address_i18n={"bg": "ж.к.Западен парк, ул. Суходолска-2 №134"},
            is_primary=True,
        )
        db_session.add(location)
        await db_session.commit()
        provider = _CompositeLike(
            _name_match(42.7051, 23.2829, "Ж.К.ЗАПАДЕН ПАРК | УЛ. САВА МИХАЙЛОВ №64, 1373 СТОЛИЧНА"),
            NO_RESULTS,
        )

        result = await GeocodingService(db=db_session, provider=provider).geocode_location(location)

        assert result.success is True
        provider.nominatim_provider.geocode.assert_not_awaited()
        assert (location.lat, location.lng) == (42.7051, 23.2829)

    async def test_duplicate_row_spelling_is_not_a_second_building(self, db_session: AsyncSession):
        """"№ 43" and "№ 43, партер" are one building: both rows get the register point."""
        school = await _school(db_session, "Частна детска градина Доверие", "private")
        first = SchoolLocation(
            school_id=school.id,
            address_i18n={"bg": 'ж. к. Редута, ул. "Калиманци" № 43'},
            lat=42.68855,
            lng=23.35684,
            geocode_meta={"status": "accepted", "method": "geojson_name_match", "precision": "approximate"},
            is_primary=True,
        )
        second = SchoolLocation(
            school_id=school.id,
            address_i18n={"bg": 'ж. к. Редута, ул. "Калиманци" № 43, партер'},
            is_primary=False,
        )
        db_session.add_all([first, second])
        await db_session.commit()
        provider = _CompositeLike(
            _name_match(42.68855, 23.35684, "Ж.К. РЕДУТА | УЛ. КАЛИМАНЦИ 43, 1505 СТОЛИЧНА"),
            NO_RESULTS,
        )

        result = await GeocodingService(db=db_session, provider=provider).geocode_location(second)

        assert result.success is True
        assert (second.lat, second.lng) == (42.68855, 23.35684)


@pytest.mark.asyncio
class TestOfficialPointOfHostBuilding:
    async def _host(self, db: AsyncSession, address: str, lat: float, lng: float) -> SchoolLocation:
        host_school = await _school(db, "Професионална гимназия по екология и биотехнологии")
        host = SchoolLocation(
            school_id=host_school.id,
            address_i18n={"bg": address},
            lat=lat,
            lng=lng,
            location_tags=["source=moe", OFFICIAL_COORDS_TAG],
            is_primary=True,
        )
        db.add(host)
        return host

    async def _tenant(self, db: AsyncSession) -> SchoolLocation:
        school = await _school(db, "Частно средно училище Петко Р. Славейков", "private")
        tenant = SchoolLocation(
            school_id=school.id,
            address_i18n={"bg": 'район Витоша, кв. Княжево, ул. "Средорек" № 3, ет. 1 и ет. 4 на ПГЕХ'},
            geocode_meta={"status": "failed", "provider": "nominatim", "rejection_reason": "No results found"},
            is_primary=True,
        )
        db.add(tenant)
        return tenant

    async def test_tenant_reuses_the_host_official_point(self, db_session: AsyncSession):
        await self._host(db_session, 'ул."Средорек" № 3', 42.66628, 23.25357)
        tenant = await self._tenant(db_session)
        await db_session.commit()
        provider = _CompositeLike(_name_match(0, 0, ""), NO_RESULTS)

        result = await GeocodingService(db=db_session, provider=provider).geocode_location(
            tenant, force=True
        )

        assert result.success is True
        provider.geocode.assert_not_awaited()
        assert (tenant.lat, tenant.lng) == (42.66628, 23.25357)
        assert tenant.geocode_meta["method"] == "official_point_same_address"
        assert tenant.geocode_meta["precision"] == "exact"
        assert OFFICIAL_COORDS_TAG not in (tenant.location_tags or [])

    async def _stale_address_school(self, db: AsyncSession) -> SchoolLocation:
        """1174 shape: stored address is another school's building; register says elsewhere."""
        await self._host(db, 'ул. "Стара планина" № 13', 42.69966, 23.33225)
        school = await _school(db, "Профилирана гимназия Михай Еминеску")
        location = SchoolLocation(
            school_id=school.id,
            address_i18n={"bg": 'ул. "Стара планина" №13'},
            lat=42.6981,
            lng=23.3237,
            geocode_meta={"status": "accepted", "method": "geojson_name_match", "precision": "approximate"},
            is_primary=True,
        )
        db.add(location)
        await db.commit()
        return location

    async def test_register_address_conflict_leaves_the_location_unchanged(
        self, db_session: AsyncSession
    ):
        location = await self._stale_address_school(db_session)
        before = dict(location.geocode_meta)
        provider = _CompositeLike(_name_match(0, 0, ""), NO_RESULTS)
        provider.geojson_provider.geocode.return_value = _name_match(
            42.6981, 23.3237, "УЛ.Г.С.РАКОВСКИ № 20, 1202 СТОЛИЧНА"
        )

        result = await GeocodingService(db=db_session, provider=provider).geocode_location(
            location, force=True
        )

        assert result.error == REGISTER_ADDRESS_CONFLICT
        provider.geocode.assert_not_awaited()
        provider.nominatim_provider.geocode.assert_not_awaited()
        await db_session.refresh(location)
        assert (location.lat, location.lng) == (42.6981, 23.3237)
        assert location.geocode_meta == before

    async def test_register_agreeing_with_the_address_allows_the_host_point(
        self, db_session: AsyncSession
    ):
        location = await self._stale_address_school(db_session)
        provider = _CompositeLike(_name_match(0, 0, ""), NO_RESULTS)
        provider.geojson_provider.geocode.return_value = _name_match(
            42.6981, 23.3237, "УЛ. СТАРА ПЛАНИНА № 13, 1000 СТОЛИЧНА"
        )

        result = await GeocodingService(db=db_session, provider=provider).geocode_location(
            location, force=True
        )

        assert result.method == "official_point_same_address"
        assert (location.lat, location.lng) == (42.69966, 23.33225)

    async def test_other_house_number_is_not_the_host(self, db_session: AsyncSession):
        await self._host(db_session, 'ул."Средорек" № 5', 42.66628, 23.25357)
        tenant = await self._tenant(db_session)
        await db_session.commit()
        provider = _CompositeLike(_name_match(0, 0, ""), NO_RESULTS)
        provider.geocode.return_value = NO_RESULTS

        result = await GeocodingService(db=db_session, provider=provider).geocode_location(
            tenant, force=True
        )

        provider.geocode.assert_awaited_once()
        assert result.success is False
        assert tenant.lat is None

    async def test_two_different_official_points_at_the_address_are_ambiguous(
        self, db_session: AsyncSession
    ):
        await self._host(db_session, 'ул."Средорек" № 3', 42.66628, 23.25357)
        await self._host(db_session, 'ул. "Средорек" № 3', 42.67000, 23.26000)
        tenant = await self._tenant(db_session)
        await db_session.commit()
        provider = _CompositeLike(_name_match(0, 0, ""), NO_RESULTS)
        provider.geocode.return_value = NO_RESULTS

        await GeocodingService(db=db_session, provider=provider).geocode_location(tenant, force=True)

        provider.geocode.assert_awaited_once()
        assert tenant.lat is None


@pytest.mark.asyncio
async def test_same_school_shared_points_reports_different_addresses(db_session: AsyncSession):
    school = await _school(db_session, "ДГ №149 Зорница")
    other = await _school(db_session, "ДГ №150")
    db_session.add_all([
        SchoolLocation(school_id=school.id, address_i18n={"bg": 'ул. "Железопътна" №22'},
                       lat=42.72086, lng=23.34408, is_primary=True),
        SchoolLocation(school_id=school.id, address_i18n={"bg": 'кв. "Малашевци", ул. "Училищна" №10'},
                       lat=42.72090, lng=23.34410, is_primary=False),
        # Same address twice (duplicate rows) and another school nearby: not reported.
        SchoolLocation(school_id=other.id, address_i18n={"bg": 'бул. "Стамболийски" № 50'},
                       lat=42.72088, lng=23.34409, is_primary=True),
        SchoolLocation(school_id=other.id, address_i18n={"bg": "бул. „Стамболийски“ № 50"},
                       lat=42.72088, lng=23.34409, is_primary=False),
    ])
    await db_session.commit()

    pairs = await same_school_shared_points(db_session, city="sofia")

    assert [(a.school_id, b.school_id) for a, b, _ in pairs] == [(school.id, school.id)]
