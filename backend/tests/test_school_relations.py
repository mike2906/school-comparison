"""UF42c: computed kindergarten ↔ school links (continues_to, continued_from, same_place)."""

import pytest

from app.models.school import School, SchoolLocation
from app.services.school_relations import (
    Institution,
    PlacePin,
    brand,
    brand_key,
    continues_to_target,
    link_rejection,
    same_place_groups,
    shared_brand_key,
)
from app.utils.website_data import WEBSITE_DATA_WITHHELD_KEY

KG_NAME = '"ЧАСТНА ДЕТСКА ГРАДИНА КАНАДСКО МЕЧЕ" ООД'
SCHOOL_NAME = '"Частно основно училище Канадско мече" ООД'
VALIDATED = {
    "data_validation": {"_schema_version": 1, "validated_at": "2026-09-01T00:00:00+00:00", "status": "ok"},
    "display_name_i18n": {"bg": "Maple Bear Sofia", "en": "Maple Bear Sofia"},
}


def check(action, url, previous_url=None):
    """A shared-site check record as `apply_decision` stores it."""
    return {
        "shared_site_check": {
            "checked_at": "2026-09-25T12:00:00+00:00",
            "action": action,
            "reason": "test",
            "previous_url": previous_url or url,
            "new_url": url,
        }
    }


def inst(id, name, level, url, status="extracted", city="sofia", attributes=None):
    return Institution(
        id=id,
        name=name,
        education_level=level,
        city=city,
        website_url=url,
        scrape_status=status,
        # Default: the shared-site check kept this URL.
        attributes=check("keep", url) if attributes is None else attributes,
    )


# 556's shape: the check replaced the school site with the kindergarten campus; pending.
KG = inst(
    1, KG_NAME, "kindergarten", "https://sofia-kindergarten.maplebear.bg/", status="pending",
    attributes=check("replace", "https://sofia-kindergarten.maplebear.bg/", "https://sofia-school.maplebear.bg/en/"),
)
SCHOOL = inst(2, SCHOOL_NAME, "lower_secondary", "https://sofia-school.maplebear.bg/")


class TestRule:
    def test_brand_strips_legal_form_and_type_words(self):
        assert brand_key(brand(KG_NAME)) == brand_key(brand(SCHOOL_NAME)) == "канадскомече"

    def test_shared_brand_is_the_shortest_contained_key(self):
        assert shared_brand_key(["британика", "среднобританика"]) == "британика"
        assert shared_brand_key(["британика", "дружба"]) is None

    def test_maple_bear_shape_links(self):
        assert link_rejection(KG, SCHOOL) is None
        assert continues_to_target(KG, [KG, SCHOOL]) == 2

    def test_only_kindergarten_to_school(self):
        other_kg = inst(3, KG_NAME, "nursery", "https://x.maplebear.bg/")
        assert link_rejection(SCHOOL, KG) == "source is not kindergarten-level"
        assert link_rejection(KG, other_kg) == "target is not school-level"

    @pytest.mark.parametrize("status", ["failed_validate", "no_official_website"])
    def test_withheld_school_site_blocks(self, status):
        withheld = inst(2, SCHOOL_NAME, "lower_secondary", "https://sofia-school.maplebear.bg/", status=status)
        assert link_rejection(KG, withheld) == "school website: withheld"

    def test_withheld_kindergarten_site_blocks(self):
        withheld = inst(1, KG_NAME, "kindergarten", None, status="failed_validate")
        assert link_rejection(withheld, SCHOOL) == "kindergarten website: withheld"
        assert continues_to_target(withheld, [SCHOOL]) is None

    def test_shared_site_withhold_record_for_the_same_site_blocks(self):
        attrs = check("withhold", None, "https://sofia-school.maplebear.bg/")
        school = inst(2, SCHOOL_NAME, "lower_secondary", "https://sofia-school.maplebear.bg/", attributes=attrs)
        assert link_rejection(KG, school) == "school website: no shared-site check decision"

    def test_pending_url_without_a_check_record_blocks(self):
        unchecked = inst(1, KG_NAME, "kindergarten", "https://sofia-kindergarten.maplebear.bg/",
                         status="pending", attributes={})
        assert link_rejection(unchecked, SCHOOL) == "kindergarten website: no shared-site check decision"
        assert link_rejection(KG, inst(2, SCHOOL_NAME, "primary", "https://sofia-school.maplebear.bg/",
                                       attributes={})) == "school website: no shared-site check decision"

    def test_check_record_for_an_old_url_blocks(self):
        # Decision taken on another site; the URL moved afterwards.
        moved = inst(2, SCHOOL_NAME, "lower_secondary", "https://new-school.maplebear.bg/",
                     attributes=check("keep", "https://sofia-school.maplebear.bg/"))
        assert link_rejection(KG, moved) == "school website: shared-site check decision is for another site"
        replaced_elsewhere = inst(1, KG_NAME, "kindergarten", "https://sofia-school.maplebear.bg/",
                                  attributes=check("replace", "https://sofia-kindergarten.maplebear.bg/",
                                                   "https://sofia-school.maplebear.bg/"))
        assert link_rejection(replaced_elsewhere, SCHOOL) == "kindergarten website: shared-site check decision is for another site"

    def test_keep_record_for_the_current_url_links(self):
        kept_kg = inst(1, KG_NAME, "kindergarten", "https://sofia-kindergarten.maplebear.bg/about")
        assert link_rejection(kept_kg, SCHOOL) is None

    def test_different_domain_city_or_brand_blocks(self):
        assert link_rejection(KG, inst(2, SCHOOL_NAME, "primary", "https://maplebear.com/")) == "different site"
        assert link_rejection(KG, inst(2, SCHOOL_NAME, "primary", "https://a.maplebear.bg/", city="varna")) == "different city"
        assert link_rejection(KG, inst(2, '"Частно училище Дружба"', "primary", "https://a.maplebear.bg/")) == "no shared brand"

    def test_short_shared_brand_blocks(self):
        kg = inst(1, "Детска градина 12", "kindergarten", "https://a.example.bg/")
        school = inst(2, "Основно училище 12", "primary", "https://b.example.bg/")
        assert link_rejection(kg, school) == "shared brand too short"

    def test_several_candidate_schools_is_ambiguous(self):
        second = inst(4, '"Частно средно училище Канадско мече"', "upper_secondary", "https://hs.maplebear.bg/")
        assert continues_to_target(KG, [SCHOOL, second]) is None


