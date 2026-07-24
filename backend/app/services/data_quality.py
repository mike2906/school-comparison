"""Data-quality scoreboard (P1.6).

Computes the six go-live quality metrics over the schools/locations/pricing in
scope. Metrics are computed in Python from loaded rows rather than DB-side JSON
operators so the same code runs on PostgreSQL (prod) and SQLite (tests) — the
data set is city-scale (~500 schools), so this is cheap and keeps the queries
portable.

Used by:
* the pipeline run lifecycle (snapshotted into ``PipelineRun.metrics``), and
* the ``data-quality`` CLI report.
"""

from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pricing import Pricing
from app.models.school import School, SchoolLocation
from app.services.identity_curation import curated_identity_candidate
# Re-exported for callers that import it from here; it now lives in
# `app.utils.display_gating` so the scoreboard metric and the P1.7 display gate
# share one source of truth.
from app.utils.display_gating import (  # noqa: F401
    PRICING_CONFIDENCE_FLOOR,
    passes_pricing_gate,
    pricing_row_is_publishable,
    summary_is_publishable,
)
from app.utils.i18n_resolver import resolve_display_name_i18n, resolve_name_i18n
from app.utils.school_attributes import build_display_attributes
from app.utils.website_data import attributes_for_publication, website_data_is_publishable

# Precision used to group coordinates when detecting shared/duplicate points.
_COORD_ROUNDING = 5


def _pct(numerator: int, denominator: int) -> Optional[float]:
    """Percentage in [0, 100] rounded to 1dp, or ``None`` when there is no base."""
    if denominator <= 0:
        return None
    return round(100.0 * numerator / denominator, 1)


