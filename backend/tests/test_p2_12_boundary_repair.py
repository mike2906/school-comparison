import pytest

from app.models import School, SchoolLocation
from scripts import audit_p2_12_boundary, repair_p2_12_boundary


def test_boundary_audit_uses_shared_sanitizer_for_bare_domains():
    hits = audit_p2_12_boundary.display_markdown_hits(
        {"name_i18n": {"bg": "school.bg/path", "en": "www.school.com"}},
        endpoint="test",
    )

    assert {hit["value"] for hit in hits} == {"school.bg/path", "www.school.com"}


def test_boundary_audit_does_not_treat_plain_hyphens_as_markdown():
    hits = audit_p2_12_boundary.display_markdown_hits(
        {"address_i18n": {"bg": "ж.к. Обеля - 1, бл. 102"}},
        endpoint="test",
    )

    assert hits == []


def test_exhaustive_boundary_scan_allows_url_fields_but_rejects_display_urls():
    hits = audit_p2_12_boundary.all_display_text_hits(
        {
            "website_url": "https://school.bg",
            "source_url": "https://source.bg",
            "attributes_i18n": {"bg": {"facilities": ["See school.bg/library"]}},
        },
        endpoint="test",
    )

    assert hits == [
        {
            "endpoint": "test",
            "path": "attributes_i18n.bg.facilities.0",
            "value": "See school.bg/library",
        }
    ]


def test_exhaustive_boundary_scan_finds_internal_and_raw_provenance_keys():
    hits = audit_p2_12_boundary.internal_key_hits(
        {
            "attributes": {"moe_code": "x", "data_validation": {}},
            "field_sources": [{"source_type": "official", "value_text": "rejected"}],
        },
        endpoint="test",
    )

    assert {hit["path"] for hit in hits} == {
        "attributes.moe_code",
        "attributes.data_validation",
        "field_sources.0.value_text",
    }


def test_boundary_audit_has_tracked_default_cohort_and_accepts_override(tmp_path):
    assert audit_p2_12_boundary.read_cohort() == list(
        audit_p2_12_boundary.DEFAULT_COHORT_IDS
    )

    cohort_file = tmp_path / "cohort.txt"
    cohort_file.write_text("# custom\n161\n367  # regression\n", encoding="utf-8")

    assert audit_p2_12_boundary.read_cohort(cohort_file) == [161, 367]


def _school(
    school_id: int,
    name: str,
    *,
    attributes: dict | None = None,
    scrape_status: str = "pending",
) -> School:
    return School(
        id=school_id,
        name_i18n={"bg": name},
        country_code="bg",
        city="sofia",
        school_type="private",
        education_level="primary",
        attributes=attributes or {},
        scrape_status=scrape_status,
    )


@pytest.mark.asyncio
async def test_guarded_repair_corrects_only_location_119_and_accepts_terminal_nulls(
    db_session,
):
    schools = [
        _school(116, "ДГ №13 Калинка"),
        _school(595, "СофтУни БУДИТЕЛ"),
        _school(593, "Наука за деца"),
        _school(631, "Щастливата къща"),
        _school(
            161,
            "5 ОУ Иван Вазов",
            attributes={
                "website_candidate_url": repair_p2_12_boundary.SCHOOL_161_URL,
                "website_candidate_reason": "HTTP error: 503",
                "website_data_withheld": True,
            },
            scrape_status="failed_validate",
        ),
    ]
    db_session.add_all(schools)
    await db_session.flush()

    terminal_failure = {
        "status": "failed",
        "provider": "nominatim",
        "rejection_reason": "No results found",
    }
    db_session.add_all(
        [
            SchoolLocation(
                id=119,
                school_id=116,
                address_i18n={"bg": 'гр. София, ж.к. "Дружба" I, ул. 5036'},
                lat=42.6548862,
                lng=23.4008491,
            ),
            SchoolLocation(
                id=1147,
                school_id=595,
                address_i18n={"bg": 'ж. к. Дружба, ул. "5006" № 2'},
                lat=42.6548862,
                lng=23.4008491,
            ),
            SchoolLocation(
                id=1143,
                school_id=593,
                address_i18n={"bg": 'бул. "Самоков", бл. 47'},
                geocode_meta=terminal_failure,
            ),
            SchoolLocation(
                id=1145,
                school_id=593,
                address_i18n={"bg": 'бул. "Цариградско шосе" № 11'},
                geocode_meta=terminal_failure,
            ),
            SchoolLocation(
                id=1188,
                school_id=631,
                address_i18n={"bg": "ж. к. Младост 4, бл. 460А"},
                geocode_meta={
                    "status": "rejected",
                    "provider": "geojson_bg",
                    "rejection_reason": "duplicate_geojson_name_match_different_address",
                },
            ),
        ]
    )
    await db_session.commit()

    dry_run = await repair_p2_12_boundary.run_repair(db_session, apply=False)
    assert dry_run["collision"]["action"] == "would_correct"
    assert dry_run["terminal_locations"] == [1143, 1145, 1188]

    applied = await repair_p2_12_boundary.run_repair(db_session, apply=True)
    assert applied["collision"]["action"] == "corrected"
    assert applied["school_161_withheld"] is True

    location_119 = await db_session.get(SchoolLocation, 119)
    location_1147 = await db_session.get(SchoolLocation, 1147)
    assert (location_119.lat, location_119.lng) == pytest.approx((42.66442, 23.39475))
    assert location_119.geocode_meta["source_feature_id"] == "BG_30912207846"
    assert (location_1147.lat, location_1147.lng) == pytest.approx(
        (42.6548862, 23.4008491)
    )

    repeated = await repair_p2_12_boundary.run_repair(db_session, apply=True)
    assert repeated["collision"]["action"] == "already_correct"


@pytest.mark.asyncio
async def test_repair_fails_closed_when_school_161_is_not_withheld(db_session):
    db_session.add(
        _school(
            161,
            "5 ОУ Иван Вазов",
            attributes={"website_candidate_url": repair_p2_12_boundary.SCHOOL_161_URL},
            scrape_status="failed_validate",
        )
    )
    await db_session.commit()

    with pytest.raises(RuntimeError, match="school 161 is not withheld"):
        await repair_p2_12_boundary.audit_school_161(db_session)
