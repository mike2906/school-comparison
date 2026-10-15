from app.schemas.scraping import DiscoveredLocation, DiscoveredSchool
from app.scrapers.sources.base_adapter import BaseSourceAdapter


class DummyAdapter(BaseSourceAdapter):
    async def discover(self, limit=None, sample_ratio=0.0):
        return []


async def test_registry_rerun_keeps_pinned_locations(db_session):
    """Recreating a school's locations must not lose official or hand-corrected points."""
    from sqlalchemy import select

    from app.models import School, SchoolLocation, SchoolLocationAgeGroupShift
    from app.services.geocoding.write_gate import OFFICIAL_COORDS_TAG

    school = School(
        institutional_id="2200001",
        name_i18n={"bg": "Тестово училище"},
        country_code="bg",
        school_type="private",
        education_level="primary",
        city="sofia",
    )
    db_session.add(school)
    await db_session.flush()
    official = SchoolLocation(
        school_id=school.id,
        address_i18n={"bg": 'ул. "Ела" № 6'},
        lat=42.61,
        lng=23.31,
        is_primary=True,
        location_tags=[OFFICIAL_COORDS_TAG, "address_source=website_contact"],
    )
    # The register still lists the old address this hand correction replaced.
    corrected = SchoolLocation(
        school_id=school.id,
        address_i18n={"bg": 'ул. "Кожух планина" № 18'},
        lat=42.62,
        lng=23.32,
        is_primary=False,
        geocode_meta={
            "method": "manual_fix",
            "manual_fix": {"previous": {"address_i18n": {"bg": 'бул. "Александър Стамболийски" № 50'}}},
        },
    )
    campus = SchoolLocation(
        school_id=school.id,
        address_i18n={"bg": 'ул. "Майска роза" № 4'},
        lat=42.63,
        lng=23.33,
        is_primary=False,
        location_tags=["location_source=website_campus"],
        geocode_meta={"method": "manual_fix", "manual_fix": {"previous": {}}},
    )
    geocoded = SchoolLocation(
        school_id=school.id,
        address_i18n={"bg": 'ул. "Роза" № 3'},
        lat=42.64,
        lng=23.34,
        is_primary=False,
        geocode_meta={"method": "nominatim"},
    )
    db_session.add_all([official, corrected, campus, geocoded])
    await db_session.flush()
    db_session.add(SchoolLocationAgeGroupShift(location_id=official.id, age_group="grade_5_7"))
    await db_session.commit()
    school_id = school.id
    kept_ids = {official.id, corrected.id, campus.id}

    def source_location(address, **extra):
        return DiscoveredLocation(
            address_i18n={"bg": address},
            lat=42.70,
            lng=23.40,
            location_tags=["source=moe_registry", "coords_source=nominatim"],
            geocode_meta={"method": "nominatim", "provider": "nominatim"},
            age_groups=["grade_1_4"],
            **extra,
        )

    result = await DummyAdapter(db=db_session).upsert_schools(
        [
            DiscoveredSchool(
                institutional_id="2200001",
                country_code="bg",
                city="sofia",
                name_i18n={"bg": "Тестово училище"},
                school_type="private",
                education_level="primary",
                locations=[
                    source_location("ул. Ела №6", district="Лозенец"),
                    source_location('бул. "Александър Стамболийски" № 50', is_primary=False),
                    source_location('ул. "Роза" № 3', is_primary=False),
                    source_location('ул. "Нова" № 9'),
                ],
            )
        ]
    )
    assert result["updated"] == 1

    db_session.expire_all()
    locations = {
        loc.address_i18n["bg"]: loc
        for loc in (
            await db_session.execute(select(SchoolLocation).where(SchoolLocation.school_id == school_id))
        ).scalars()
    }
    assert set(locations) == {
        'ул. "Ела" № 6',
        'ул. "Кожух планина" № 18',
        'ул. "Майска роза" № 4',
        'ул. "Роза" № 3',
        'ул. "Нова" № 9',
    }
    pinned = [loc for loc in locations.values() if loc.id in kept_ids]
    assert sorted((loc.lat, loc.lng) for loc in pinned) == [(42.61, 23.31), (42.62, 23.32), (42.63, 23.33)]

    kept_official = locations['ул. "Ела" № 6']
    assert kept_official.district == "Лозенец"
    assert OFFICIAL_COORDS_TAG in kept_official.location_tags
    assert "coords_source=nominatim" not in kept_official.location_tags
    assert "source=moe_registry" in kept_official.location_tags
    assert locations['ул. "Кожух планина" № 18'].geocode_meta["method"] == "manual_fix"
    shifts = (
        await db_session.execute(
            select(SchoolLocationAgeGroupShift.age_group).where(
                SchoolLocationAgeGroupShift.location_id == kept_official.id
            )
        )
    ).scalars().all()
    assert shifts == ["grade_1_4"]

    # Unpinned rows are still recreated from the source, and a pinned primary stays primary.
    assert locations['ул. "Роза" № 3'].lat == 42.70
    assert [loc.address_i18n["bg"] for loc in locations.values() if loc.is_primary] == ['ул. "Ела" № 6']