def _rate(numerator: int, denominator: int) -> Optional[float]:
    """Ratio in [0, 1] rounded to 4dp, or ``None`` when there is no base."""
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _has_meaningful_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        return any(_has_meaningful_value(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_has_meaningful_value(item) for item in value)
    return bool(value)


def _report_is_current(report: dict[str, Any]) -> bool:
    version = report.get("_schema_version", report.get("schema_version"))
    try:
        return int(version) == 1
    except (TypeError, ValueError):
        return False


def _validation_ok(schools: list[School]) -> dict[str, Any]:
    total = len(schools)
    with_report = 0
    ok = 0
    for school in schools:
        report = _as_dict(_as_dict(school.attributes).get("data_validation"))
        if not report:
            continue
        with_report += 1
        if report.get("status") == "ok":
            ok += 1
    return {
        "ok": ok,
        "total": total,
        "pct": _pct(ok, total),
        "with_report": with_report,
        "coverage_pct": _pct(with_report, total),
    }


def _display_name_overrides(schools: list[School]) -> dict[str, Any]:
    total = len(schools)
    candidates = 0
    overrides = 0
    for school in schools:
        attrs = _as_dict(school.attributes)
        if not website_data_is_publishable(attrs, school.scrape_status):
            continue
        if not attrs.get("display_name_i18n"):
            continue
        candidates += 1
        if resolve_display_name_i18n(attrs):
            overrides += 1
    return {
        "overrides": overrides,
        "total": total,
        "pct": _pct(overrides, total),
        "candidates": candidates,
        "candidate_coverage_pct": _pct(candidates, total),
        "conversion_pct": _pct(overrides, candidates),
    }


def _curated_identity_promotions(schools: list[School]) -> dict[str, Any]:
    """Track reviewed EN identities that still fall back to transliteration."""
    eligible = 0
    published = 0
    conflicts = 0
    blocked_school_ids: list[int] = []
    for school in schools:
        candidate = curated_identity_candidate(school)
        if not candidate.eligible:
            continue
        eligible += 1
        canonical_en = str(_as_dict(school.name_i18n).get("en") or "").strip()
        if canonical_en and canonical_en != candidate.english_name:
            conflicts += 1
        public_attributes = attributes_for_publication(school.attributes, school.scrape_status)
        resolved_en = resolve_name_i18n(school.name_i18n, public_attributes).get("en")
        if resolved_en == candidate.english_name:
            published += 1
        else:
            blocked_school_ids.append(school.id)
    return {
        "eligible": eligible,
        "published": published,
        "blocked": len(blocked_school_ids),
        "conflicts": conflicts,
        "blocked_school_ids": sorted(blocked_school_ids),
    }


def _website_validation_coverage(
    schools: list[School],
) -> dict[str, Any]:
    """Coverage for schools whose website-derived data is currently publishable."""
    eligible = 0
    with_report = 0
    ok = 0
    for school in schools:
        attrs = _as_dict(school.attributes)
        website_only_attrs = {
            key: attrs[key]
            for key in ("extracted", "extracted_i18n", "data_validation")
            if key in attrs
        }
        base, localized = build_display_attributes(website_only_attrs)
        has_website_data = any(
            (
                _has_meaningful_value(base),
                _has_meaningful_value(localized),
                _has_meaningful_value(resolve_display_name_i18n(attrs)),
                summary_is_publishable(attrs)
                and _has_meaningful_value(school.summary_i18n),
            )
        )
        if not has_website_data or not website_data_is_publishable(attrs, school.scrape_status):
            continue
        eligible += 1
        report = _as_dict(attrs.get("data_validation"))
        if _report_is_current(report):
            with_report += 1
            if report.get("status") == "ok":
                ok += 1
    return {
        "eligible": eligible,
        "with_report": with_report,
        "coverage_pct": _pct(with_report, eligible),
        "ok": ok,
        "ok_pct": _pct(ok, eligible),
        "published_without_report": eligible - with_report,
    }


def _location_metrics(locations: list[SchoolLocation]) -> tuple[dict[str, Any], int]:
    precise = 0
    with_precision = 0
    geocoded = 0
    coord_groups: dict[tuple[float, float], set[int]] = {}
    for loc in locations:
        if loc.lat is not None and loc.lng is not None:
            geocoded += 1
            meta = _as_dict(loc.geocode_meta)
            precision = meta.get("precision")
            if precision in {"exact", "approximate"}:
                with_precision += 1
                if precision == "exact":
                    precise += 1
            key = (round(float(loc.lat), _COORD_ROUNDING), round(float(loc.lng), _COORD_ROUNDING))
            coord_groups.setdefault(key, set()).add(loc.school_id)

    # A "duplicate coordinate group" is one point shared by >=2 distinct schools.
    duplicate_groups = sum(1 for schools in coord_groups.values() if len(schools) >= 2)
    precision_metric = {
        "exact": precise,
        "geocoded": geocoded,
        "with_precision": with_precision,
        "pct": _pct(precise, geocoded),
        "coverage_pct": _pct(with_precision, geocoded),
    }
    return precision_metric, duplicate_groups


def _pricing_gate_failures(rows: list[Pricing]) -> dict[str, Any]:
    total = 0
    failing = 0
    publishable = 0
    for row in rows:
        total += 1
        if pricing_row_is_publishable(row):
            publishable += 1
        else:
            failing += 1
    return {
        "publishable": publishable,
        "failing": failing,
        "total": total,
        "pct": _pct(failing, total),
    }


def _spot_check_rate(schools: list[School]) -> dict[str, Any]:
    """Fraction of spot-checked schools that showed an actionable discrepancy.

    Spot-check results are persisted on the school under
    ``attributes.data_validation.spot_check`` (latest run per school), not in a
    per-run table, so the rate is over the most recent spot-check per school.
    """
    checked = 0
    discrepancies = 0
    for school in schools:
        spot = _as_dict(_as_dict(_as_dict(school.attributes).get("data_validation")).get("spot_check"))
        if not spot:
            continue
        checked += 1
        if spot.get("has_discrepancy"):
            discrepancies += 1
    return {
        "discrepancies": discrepancies,
        "schools_checked": checked,
        "rate": _rate(discrepancies, checked),
    }


async def compute_quality_metrics(
    db: AsyncSession,
    *,
    country: str,
    city: Optional[str] = None,
) -> dict[str, Any]:
    """Compute the six data-quality metrics for schools in ``country`` [/``city``]."""
    school_query = select(School).where(School.country_code == country)
    if city:
        school_query = school_query.where(School.city == city)
    schools = list((await db.execute(school_query)).scalars().all())
    school_ids = [school.id for school in schools]

    if school_ids:
        locations = list(
            (
                await db.execute(
                    select(SchoolLocation).where(SchoolLocation.school_id.in_(school_ids))
                )
            )
            .scalars()
            .all()
        )
        pricing_rows = list(
            (
                await db.execute(
                    select(Pricing).where(Pricing.school_id.in_(school_ids))
                )
            )
            .scalars()
            .all()
        )
    else:
        locations, pricing_rows = [], []

    precision_metric, duplicate_groups = _location_metrics(locations)
    return {
        "schools_in_scope": len(school_ids),
        "validation_ok": _validation_ok(schools),
        "website_validation_coverage": _website_validation_coverage(schools),
        "duplicate_coordinate_groups": duplicate_groups,
        "location_precision_exact": precision_metric,
        "display_name_overrides": _display_name_overrides(schools),
        "curated_identity_promotions": _curated_identity_promotions(schools),
        "spot_check_discrepancy_rate": _spot_check_rate(schools),
        "pricing_rows_failing_gates": _pricing_gate_failures(pricing_rows),
    }
