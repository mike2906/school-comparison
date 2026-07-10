"""Tests for P1.7 field-level display gating (`app.utils.display_gating`).

Beyond the endpoint-level tests in `test_api.py`, these pin down the parts most
likely to rot silently: the coupling between the gate's field-path mapping and the
validator's own path vocabulary, and that the gate fires on a *real* validator run
rather than only on hand-written report payloads.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models import Pricing, School, SchoolLocation
from app.models.pricing import PriceSource
from app.schemas.school import SchoolResponse
from app.scrapers import validator as validator_module
from app.scrapers.validator import SPOT_CHECK_EXTRACTED_ROOT_KEYS
from app.utils.display_gating import (
    _FIELD_PATH_DISPLAY_FIELDS,
    iter_blocking_field_paths,
    summary_is_publishable,
)
from app.utils.school_attributes import build_filterable_attributes


def test_display_field_mapping_stays_within_validator_vocabulary():
    """Guard: every gated `attributes.extracted.<key>` is a key the validator can emit.

    A spot-check discrepancy is re-rooted to `attributes.extracted.<key>` using
    `validator.SPOT_CHECK_EXTRACTED_ROOT_KEYS`. If the gate maps a key outside that
    set, no report path will ever match it and the field silently stops being gated.
    """
    prefix = "attributes.extracted."
    for path in _FIELD_PATH_DISPLAY_FIELDS:
        assert path.startswith(prefix), f"{path} is not an extracted path"
        key = path[len(prefix):]
        assert key in SPOT_CHECK_EXTRACTED_ROOT_KEYS, (
            f"{key!r} is mapped for display gating but is not in the validator's "
            "spot-check vocabulary — the gate can never match it."
        )


def test_iter_blocking_field_paths_selects_errors_and_actionable_discrepancies():
    """Warnings and omission-only discrepancies must not withhold anything."""
    report = {
        "issues": [
            {"severity": "error", "field_path": "pricing[1].amount"},
            {"severity": "warning", "field_path": "pricing[2].source_url"},
        ],
        "spot_check": {
            "discrepancies": [
                {"kind": "contradiction", "field_path": "attributes.extracted.facilities"},
                {"kind": "omission", "field_path": "attributes.extracted.programs"},
            ]
        },
    }
    assert set(iter_blocking_field_paths(report)) == {
        "pricing[1].amount",
        "attributes.extracted.facilities",
    }


def test_filter_projection_drops_validation_flagged_field():
    """The filter projection respects the gate, so payload and filters agree.

    A validation-flagged facility disappears from what filters match against — the
    school stops matching a `facilities` filter on that value entirely, by design.
    """
    attrs = {
        "extracted": {"facilities": ["Library"], "programs": ["STEM"]},
        "data_validation": {
            "status": "ok",
            "issues": [],
            "spot_check": {
                "discrepancies": [
                    {"field_path": "attributes.extracted.facilities", "kind": "contradiction"}
                ]
            },
        },
    }
    filterable = build_filterable_attributes(attrs)
    assert filterable["facilities"] == []
    # An unaffected field is untouched.
    assert filterable["special_programs"] == ["STEM"]


def test_summary_publishable_is_lenient_without_a_report():
    # No report / no status → keep the (already-vetted) summary.
    assert summary_is_publishable({}) is True
    assert summary_is_publishable({"data_validation": {}}) is True
    assert summary_is_publishable(None) is True
    # Explicit statuses gate as documented.
    assert summary_is_publishable({"data_validation": {"status": "ok"}}) is True
    assert summary_is_publishable({"data_validation": {"status": "needs_review"}}) is False


@pytest.mark.asyncio
async def test_real_validator_error_gates_pricing_row_and_summary(db_session):
    """End-to-end: a real `validate_school_data` run drives the serialization gate.

    This exercises the deterministically-reachable error path (a negative price →
    `negative_price_amount` at `pricing[{id}].amount` → status `needs_review`) rather
    than a synthetic report, proving the validator's real output and the gate agree.
    """
    school = School(
        name_i18n={"bg": "Гейт Тест", "en": "Gate Test"},
        country_code="bg",
        school_type="private",
        education_level="primary",
        city="sofia",
        scrape_status="extracted",
        summary_i18n={"bg": {"short": "кратко", "long": "дълго"}},
    )
    db_session.add(school)
    await db_session.flush()
    db_session.add(
        SchoolLocation(
            school_id=school.id,
            address_i18n={"bg": "ул. Тест 1", "en": "1 Test St"},
            lat=42.7,
            lng=23.3,
            is_primary=True,
        )
    )
    good = Pricing(
        school_id=school.id,
        category="tuition",
        amount=500,
        currency="BGN",
        period="monthly",
        source=PriceSource.SCRAPED_WEBSITE,
        source_url="https://example.com/fees",
    )
    bad = Pricing(
        school_id=school.id,
        category="food",
        amount=-5,
        currency="BGN",
        period="monthly",
        source=PriceSource.SCRAPED_WEBSITE,
        source_url="https://example.com/fees",
    )
    db_session.add_all([good, bad])
    await db_session.commit()

    result = await validator_module.validate_school_data(db_session, school.id, "bg")
    assert result["status"] == "needs_review"

    loaded = (
        await db_session.execute(
            select(School)
            .options(
                selectinload(School.locations).selectinload(SchoolLocation.age_group_shifts),
                selectinload(School.pricing),
                selectinload(School.exam_results),
                selectinload(School.field_sources),
            )
            .where(School.id == school.id)
        )
    ).scalar_one()

    payload = SchoolResponse.model_validate(loaded).model_dump()

    # The validator-flagged (negative) row is withheld; the clean row survives.
    assert [row["category"] for row in payload["pricing"]] == ["tuition"]
    # needs_review withholds the stored whole-school summary too.
    assert payload["summary_i18n"] is None
