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

from app.models.pricing import PriceSource, Pricing
from app.models.school import School, SchoolLocation

# Pricing rows are gated below this per-row confidence (mirrors P1.7's display gate).
PRICING_CONFIDENCE_FLOOR = 0.7
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


def _validation_ok(schools: list[School]) -> dict[str, Any]:
    total = 0
    ok = 0
    for school in schools:
        report = _as_dict(_as_dict(school.attributes).get("data_validation"))
        if not report:
            continue
        total += 1
        if report.get("status") == "ok":
            ok += 1
    return {"ok": ok, "total": total, "pct": _pct(ok, total)}


def _display_name_overrides(schools: list[School]) -> dict[str, Any]:
    candidates = 0
    overrides = 0
    for school in schools:
        attrs = _as_dict(school.attributes)
        if not attrs.get("display_name_i18n"):
            continue
        candidates += 1
        evidence = _as_dict(attrs.get("display_name_evidence"))
        if evidence.get("status") == "corroborated":
            overrides += 1
    return {"overrides": overrides, "candidates": candidates, "pct": _pct(overrides, candidates)}


def _location_metrics(locations: list[SchoolLocation]) -> tuple[dict[str, Any], int]:
    precise = 0
    with_precision = 0
    coord_groups: dict[tuple[float, float], set[int]] = {}
    for loc in locations:
        meta = _as_dict(loc.geocode_meta)
        precision = meta.get("precision")
        if precision:
            with_precision += 1
            if precision == "exact":
                precise += 1
        if loc.lat is not None and loc.lng is not None:
            key = (round(float(loc.lat), _COORD_ROUNDING), round(float(loc.lng), _COORD_ROUNDING))
            coord_groups.setdefault(key, set()).add(loc.school_id)

    # A "duplicate coordinate group" is one point shared by >=2 distinct schools.
    duplicate_groups = sum(1 for schools in coord_groups.values() if len(schools) >= 2)
    precision_metric = {
        "exact": precise,
        "with_precision": with_precision,
        "pct": _pct(precise, with_precision),
    }
    return precision_metric, duplicate_groups


def _pricing_gate_failures(rows: list[Pricing]) -> dict[str, Any]:
    total = 0
    failing = 0
    for row in rows:
        total += 1
        confidence = _as_dict(row.pricing_context).get("confidence")
        no_source = not (row.source_url or "").strip()
        low_confidence = isinstance(confidence, (int, float)) and confidence < PRICING_CONFIDENCE_FLOOR
        if no_source or low_confidence:
            failing += 1
    return {"failing": failing, "total": total, "pct": _pct(failing, total)}


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
                    select(Pricing).where(
                        Pricing.school_id.in_(school_ids),
                        Pricing.source == PriceSource.SCRAPED_WEBSITE,
                    )
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
        "duplicate_coordinate_groups": duplicate_groups,
        "location_precision_exact": precision_metric,
        "display_name_overrides": _display_name_overrides(schools),
        "spot_check_discrepancy_rate": _spot_check_rate(schools),
        "pricing_rows_failing_gates": _pricing_gate_failures(pricing_rows),
    }
