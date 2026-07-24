"""Tests for bounded promotion of manually curated canonical identities."""

from __future__ import annotations

from sqlalchemy import select

from app.models import FieldSource, School, SchoolLocation
from app.schemas.school import SchoolListResponse
from app.services.data_quality import _curated_identity_promotions
from app.services.identity_curation import (
    merge_authoritative_name_i18n,
    promote_curated_identities,
)


def _curated_attributes(
    *,
    english_name: str = "The Beehive",
    source_urls: list[str] | None = None,
) -> dict:
    return {
        "display_name_i18n": {"bg": "Пчелното кошерче", "en": english_name},
        "display_name_evidence": {
            "status": "corroborated",
            "signals": ["website_domain_alias_match", "repeated_on_page_identity"],
            "curation": {
                "method": "offline_cached_source_review",
                "reviewed_at": "2026-07-23T09:14:02Z",
                "reviewed_by": "codex",
                "source_urls": source_urls
                or [
                    "https://www.thebeehive.bg/enroll/book-a-visit",
                    "https://www.thebeehive.bg/programs",
                ],
            },
        },
        "extracted": {"facilities": ["Unvalidated pool"]},
    }


async def _school(db_session, **overrides) -> School:
    values = {
        "country_code": "bg",
        "city": "sofia",
        "school_type": "private",
        "education_level": "kindergarten",
        "name_i18n": {"bg": 'Частна детска градина "Пчелното кошерче" ООД'},
        "website_url": "https://www.thebeehive.bg/",
        "scrape_status": "extracted",
        "attributes": _curated_attributes(),
    }
    values.update(overrides)
    school = School(**values)
    db_session.add(school)
    await db_session.commit()
    return school


async def test_promotion_is_dry_run_by_default_and_commit_is_idempotent(db_session):
    school = await _school(db_session)

    preview = await promote_curated_identities(
        db_session,
        school_ids=[school.id],
        country="bg",
        city="sofia",
        promoted_by="codex",
        commit=False,
    )
    await db_session.refresh(school)
    assert preview == {
        "requested": 1,
        "eligible": 1,
        "promoted": 0,
        "already_promoted": 0,
        "conflicts": 0,
        "ineligible": 0,
        "rows": [
            {
                "school_id": school.id,
                "english_name": "The Beehive",
                "decision": "would_promote",
                "reason": None,
            }
        ],
    }
    assert "en" not in school.name_i18n

    committed = await promote_curated_identities(
        db_session,
        school_ids=[school.id],
        country="bg",
        city="sofia",
        promoted_by="codex",
        commit=True,
    )
    await db_session.refresh(school)
    assert committed["promoted"] == 1
    assert school.name_i18n["en"] == "The Beehive"
    assert school.attributes["canonical_identity_curation"]["en"]["value"] == "The Beehive"
    assert school.attributes["canonical_identity_curation"]["en"]["status"] == "promoted"
    canonical_curation = school.attributes["canonical_identity_curation"]

    sources = list(
        (
            await db_session.execute(
                select(FieldSource)
                .where(
                    FieldSource.school_id == school.id,
                    FieldSource.field_key == "name_i18n.en",
                )
                .order_by(FieldSource.source_url)
            )
        )
        .scalars()
        .all()
    )
    assert [source.source_url for source in sources] == [
        "https://www.thebeehive.bg/enroll/book-a-visit",
        "https://www.thebeehive.bg/programs",
    ]
    assert all(source.verified is True for source in sources)
    assert all(source.submitted_by == "codex" for source in sources)

    repeated = await promote_curated_identities(
        db_session,
        school_ids=[school.id],
        country="bg",
        city="sofia",
        promoted_by="codex",
        commit=True,
    )
    await db_session.refresh(school)
    assert repeated["already_promoted"] == 1
    assert school.attributes["canonical_identity_curation"] == canonical_curation
    assert len(
        list(
            (
                await db_session.execute(
                    select(FieldSource).where(
                        FieldSource.school_id == school.id,
                        FieldSource.field_key == "name_i18n.en",
                    )
                )
            )
            .scalars()
            .all()
        )
    ) == 2


async def test_registry_merge_preserves_only_explicitly_promoted_english(db_session):
    curated = await _school(db_session)
    await promote_curated_identities(
        db_session,
        school_ids=[curated.id],
        country="bg",
        city="sofia",
        promoted_by="codex",
        commit=True,
    )
    uncurated = await _school(
        db_session,
        name_i18n={"bg": "Старо име", "en": "Legacy English"},
        attributes={},
    )

    assert merge_authoritative_name_i18n(curated, {"bg": "Ново име"}) == {
        "bg": "Ново име",
        "en": "The Beehive",
    }
    assert merge_authoritative_name_i18n(uncurated, {"bg": "Ново име"}) == {
        "bg": "Ново име"
    }
    assert merge_authoritative_name_i18n(curated, {"bg": "Ново име", "en": "Incoming EN"}) == {
        "bg": "Ново име",
        "en": "Incoming EN",
    }


