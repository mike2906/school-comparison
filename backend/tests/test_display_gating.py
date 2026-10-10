"""Tests for P1.7 field-level display gating (`app.utils.display_gating`).

Beyond the endpoint-level tests in `test_api.py`, these pin down the parts most
likely to rot silently: the coupling between the gate's field-path mapping and the
validator's own path vocabulary, and that the gate fires on a *real* validator run
rather than only on hand-written report payloads.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models import Pricing, School, SchoolLocation, ScrapeType, SourcePage
from app.models.pricing import PriceSource
from app.schemas.school import SchoolResponse
from app.schemas.validation import SpotCheckDiscrepancy, SpotCheckOutput
from app.scrapers import validator as validator_module
from app.scrapers.validator import _spot_check_path_is_core
from app.utils.display_gating import (
    _FIELD_PATH_DISPLAY_FIELDS,
    NVO_MIN_PUPILS,
    admission_value_is_semantically_valid,
    blocked_display_fields,
    exam_result_is_publishable,
    implausible_tuition_row_ids,
    iter_blocking_field_paths,
    passes_pricing_gate,
    pricing_row_is_publishable,
    summary_is_publishable,
)
from app.utils.school_attributes import build_filterable_attributes


def _curated_pricing_row(**overrides):
    row = {
        "source": PriceSource.OFFICIAL,
        "source_url": "https://example.com/fees",
        "pricing_context": {
            "confidence": 0.9,
            "human_verification": {
                "verified_by": "test-curator",
                "verified_at": "2026-07-16T09:00:00Z",
            },
        },
        "scraped_at": datetime(2026, 7, 16, 9, 0, tzinfo=timezone.utc),
        # Evidence link is the trust signal; the human_verification block above is
        # tolerated for backward compatibility but no longer authoritative.
        "source_page_id": 11,
        "source_page": {"id": 11, "is_valid": True},
        "amount": 500,
        "amount_min": None,
        "amount_max": None,
        "currency": "BGN",
        "period": "monthly",
    }
    row.update(overrides)
    return row


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


def test_summary_publishable_requires_flag_and_an_ok_report(monkeypatch):
    settings = validator_module.get_settings()
    monkeypatch.setattr(settings, "publish_summaries", False)
    assert summary_is_publishable({"data_validation": {"status": "ok"}}) is False

    monkeypatch.setattr(settings, "publish_summaries", True)
    assert summary_is_publishable({}) is False
    assert summary_is_publishable({"data_validation": {}}) is False
    assert summary_is_publishable(None) is False
    assert summary_is_publishable({"data_validation": {"status": "ok"}}) is True
    assert summary_is_publishable({"data_validation": {"status": "needs_review"}}) is False


def test_summary_publishable_rejects_actionable_spot_check_even_when_status_is_ok(monkeypatch):
    monkeypatch.setattr(validator_module.get_settings(), "publish_summaries", True)
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


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (PriceSource.OFFICIAL, True),
        ("official", True),
        # School-owned website evidence publishes automatically now that trust comes
        # from the evidence link rather than a curator's sign-off.
        (PriceSource.SCRAPED_WEBSITE, True),
        ("scraped_website", True),
        (None, True),
    ],
)
def test_launch_pricing_gate_publishes_evidence_backed_rows_regardless_of_source(source, expected):
    assert pricing_row_is_publishable(_curated_pricing_row(source=source)) is expected


def test_launch_pricing_gate_keeps_existing_fail_closed_checks_for_curated_rows():
    assert pricing_row_is_publishable(_curated_pricing_row(source_url=None)) is False
    assert pricing_row_is_publishable(
        _curated_pricing_row(pricing_context={"confidence": 0.69})
    ) is False


def test_launch_pricing_gate_requires_valid_linked_evidence():
    """The evidence link, not a human sign-off, is what makes a price publishable."""
    assert pricing_row_is_publishable(
        _curated_pricing_row(source_page_id=None, source_page=None)
    ) is False
    # Link recorded but the page row is gone (FK backstop fired).
    assert pricing_row_is_publishable(_curated_pricing_row(source_page=None)) is False
    # Page still present but the crawler invalidated it.
    assert pricing_row_is_publishable(
        _curated_pricing_row(source_page={"id": 11, "is_valid": False})
    ) is False
    assert pricing_row_is_publishable(
        _curated_pricing_row(source_page={"id": 11, "is_valid": None})
    ) is False


def test_launch_pricing_gate_ignores_human_verification():
    """The block is tolerated in stored JSON but must not decide publication."""
    # Absent entirely -> still publishable.
    assert pricing_row_is_publishable(
        _curated_pricing_row(pricing_context={"confidence": 0.9})
    ) is True
    # Present but malformed -> no longer a reason to withhold.
    assert pricing_row_is_publishable(
        _curated_pricing_row(
            pricing_context={
                "confidence": 0.9,
                "human_verification": {"verified_by": "", "verified_at": "nonsense"},
            }
        )
    ) is True


@pytest.mark.parametrize(
    "academic_year,expected",
    [
        ("2026/2027", True),
        ("2025-2026", True),
        (None, True),
        ("", True),
        # A development-strategy span is not an academic year; fail closed.
        ("2022-2027", False),
        ("sometime next year", False),
    ],
)
def test_launch_pricing_gate_requires_resolvable_academic_year(academic_year, expected):
    assert pricing_row_is_publishable(
        _curated_pricing_row(academic_year=academic_year)
    ) is expected


@pytest.mark.parametrize(
    "overrides",
    [
        {"amount": 0},
        {"amount": None, "amount_min": 600, "amount_max": 500},
        {"amount": None, "amount_min": 500, "amount_max": None},
        {"amount": 500, "amount_min": 400, "amount_max": 600},
        {"currency": "bgn"},
        {"currency": "EURO"},
        {"period": "weekly"},
    ],
)
def test_launch_pricing_gate_rejects_malformed_curated_rows(overrides):
    assert pricing_row_is_publishable(_curated_pricing_row(**overrides)) is False


def test_launch_pricing_gate_accepts_well_formed_range():
    assert pricing_row_is_publishable(
        _curated_pricing_row(amount=None, amount_min=500, amount_max=600)
    ) is True


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
    evidence_page = SourcePage(
        school_id=school.id,
        scrape_type=ScrapeType.WEBSITE,
        source_url="https://example.com/fees",
        content_hash="hash-fees",
        raw_markdown="Такси 500 лв.",
        is_valid=True,
    )
    db_session.add(evidence_page)
    await db_session.flush()
    good = Pricing(
        school_id=school.id,
        category="tuition",
        amount=500,
        currency="BGN",
        period="monthly",
        source=PriceSource.OFFICIAL,
        scraped_at=datetime(2026, 7, 16, 9, 0),
        source_url="https://example.com/fees",
        source_page_id=evidence_page.id,
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

    # The validator-flagged (negative) row is withheld; the clean curated row survives.
    assert [row["category"] for row in payload["pricing"]] == ["tuition"]
    # needs_review withholds the stored whole-school summary too.
    assert payload["summary_i18n"] is None


def test_launch_pricing_gate_publishes_an_unstated_period():
    """A school that states a fee but not how often it is charged still publishes it."""
    assert pricing_row_is_publishable(_curated_pricing_row(period=None)) is True


@pytest.mark.parametrize("period", ["weekly", "", "per visit"])
def test_launch_pricing_gate_still_rejects_an_invalid_stated_period(period):
    """Allowing NULL must not loosen validation of a period that *is* present."""
    assert pricing_row_is_publishable(_curated_pricing_row(period=period)) is False


def _tuition_row(row_id, amount, period="monthly", currency="EUR", **overrides):
    return _curated_pricing_row(
        id=row_id, category="tuition", amount=amount, period=period, currency=currency, **overrides
    )


def test_tuition_far_below_the_schools_other_tuition_is_withheld():
    # School 525: a €25/month yoga class stored as tuition next to €280-560/month fees.
    rows = [
        _tuition_row(1, 560),
        _tuition_row(2, 280),
        _tuition_row(3, 350),
        _tuition_row(4, 25, plan_name="Yoga for children"),
    ]
    assert implausible_tuition_row_ids(rows) == {4}


def test_tuition_below_the_yearly_floor_is_withheld_even_without_peers():
    # School 609: a lone €30/year "tuition" row.
    assert implausible_tuition_row_ids([_tuition_row(1, 30, period="yearly")]) == {1}
    assert implausible_tuition_row_ids([_tuition_row(1, 1000, period="yearly")]) == set()


def test_monthly_fee_stored_as_yearly_is_withheld():
    # School 199: €450 "yearly" beside the same plan's €4702 yearly fee.
    rows = [_tuition_row(1, 450, period="yearly"), _tuition_row(2, 4702, period="yearly")]
    assert implausible_tuition_row_ids(rows) == {1}


def test_yearly_fee_stored_as_monthly_is_withheld():
    # School 199's re-run: the yearly fees came back as "6792 EUR monthly", "4702 EUR monthly".
    rows = [
        _tuition_row(i, amount)
        for i, amount in enumerate([613.55, 409.03, 650, 6792, 450, 4702], start=1)
    ]
    assert implausible_tuition_row_ids(rows) == {4, 6}
    # Without cheaper peers the yearly ceiling still catches it.
    assert implausible_tuition_row_ids([_tuition_row(1, 6792), _tuition_row(2, 4702)]) == {1, 2}
    # Below the ceiling, a row at more than four times the school's median tuition.
    rows = [_tuition_row(1, 300), _tuition_row(2, 320), _tuition_row(3, 3600)]
    assert implausible_tuition_row_ids(rows) == {3}
    # Misfiled add-ons below the floor do not make the one real fee look too dear.
    rows = [_tuition_row(1, 25), _tuition_row(2, 30), _tuition_row(3, 20), _tuition_row(4, 560)]
    assert implausible_tuition_row_ids(rows) == {1, 2, 3}
    # The dearest real tuition in Sofia (school 506) is well inside both bounds.
    rows = [_tuition_row(i, a, "yearly") for i, a in enumerate([10000, 12008, 20000, 22300, 23983, 25619], 1)]
    assert implausible_tuition_row_ids(rows) == set()


def test_bgn_tuition_is_converted_before_comparison():
    # 1500 BGN/year is about €767: below the floor. 1500 BGN/month is not.
    assert implausible_tuition_row_ids([_tuition_row(1, 1500, "yearly", "BGN")]) == {1}
    assert implausible_tuition_row_ids([_tuition_row(1, 1500, "monthly", "BGN")]) == set()


def test_monthly_tuition_annualizes_twelve_times_against_both_yearly_bounds():
    # 80/month is 960 a year, under the 1,000 floor; 90/month is 1,080, over it.
    assert implausible_tuition_row_ids([_tuition_row(1, 80)]) == {1}
    assert implausible_tuition_row_ids([_tuition_row(1, 90)]) == set()
    # 4,000/month is 48,000 a year, under the 50,000 ceiling; 4,200/month is 50,400.
    assert implausible_tuition_row_ids([_tuition_row(1, 4000)]) == set()
    assert implausible_tuition_row_ids([_tuition_row(1, 4200)]) == {1}
    # Both bounds are inclusive.
    assert implausible_tuition_row_ids([_tuition_row(1, 1000, "yearly")]) == set()
    assert implausible_tuition_row_ids([_tuition_row(1, 50000, "yearly")]) == set()


def test_peer_floor_uses_the_middle_two_rows_when_a_school_has_an_even_number():
    # Median of 4,000 and 5,000 is 4,500, so the peer floor is 1,125 a year.
    rows = [_tuition_row(i, a, "yearly") for i, a in enumerate([1100, 4000, 5000, 6000], 1)]
    assert implausible_tuition_row_ids(rows) == {1}
    rows = [_tuition_row(i, a, "yearly") for i, a in enumerate([1150, 4000, 5000, 6000], 1)]
    assert implausible_tuition_row_ids(rows) == set()


@pytest.mark.parametrize(
    "today,text,valid",
    [
        # Before July the admission cycle in progress is the one that started last autumn.
        (date(2027, 3, 10), "Прием за 2026/2027 учебна година с тест и интервю.", True),
        (date(2027, 3, 10), "Прием за 2025/2026 учебна година с тест и интервю.", False),
        (date(2027, 6, 30), "Прием за 2026/2027 учебна година с тест и интервю.", True),
        # From 1 July the next cycle has started.
        (date(2027, 7, 1), "Прием за 2026/2027 учебна година с тест и интервю.", False),
        (date(2027, 7, 1), "Прием за 2027/2028 учебна година с тест и интервю.", True),
        # A bare year counts as current when it is the cycle's start year or later.
        (date(2027, 3, 10), "Приемният изпит се проведе през 2026 г.", True),
        (date(2027, 3, 10), "Приемният изпит се проведе през 2025 г.", False),
    ],
)
def test_admission_text_is_current_only_for_the_cycle_in_progress(today, text, valid):
    assert admission_value_is_semantically_valid("entrance_requirements", text, today=today) is valid


def test_quarterly_tuition_annualizes_and_ranges_use_the_lower_bound():
    assert implausible_tuition_row_ids([_tuition_row(1, 200, period="quarter")]) == {1}
    ranged = _tuition_row(1, None, period="yearly", amount_min=500, amount_max=5000)
    assert implausible_tuition_row_ids([ranged]) == {1}


@pytest.mark.parametrize(
    "row",
    [
        # Periods with no defensible yearly figure are left to the other gates.
        _tuition_row(1, 30, period="semester"),
        _tuition_row(1, 30, period="term"),
        _tuition_row(1, 30, period="one_time"),
        # Only tuition is checked; a small registration or food fee is normal.
        _curated_pricing_row(id=1, category="registration", amount=30, period="one_time"),
        _curated_pricing_row(id=1, category="food", amount=30, period="monthly"),
        # No conversion rate for other currencies, so no judgement either.
        _tuition_row(1, 30, currency="USD"),
    ],
)
def test_rows_that_cannot_be_compared_are_left_alone(row):
    assert implausible_tuition_row_ids([row, _tuition_row(2, 900)]) == set()


def test_tuition_with_no_period_is_held_to_the_floors_as_a_yearly_fee():
    """Price audit 2026-10-09: 13 monthly kindergarten fees were saved with no period
    (330's "899.68 лв. / 460 €", 601's "Monthly fee 950 €") and read as the whole cost."""
    assert implausible_tuition_row_ids([_tuition_row(1, "899.68", None, "BGN")]) == {1}
    assert implausible_tuition_row_ids([_tuition_row(1, 950, None)]) == {1}
    # Below a quarter of the school's stated tuition: 587's €572 beside its yearly fees.
    rows = [_tuition_row(1, 7865, "yearly"), _tuition_row(2, 1800, None)]
    assert implausible_tuition_row_ids(rows) == {2}
    # A yearly fee whose period sat only in a table heading (517's 5th grade €7,810)
    # stays, and does not move the median the stated rows set.
    rows = [_tuition_row(1, 7000, "yearly"), _tuition_row(2, 7810, None), _tuition_row(3, 600)]
    assert implausible_tuition_row_ids(rows) == set()
    # Unknown period: not judged against the peer ceiling (it may be a whole course).
    assert implausible_tuition_row_ids([_tuition_row(1, 400), _tuition_row(2, 9000, None)]) == set()
    assert implausible_tuition_row_ids([_tuition_row(1, 60000, None)]) == {1}


def test_plausible_tuition_spread_publishes_in_full():
    # School 625: half-day through annual-prepay monthly rates, all genuine.
    rows = [_tuition_row(i, amount) for i, amount in enumerate([454, 680, 646, 612], start=1)]
    assert implausible_tuition_row_ids(rows) == set()


@pytest.mark.parametrize(
    ("pupil_count", "expected"),
    [
        (1, False),
        (NVO_MIN_PUPILS - 1, False),
        (NVO_MIN_PUPILS, True),
        (120, True),
        # No count stored (a file without the column): the official average still shows.
        (None, True),
    ],
)
def test_exam_result_needs_enough_pupils_to_be_published(pupil_count, expected):
    assert NVO_MIN_PUPILS > 1
    assert exam_result_is_publishable(pupil_count) is expected