async def _add(db, *, name, level, url, status="extracted", attributes=None, city="sofia", pinned=True):
    school = School(
        name_i18n={"bg": name},
        country_code="bg",
        city=city,
        school_type="private",
        education_level=level,
        website_url=url,
        scrape_status=status,
        attributes=attributes or {},
    )
    db.add(school)
    await db.flush()
    db.add(
        SchoolLocation(
            school_id=school.id,
            address_i18n={"bg": "София"},
            lat=42.65 if pinned else None,
            lng=23.33 if pinned else None,
            is_primary=True,
        )
    )
    await db.commit()
    return school


async def _maple_bear(db, *, kg_status="pending", school_status="extracted", school_pinned=True, kg_checked=True):
    kg = await _add(
        db,
        name=KG_NAME,
        level="kindergarten",
        url=None if kg_status == "failed_validate" else "https://sofia-kindergarten.maplebear.bg/",
        status=kg_status,
        # 556's current shape: re-extraction pending, website data withheld.
        attributes={
            WEBSITE_DATA_WITHHELD_KEY: True,
            "extracted": {"secret": "must not ship"},
            **(check("replace", "https://sofia-kindergarten.maplebear.bg/", "https://sofia-school.maplebear.bg/en/")
               if kg_checked else {}),
        },
    )
    school = await _add(
        db,
        name=SCHOOL_NAME,
        level="lower_secondary",
        url=None if school_status == "failed_validate" else "https://sofia-school.maplebear.bg/",
        status=school_status,
        attributes=dict(
            VALIDATED,
            extracted={"secret": "must not ship"},
            **check("replace", "https://sofia-school.maplebear.bg/", "https://maplebear.bg/"),
        ),
        pinned=school_pinned,
    )
    return kg, school