async def test_registry_rerun_keeps_checked_pins_whose_address_it_still_lists(db_session):
    """A pin stage's checked pin survives a re-import of its address; a moved school's does not."""
    from sqlalchemy import select

    from app.models import School, SchoolLocation

    school = School(
        institutional_id="2200002",
        name_i18n={"bg": "Друго училище"},
        country_code="bg",
        school_type="private",
        education_level="primary",
        city="sofia",
    )
    db_session.add(school)
    await db_session.flush()
    register_pin = SchoolLocation(
        school_id=school.id,
        address_i18n={"bg": 'ул. "Ела" № 6'},
        lat=42.61,
        lng=23.31,
        is_primary=True,
        geocode_meta={"method": "address_register", "precision": "exact"},
        location_tags=["coords_source=sofia_address_register"],
    )
    # site-address replaced the registered address with the one the school's site gives.
    site_pin = SchoolLocation(
        school_id=school.id,
        address_i18n={"bg": "гр. Банкя, ул. Тенев баир 2"},
        lat=42.70,
        lng=23.13,
        is_primary=False,
        geocode_meta={
            "method": "site_address",
            "precision": "exact",
            "evidence": {"previous_address_i18n": {"bg": 'ул. "Майор Коста Паница"'}},
        },
    )
    moved = SchoolLocation(
        school_id=school.id,
        address_i18n={"bg": 'ул. "Роза" № 3'},
        lat=42.64,
        lng=23.34,
        is_primary=False,
        geocode_meta={"method": "agent_pick", "precision": "exact"},
    )
    db_session.add_all([register_pin, site_pin, moved])
    await db_session.commit()
    school_id = school.id

    def source_location(address, **extra):
        return DiscoveredLocation(
            address_i18n={"bg": address},
            lat=42.69,
            lng=23.39,
            geocode_meta={"method": "geojson_name_match", "precision": "approximate"},
            location_tags=["coords_source=geojson"],
            **extra,
        )

    await DummyAdapter(db=db_session).upsert_schools(
        [
            DiscoveredSchool(
                institutional_id="2200002",
                country_code="bg",
                city="sofia",
                name_i18n={"bg": "Друго училище"},
                school_type="private",
                education_level="primary",
                locations=[
                    source_location('ул. "Ела" № 6'),
                    source_location('ул. "Майор Коста Паница"', is_primary=False),
                    source_location('ул. "Нова" № 9', is_primary=False),
                ],
            )
        ]
    )

    db_session.expire_all()
    locations = {
        loc.address_i18n["bg"]: loc
        for loc in (
            await db_session.execute(select(SchoolLocation).where(SchoolLocation.school_id == school_id))
        ).scalars()
    }
    assert set(locations) == {'ул. "Ела" № 6', "гр. Банкя, ул. Тенев баир 2", 'ул. "Нова" № 9'}
    assert (locations['ул. "Ела" № 6'].lat, locations['ул. "Ела" № 6'].lng) == (42.61, 23.31)
    assert locations['ул. "Ела" № 6'].geocode_meta["method"] == "address_register"
    assert "coords_source=geojson" not in locations['ул. "Ела" № 6'].location_tags
    assert locations["гр. Банкя, ул. Тенев баир 2"].geocode_meta["method"] == "site_address"
    assert locations['ул. "Нова" № 9'].geocode_meta["precision"] == "approximate"
