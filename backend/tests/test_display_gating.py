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
from app.schemas.validation import SpotCheckDiscrepancy, SpotCheckOutput
from app.scrapers import validator as validator_module
from app.scrapers.validator import _spot_check_path_is_core
from app.utils.display_gating import (
    _FIELD_PATH_DISPLAY_FIELDS,
    blocked_display_fields,
    iter_blocking_field_paths,
    passes_pricing_gate,
    summary_is_publishable,
)
from app.utils.school_attributes import build_filterable_attributes


def test_display_field_mapping_is_reachable_via_spot_check_scope():
    """Guard: every gated `attributes.extracted.<key>` is actually reachable.

    `_normalize_spot_check_output` drops any discrepancy outside the spot-check core
    scope, so a mapped key that is not core can never match — the gate would silently
    no-op while a guard on a broader vocabulary still passed (the bug Codex caught).
    Asserting against the validator's own `_spot_check_path_is_core` keeps the gate's
    coverage and the spot-check scope in lockstep.
    """
    for path in _FIELD_PATH_DISPLAY_FIELDS:
        assert path.startswith("attributes.extracted."), f"{path} is not an extracted path"
        assert _spot_check_path_is_core(path), (
            f"{path!r} is mapped for display gating but is outside the spot-check core "
            "scope (SPOT_CHECK_CORE_FIELD_PREFIXES) — a discrepancy on it is dropped "
            "before it reaches the report, so the gate can never withhold it."
        )


def test_free_text_field_discrepancy_survives_spot_check_and_gates_field():
    """A `facilities` discrepancy now survives normalization and drives the gate.

    Before the spot-check scope was widened, `_normalize_spot_check_output` dropped
    this discrepancy (facilities was outside core scope) and the gate could never
    withhold the field. This is the end-to-end proof the widening is effective.
    """
    parsed = SpotCheckOutput(
        has_discrepancy=True,
        discrepancies=[
            SpotCheckDiscrepancy(
                field_path="facilities",
                kind="contradiction",
                issue="listed facility contradicts source",
                evidence="site states there is no swimming pool",
                cheap_value=["Swimming pool"],
                capable_value=[],
                confidence=0.9,
            )
        ],
    )

    normalized = validator_module._normalize_spot_check_output(parsed)

    # Retained (in scope) and re-rooted to its canonical extracted path...
    assert normalized.has_discrepancy is True
    assert [d.field_path for d in normalized.discrepancies] == ["attributes.extracted.facilities"]
    # ...and the display gate withholds the facilities field because of it.
    attributes = {"data_validation": {"issues": [], "spot_check": normalized.model_dump()}}
    assert "facilities" in blocked_display_fields(attributes)


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
        "extracted": {"facilities": ["Library"], "programs": ["Футбол"]},
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
    # An unaffected field is untouched (free text maps to its canonical tag, P1.9).
    assert filterable["special_programs"] == ["sports_program"]


def test_summary_publishable_requires_an_ok_report():
    assert summary_is_publishable({}) is False
    assert summary_is_publishable({"data_validation": {}}) is False
    assert summary_is_publishable(None) is False
    assert summary_is_publishable({"data_validation": {"status": "ok"}}) is True
    assert summary_is_publishable({"data_validation": {"status": "needs_review"}}) is False


def test_summary_publishable_rejects_actionable_spot_check_even_when_status_is_ok():
    attributes = {
        "data_validation": {
            "status": "ok",
            "issues": [],
            "spot_check": {
                "has_discrepancy": True,
                "discrepancies": [
                    {
                        "field_path": "attributes.extracted.class_size",
                        "kind": "unsupported",
                    }
                ],
            },
        }
    }

    assert summary_is_publishable(attributes) is False


@pytest.mark.parametrize(
    ("confidence", "expected"),
    [
        (0.7, True),
        (1.0, True),
        (0.699, False),
        (-0.1, False),
        (1.1, False),
        (None, False),
        (True, False),
        ("0.9", False),
        (float("nan"), False),
    ],
)
def test_pricing_gate_requires_bounded_numeric_confidence(confidence, expected):
    context = {} if confidence is None else {"confidence": confidence}
    assert passes_pricing_gate("https://example.com/fees", context) is expected


def test_pricing_gate_requires_a_source_and_context():
    assert passes_pricing_gate(None, {"confidence": 0.9}) is False
    assert passes_pricing_gate("  ", {"confidence": 0.9}) is False
    assert passes_pricing_gate("https://example.com/fees", None) is False


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
        pricing_context={"confidence": 0.9},
    )
    bad = Pricing(
        school_id=school.id,
        category="food",
        amount=-5,
        currency="BGN",
        period="monthly",
        source=PriceSource.SCRAPED_WEBSITE,
        source_url="https://example.com/fees",
        pricing_context={"confidence": 0.9},
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