class TestDetailEndpoint:
    @pytest.mark.asyncio
    async def test_maple_bear_shape_publishes_one_safe_link(self, seeded_db, seeded_client):
        kg, school = await _maple_bear(seeded_db)

        response = await seeded_client.get(f"/schools/{kg.id}")

        assert response.status_code == 200
        # The target's name exactly as its own detail response publishes it.
        published_name = (await seeded_client.get(f"/schools/{school.id}")).json()["resolved_name_i18n"]
        assert response.json()["continues_to"] == {
            "id": school.id,
            "resolved_name_i18n": published_name,
            "school_type": "private",
            "education_level": "lower_secondary",
        }
        assert "must not ship" not in response.text

    @pytest.mark.asyncio
    async def test_school_side_links_back(self, seeded_db, seeded_client):
        kg, school = await _maple_bear(seeded_db)
        response = await seeded_client.get(f"/schools/{school.id}")
        body = response.json()
        assert body["continues_to"] is None
        assert [row["id"] for row in body["continued_from"]] == [kg.id]
        assert set(body["continued_from"][0]) == {"id", "resolved_name_i18n", "school_type", "education_level"}
        assert "must not ship" not in response.text

    @pytest.mark.asyncio
    async def test_school_side_has_no_link_back_when_the_link_fails(self, seeded_db, seeded_client):
        _, school = await _maple_bear(seeded_db, kg_checked=False)
        response = await seeded_client.get(f"/schools/{school.id}")
        assert response.json()["continued_from"] == []

    @pytest.mark.asyncio
    async def test_withheld_school_website_removes_link(self, seeded_db, seeded_client):
        kg, _ = await _maple_bear(seeded_db, school_status="failed_validate")
        response = await seeded_client.get(f"/schools/{kg.id}")
        assert response.json()["continues_to"] is None

    @pytest.mark.asyncio
    async def test_withheld_kindergarten_website_removes_link(self, seeded_db, seeded_client):
        kg, _ = await _maple_bear(seeded_db, kg_status="failed_validate")
        response = await seeded_client.get(f"/schools/{kg.id}")
        assert response.json()["continues_to"] is None

    @pytest.mark.asyncio
    async def test_unchecked_pending_kindergarten_has_no_link(self, seeded_db, seeded_client):
        kg, _ = await _maple_bear(seeded_db, kg_checked=False)
        response = await seeded_client.get(f"/schools/{kg.id}")
        assert response.json()["continues_to"] is None

    @pytest.mark.asyncio
    async def test_unlisted_target_removes_link(self, seeded_db, seeded_client):
        kg, _ = await _maple_bear(seeded_db, school_pinned=False)
        response = await seeded_client.get(f"/schools/{kg.id}")
        assert response.json()["continues_to"] is None

    @pytest.mark.asyncio
    async def test_ambiguous_candidates_remove_link(self, seeded_db, seeded_client):
        kg, _ = await _maple_bear(seeded_db)
        await _add(
            seeded_db,
            name='"Частно средно училище Канадско мече"',
            level="upper_secondary",
            url="https://sofia-highschool.maplebear.bg/",
            attributes=dict(VALIDATED, **check("keep", "https://sofia-highschool.maplebear.bg/")),
        )
        response = await seeded_client.get(f"/schools/{kg.id}")
        assert response.json()["continues_to"] is None

    @pytest.mark.asyncio
    async def test_list_endpoint_does_not_carry_the_link(self, seeded_db, seeded_client):
        await _maple_bear(seeded_db)
        response = await seeded_client.get("/schools")
        assert all("continues_to" not in row for row in response.json())


def pin(school_id, name, level, lat, *, location_id=None, school_type="private", city="sofia", lng=23.33):
    return PlacePin(
        school_id=school_id,
        location_id=location_id or school_id * 10,
        lat=lat,
        lng=lng,
        name=name,
        education_level=level,
        school_type=school_type,
        city=city,
    )


SVETLINA_KG = '"Частна детска градина Светлина" ЕООД'
SVETLINA_SCHOOL = '"ЧАСТНО ОСНОВНО УЧИЛИЩЕ СВЕТЛИНА" ЕООД'
SVETLINA_HIGH = '"Частна профилирана гимназия Светлина" ЕООД'