async def test_promotion_keeps_unvalidated_website_payload_withheld(db_session):
    school = await _school(db_session)
    await promote_curated_identities(
        db_session,
        school_ids=[school.id],
        country="bg",
        city="sofia",
        promoted_by="codex",
        commit=True,
    )
    await db_session.refresh(school)

    public = SchoolListResponse.model_validate(
        {
            "id": school.id,
            "country_code": school.country_code,
            "name_i18n": school.name_i18n,
            "school_type": school.school_type,
            "education_level": school.education_level,
            "scrape_status": school.scrape_status,
            "attributes": school.attributes,
            "pricing": [],
        }
    )

    assert public.resolved_name_i18n["en"] == "The Beehive"
    assert public.attributes.filter_tags.facilities == []
    assert "Unvalidated pool" not in str(public.attributes.model_dump())
    assert public.website_data_publishable is False


async def test_promoted_identity_is_consistent_across_list_detail_and_search(
    seeded_db,
    seeded_client,
):
    school = await _school(seeded_db)
    seeded_db.add(
        SchoolLocation(
            school_id=school.id,
            address_i18n={"bg": "ул. Ралевица 80А", "en": "80A Ralevitsa Street"},
            lat=42.657,
            lng=23.286,
            is_primary=True,
        )
    )
    await seeded_db.commit()
    await promote_curated_identities(
        seeded_db,
        school_ids=[school.id],
        country="bg",
        city="sofia",
        promoted_by="codex",
        commit=True,
    )

    list_response = await seeded_client.get("/schools")
    detail_response = await seeded_client.get(f"/schools/{school.id}")
    search_response = await seeded_client.get("/schools/search?q=The%20Beehive")

    assert list_response.status_code == 200
    listed = next(row for row in list_response.json() if row["id"] == school.id)
    assert listed["resolved_name_i18n"]["en"] == "The Beehive"
    assert listed["attributes"]["filter_tags"]["facilities"] == []

    assert detail_response.status_code == 200
    assert detail_response.json()["resolved_name_i18n"]["en"] == "The Beehive"
    assert "Unvalidated pool" not in str(detail_response.json())
    assert "canonical_identity_curation" not in str(detail_response.json())

    assert search_response.status_code == 200
    assert [row["id"] for row in search_response.json()] == [school.id]


async def test_promotion_rejects_conflicts_and_non_official_source_domains(db_session):
    conflict = await _school(
        db_session,
        name_i18n={"bg": "Пчелното кошерче", "en": "Different Canonical Name"},
    )
    wrong_domain = await _school(
        db_session,
        website_url="https://www.thebeehive.bg/",
        attributes=_curated_attributes(
            source_urls=[
                "https://directory.example/the-beehive",
                "https://www.thebeehive.bg/programs",
            ]
        ),
    )
    duplicate_page = await _school(
        db_session,
        attributes=_curated_attributes(
            source_urls=[
                "https://www.thebeehive.bg/programs#top",
                "https://www.thebeehive.bg/programs#enrollment",
            ]
        ),
    )

    result = await promote_curated_identities(
        db_session,
        school_ids=[conflict.id, wrong_domain.id, duplicate_page.id],
        country="bg",
        city="sofia",
        promoted_by="codex",
        commit=True,
    )

    assert result["conflicts"] == 1
    assert result["ineligible"] == 2
    assert {row["reason"] for row in result["rows"]} == {
        "canonical_english_name_conflict",
        "source_domain_mismatch",
        "insufficient_source_urls",
    }


async def test_quality_metric_flags_only_unpromoted_curated_identity(db_session):
    pending = await _school(db_session)
    promoted = await _school(
        db_session,
        name_i18n={"bg": "Пчелното кошерче", "en": "The Beehive"},
    )
    automatic = await _school(db_session)
    automatic.attributes = {
        **_curated_attributes(),
        "display_name_evidence": {
            "status": "corroborated",
            "signals": ["website_domain_alias_match", "repeated_on_page_identity"],
        },
    }

    metric = _curated_identity_promotions([pending, promoted, automatic])

    assert metric == {
        "eligible": 2,
        "published": 1,
        "blocked": 1,
        "conflicts": 0,
        "blocked_school_ids": [pending.id],
    }