class TestSamePlaceRule:
    def ids(self, groups):
        return [sorted({p.school_id for p in group}) for group in groups]

    def test_kindergarten_school_and_gymnasium_next_door_are_one_place(self):
        groups = same_place_groups([
            pin(1, SVETLINA_KG, "kindergarten", 42.6500),
            pin(2, SVETLINA_SCHOOL, "lower_secondary", 42.6500),
            pin(3, SVETLINA_HIGH, "upper_secondary", 42.6504),  # ~45 m away
        ])
        assert self.ids(groups) == [[1, 2, 3]]

    def test_other_pins_of_a_kindergarten_stay_apart(self):
        groups = same_place_groups([
            pin(1, SVETLINA_KG, "kindergarten", 42.6500, location_id=11),
            pin(1, SVETLINA_KG, "kindergarten", 42.6750, location_id=12),  # Лозенец branch
            pin(2, SVETLINA_SCHOOL, "lower_secondary", 42.6500),
        ])
        assert [[p.location_id for p in group] for group in groups] == [[11, 20]]

    def test_two_schools_alone_are_not_a_place(self):
        # Primary and gymnasium of one brand group only through their kindergarten.
        assert same_place_groups([
            pin(2, SVETLINA_SCHOOL, "lower_secondary", 42.65),
            pin(3, SVETLINA_HIGH, "upper_secondary", 42.65),
        ]) == []

    def test_within_150_metres_only(self):
        kg = pin(1, '"ЧАСТНА ДЕТСКА ГРАДИНА "Д-Р ПЕТЪР БЕРОН" ЕООД', "kindergarten", 42.6500)
        near = pin(2, '"ЧАСТНО ОСНОВНО УЧИЛИЩЕ "Д-Р ПЕТЪР БЕРОН" ЕООД', "lower_secondary", 42.6500 + 0.0006)
        far = pin(2, '"ЧАСТНО ОСНОВНО УЧИЛИЩЕ "Д-Р ПЕТЪР БЕРОН" ЕООД', "lower_secondary", 42.6500 + 0.0019)
        assert self.ids(same_place_groups([kg, near])) == [[1, 2]]
        assert same_place_groups([kg, far]) == []  # ~210 m, Куест's distance

    def test_a_shared_building_without_a_shared_brand_is_not_a_place(self):
        assert same_place_groups([
            pin(1, '"Частна детска градина Монтесори" ЕООД', "kindergarten", 42.65),
            pin(2, '"Частно основно училище Стремеж" ЕООД', "lower_secondary", 42.65),
        ]) == []

    @pytest.mark.parametrize("types", [("state", "private"), ("private", "state"), ("state", "state")])
    def test_state_institutions_are_left_out(self, types):
        assert same_place_groups([
            pin(1, "ДГ Христо Ботев", "kindergarten", 42.65, school_type=types[0]),
            pin(2, "СУ Христо Ботев", "upper_secondary", 42.65, school_type=types[1]),
        ]) == []

    def test_different_city_is_not_a_place(self):
        assert same_place_groups([
            pin(1, SVETLINA_KG, "kindergarten", 42.65),
            pin(2, SVETLINA_SCHOOL, "lower_secondary", 42.65, city="plovdiv"),
        ]) == []


class TestSamePlaceEndpoints:
    async def _pair(self, db, *, school_lat=42.6505):
        kg = await _add(db, name=SVETLINA_KG, level="kindergarten", url=None,
                        attributes={"extracted": {"secret": "must not ship"}})
        school = await _add(db, name=SVETLINA_SCHOOL, level="lower_secondary", url=None)
        location = (await db.execute(
            SchoolLocation.__table__.select().where(SchoolLocation.school_id == school.id)
        )).first()
        await db.execute(
            SchoolLocation.__table__.update().where(SchoolLocation.id == location.id).values(lat=school_lat)
        )
        await db.commit()
        return kg, school

    @pytest.mark.asyncio
    async def test_list_names_the_other_institution_both_ways(self, seeded_db, seeded_client):
        kg, school = await self._pair(seeded_db)
        response = await seeded_client.get("/schools")
        rows = {row["id"]: row for row in response.json()}
        assert [entry["id"] for entry in rows[kg.id]["same_place"]] == [school.id]
        assert [entry["id"] for entry in rows[school.id]["same_place"]] == [kg.id]
        entry = rows[kg.id]["same_place"][0]
        assert set(entry) == {
            "id", "resolved_name_i18n", "school_type", "education_level", "place_id", "location_id",
            "other_location_id",
        }
        assert entry["place_id"] == rows[school.id]["same_place"][0]["place_id"]
        assert entry["location_id"] == rows[kg.id]["locations"][0]["id"]
        assert entry["other_location_id"] == rows[school.id]["locations"][0]["id"]
        assert "must not ship" not in response.text

    @pytest.mark.asyncio
    async def test_age_filter_keeps_the_hidden_neighbour_named(self, seeded_db, seeded_client):
        kg, school = await self._pair(seeded_db)
        response = await seeded_client.get("/schools", params={"education_level": "kindergarten"})
        rows = {row["id"]: row for row in response.json()}
        assert school.id not in rows
        assert [entry["id"] for entry in rows[kg.id]["same_place"]] == [school.id]

    @pytest.mark.asyncio
    async def test_detail_carries_same_place(self, seeded_db, seeded_client):
        kg, school = await self._pair(seeded_db)
        response = await seeded_client.get(f"/schools/{school.id}")
        assert [entry["id"] for entry in response.json()["same_place"]] == [kg.id]

    @pytest.mark.asyncio
    async def test_too_far_apart_is_two_places(self, seeded_db, seeded_client):
        kg, _ = await self._pair(seeded_db, school_lat=42.6520)
        response = await seeded_client.get(f"/schools/{kg.id}")
        assert response.json()["same_place"] == []
