"""Stage 6 validation: deterministic checks + sampled capable-model spot-checks."""

from __future__ import annotations

import asyncio
import datetime
import hashlib
import json
import logging
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urldefrag

from pydantic_ai import ModelRetry
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.ai.client import calculate_cost, create_agent, extract_provider_cost_usd, get_model, get_usage
from app.config import get_settings
from app.models.pricing import PricePeriod, PriceSource, Pricing
from app.models.school import School, SchoolLocation, SchoolLocationAgeGroupShift
from app.models.scrape_log import ScrapeType
from app.models.source_page import SourcePage
from app.schemas.validation import (
    SpotCheckOutput,
    ValidationReport,
)
from app.scrapers.campus_sync import (
    geocode_campus_locations,
)
from app.scrapers.price_evidence import (
    RULE_AMOUNT_NEAR_LABEL,
    RULE_DEPOSIT,
    RULE_EXPIRED,
    RULE_INSTALLMENT,
    RULE_LEVEL,
    RULE_OFFER_ENDS,
    RULE_PERIOD_CONFLICT,
    RULE_PERIOD_MISSING,
    RULE_PERIOD_UNFIT,
    RULE_SUPERSEDED,
    PriceRow,
    check_price_row,
    name_key,
    page_is_another_institutions,
    period_fits_category,
    proper_name,
    shared_names,
    taught_grades,
)
from app.scrapers.shared_site_check import level_family, site_group_key
from app.scrapers.validation import field_checks, persistence, pricing_checks
from app.scrapers.validation.report_entries import _add_fix, _add_issue, _now_iso, _to_json_value
from app.services.provider_costs import execute_billable_request
from app.utils.academic_year import normalize_academic_year
from app.utils.display_gating import DISPLAY_NAME_FIELD_PATH
from app.utils.i18n_resolver import register_core_names, resolve_display_name_i18n, resolve_name_i18n
from app.utils.website_data import (
    attributes_for_publication,
)

logger = logging.getLogger(__name__)

SPOT_CHECK_CORE_FIELD_PREFIXES: tuple[str, ...] = (
    "attributes.extracted.languages",
    "attributes.extracted.class_size",
    "attributes.extracted.founded_year",
    "attributes.extracted.programs",
    "attributes.extracted.admission",
    "attributes.extracted.operations",
    # Free-text display fields the P1.7 gate withholds when flagged. In scope so a
    # spot-check discrepancy on them survives `_normalize_spot_check_output` and
    # reaches the report; `app.utils.display_gating._FIELD_PATH_DISPLAY_FIELDS` maps
    # each to its display field, and a guard test keeps the two sets aligned.
    "attributes.extracted.facilities",
    "attributes.extracted.extracurricular",
    "attributes.extracted.accreditations",
)
SPOT_CHECK_DISCREPANCY_KINDS: tuple[str, ...] = ("contradiction", "omission", "unsupported")
SPOT_CHECK_GENERIC_ISSUES: set[str] = {
    "expected values are missing.",
    "expected values are missing",
    "unexpected empty list",
}
_SPOT_OMISSION_RE = re.compile(
    r"\b(missing|not provided|not found|not mentioned|incomplete|unsupported|cannot verify|no evidence|omitted)\b",
    re.IGNORECASE,
)
_SPOT_CONTRADICTION_RE = re.compile(
    r"\b(inconsistent|mismatch|conflict|contradict|different|incorrect|wrong|vs)\b",
    re.IGNORECASE,
)


# Bare keys a spot-check may report under `extracted`; used to re-root a discrepancy
# `field_path` back to its canonical `attributes.extracted.<key>` form. This is the
# source of truth for the field-path vocabulary that downstream display gating maps
# (see `app.utils.display_gating`); a drift-guard test asserts the two stay in sync.
SPOT_CHECK_EXTRACTED_ROOT_KEYS: frozenset[str] = frozenset(
    {
        "languages",
        "facilities",
        "programs",
        "extracurricular",
        "class_size",
        "founded_year",
        "accreditations",
        "admission",
        "operations",
        "services",
        "pricing_terms",
        "summary_source",
        "contact",
    }
)


def _normalize_spot_check_field_path(field_path: str) -> str:
    path = (field_path or "").strip()
    if not path:
        return "attributes.extracted"
    if path.startswith("attributes.extracted."):
        return path
    if path == "extracted":
        return "attributes.extracted"
    if path.startswith("extracted."):
        return f"attributes.{path}"
    if path == "language":
        return "attributes.extracted.languages"
    if path == "address":
        return "attributes.extracted.contact.address"
    if path in SPOT_CHECK_EXTRACTED_ROOT_KEYS:
        return f"attributes.extracted.{path}"
    if any(path.startswith(f"{key}.") for key in SPOT_CHECK_EXTRACTED_ROOT_KEYS):
        return f"attributes.extracted.{path}"
    return path


def _spot_check_path_is_core(field_path: str) -> bool:
    for prefix in SPOT_CHECK_CORE_FIELD_PREFIXES:
        if field_path == prefix or field_path.startswith(f"{prefix}."):
            return True
    return False


def _is_emptyish(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, dict)):
        return len(value) == 0
    return False


def _normalize_discrepancy_kind(kind: Any, issue: str) -> str:
    hinted = str(kind or "").strip().casefold()
    if hinted in SPOT_CHECK_DISCREPANCY_KINDS and hinted != "omission":
        return hinted
    issue_text = str(issue or "").strip().casefold()
    if _SPOT_CONTRADICTION_RE.search(issue_text):
        return "contradiction"
    if "unsupported" in issue_text:
        return "unsupported"
    if hinted == "omission":
        # Backward compatibility: when kind is omitted by caller/schema defaulted,
        # keep omission unless issue text clearly implies stronger class above.
        return "omission"
    if _SPOT_OMISSION_RE.search(issue_text):
        return "omission"
    return "unsupported"


def _normalize_discrepancy_issue(issue: Any) -> str:
    text = re.sub(r"\s+", " ", str(issue or "").strip())
    return text


def _normalize_discrepancy_evidence(evidence: Any) -> str | None:
    text = re.sub(r"\s+", " ", str(evidence or "").strip())
    return text or None


def _is_generic_missing_issue(issue_text: str) -> bool:
    normalized = issue_text.casefold()
    if normalized in SPOT_CHECK_GENERIC_ISSUES:
        return True
    return normalized.startswith("expected values are missing")


def _count_spot_check_kinds(parsed: SpotCheckOutput) -> dict[str, int]:
    counts = {kind: 0 for kind in SPOT_CHECK_DISCREPANCY_KINDS}
    for discrepancy in parsed.discrepancies:
        kind = str(discrepancy.kind or "").strip().casefold()
        if kind in counts:
            counts[kind] += 1
    return counts


def _is_actionable_discrepancy_kind(kind: Any) -> bool:
    normalized = str(kind or "").strip().casefold()
    # Omission-only findings are monitoring signals, not pass/fail discrepancies.
    return normalized in {"contradiction", "unsupported"}


def _normalize_spot_check_output(parsed: SpotCheckOutput) -> SpotCheckOutput:
    normalized = []
    changed = False
    for discrepancy in parsed.discrepancies:
        normalized_path = _normalize_spot_check_field_path(discrepancy.field_path)
        issue_text = _normalize_discrepancy_issue(discrepancy.issue)
        kind = _normalize_discrepancy_kind(discrepancy.kind, issue_text)
        evidence = _normalize_discrepancy_evidence(discrepancy.evidence)
        cheap_empty = _is_emptyish(discrepancy.cheap_value)
        capable_empty = _is_emptyish(discrepancy.capable_value)

        updates: dict[str, Any] = {}
        if normalized_path != discrepancy.field_path:
            updates["field_path"] = normalized_path
        if issue_text != discrepancy.issue:
            updates["issue"] = issue_text
        if kind != discrepancy.kind:
            updates["kind"] = kind
        if evidence != discrepancy.evidence:
            updates["evidence"] = evidence
        if updates:
            changed = True
            discrepancy = discrepancy.model_copy(update=updates)

        # Keep spot-check focused on core quality gates for now.
        if not _spot_check_path_is_core(normalized_path):
            changed = True
            continue

        # Ignore generic placeholders with no concrete value/evidence signal.
        if cheap_empty and capable_empty and not evidence and _is_generic_missing_issue(issue_text):
            changed = True
            continue

        # Derive direction from the values. Model labels are not reliable enough to
        # decide whether a finding should cross the publish boundary.
        if cheap_empty and capable_empty:
            changed = True
            continue
        if cheap_empty:
            derived_kind = "omission"
        elif capable_empty:
            derived_kind = "unsupported"
        else:
            derived_kind = "contradiction"
        if kind != derived_kind:
            changed = True
            discrepancy = discrepancy.model_copy(update={"kind": derived_kind})

        # Every retained finding must be auditable. In particular, an unsupported
        # extracted claim without an audit note must not silently block publication.
        if not evidence:
            changed = True
            continue

        normalized.append(discrepancy)

    has_discrepancy = any(_is_actionable_discrepancy_kind(discrepancy.kind) for discrepancy in normalized)
    if parsed.has_discrepancy != has_discrepancy:
        changed = True

    if not changed:
        return parsed
    return parsed.model_copy(update={"has_discrepancy": has_discrepancy, "discrepancies": normalized})


def _filter_spot_check_evidence(parsed: SpotCheckOutput, source_text: str) -> SpotCheckOutput:
    """Drop quote-based findings whose purported evidence is not in the supplied context."""
    normalized_source = re.sub(r"\s+", " ", source_text).casefold()
    kept = []
    for discrepancy in parsed.discrepancies:
        if discrepancy.kind in {"omission", "contradiction"}:
            evidence = re.sub(r"\s+", " ", discrepancy.evidence or "").casefold()
            if not evidence or evidence not in normalized_source:
                continue
        kept.append(discrepancy)
    has_discrepancy = any(_is_actionable_discrepancy_kind(item.kind) for item in kept)
    return parsed.model_copy(update={"has_discrepancy": has_discrepancy, "discrepancies": kept})


_SPOT_CONTEXT_CATEGORY_PRIORITY = {
    "admission": 0,
    "programs": 1,
    "facilities": 2,
    "about": 3,
    "activities": 4,
    "contact": 5,
    "pricing": 6,
    "homepage": 7,
}


def _build_spot_check_context(pages: list[Any], *, max_chars: int) -> str:
    """Build a category-balanced, deduplicated audit context within a hard cap."""
    unique: list[tuple[str, str, str]] = []
    seen_urls: set[str] = set()
    seen_content: set[str] = set()
    for page in sorted(pages, key=lambda item: int(getattr(item, "id", 0) or 0), reverse=True):
        content = str(getattr(page, "raw_markdown", None) or "").strip()
        if not content:
            continue
        url = urldefrag(str(getattr(page, "source_url", None) or ""))[0]
        content_hash = hashlib.sha256(re.sub(r"\s+", " ", content).encode()).hexdigest()
        if url in seen_urls or content_hash in seen_content:
            continue
        seen_urls.add(url)
        seen_content.add(content_hash)
        category = str(getattr(page, "page_category", None) or "other").casefold()
        unique.append((category, url, content))

    if not unique:
        return ""
    buckets: dict[str, list[tuple[str, str, str]]] = {}
    for row in sorted(unique, key=lambda item: item[1]):
        buckets.setdefault(row[0], []).append(row)
    categories = sorted(
        buckets,
        key=lambda category: (_SPOT_CONTEXT_CATEGORY_PRIORITY.get(category, 50), category),
    )
    max_pages = max(1, min(len(unique), max_chars // 1000))
    selected: list[tuple[str, str, str]] = []
    while len(selected) < max_pages and any(buckets.values()):
        for category in categories:
            if buckets[category] and len(selected) < max_pages:
                selected.append(buckets[category].pop(0))

    per_page = max(200, max_chars // len(selected))
    sections = [
        f"[{category}] {url}\n{content}"[:per_page]
        for category, url, content in selected
    ]
    return "\n\n".join(sections)[:max_chars]


def _has_meaningful_payload(value: Any) -> bool:
    if isinstance(value, dict):
        for _key, item in value.items():
            if _has_meaningful_payload(item):
                return True
        return False
    if isinstance(value, list):
        return any(_has_meaningful_payload(item) for item in value)
    if isinstance(value, str):
        return bool(value.strip())
    return value is not None


def has_current_validation_report(attributes: Any, schema_version: int = 1) -> bool:
    """Return True when attributes already contain a current-schema Stage 6 report."""
    if not isinstance(attributes, dict):
        return False
    payload = attributes.get("data_validation")
    if not isinstance(payload, dict):
        return False
    reported_version = payload.get("_schema_version", payload.get("schema_version"))
    try:
        return int(reported_version) == int(schema_version)
    except (TypeError, ValueError):
        return False


def _parse_spot_check_output(result: Any) -> SpotCheckOutput | None:
    output = getattr(result, "output", None)
    if isinstance(output, SpotCheckOutput):
        return output
    if isinstance(output, dict):
        return SpotCheckOutput.model_validate(output)
    data = getattr(result, "data", None)
    if isinstance(data, SpotCheckOutput):
        return data
    if isinstance(data, dict):
        return SpotCheckOutput.model_validate(data)
    return None


# UF45: price rows that break an evidence rule are withheld (error on ``pricing[{id}]``).
_PRICE_EVIDENCE_ERROR_CODES = {
    RULE_AMOUNT_NEAR_LABEL: "pricing_amount_not_near_label",
    RULE_LEVEL: "pricing_label_names_other_level",
    RULE_DEPOSIT: "pricing_deposit_as_tuition",
    RULE_INSTALLMENT: "pricing_installment_as_tuition",
    RULE_EXPIRED: "pricing_offer_expired",
    RULE_SUPERSEDED: "pricing_superseded",
    RULE_PERIOD_UNFIT: "pricing_period_unfit_for_category",
}


@dataclass(frozen=True)
class FeeScope:
    """What tells a school's fees from a sibling's on the site they share."""

    grades: frozenset[int] = frozenset()
    own_name: str | None = None
    # A kindergarten and a school share the site (Druzhba 522/587, Uwekind 634/635).
    shares_site: bool = False


async def school_fee_scope(db: AsyncSession, school: School) -> FeeScope:
    """The grades to scope a school's fees by: empty unless a sibling school shares its site.

    The registry's age groups are coarse (an international K-12 school is listed with
    grades 8-12 only: 505, 529), so they decide whose fee a grade band is only where
    there is another school on the site to own the rest (151/392, 558/565). With them
    comes the school's proper name, when no sibling bears it too: a fee line may name
    the school instead of its grades. ``shares_site`` says an institution of the other
    family (a kindergarten for a school, a school for a kindergarten) shares the site.
    """
    # Local import: school_relations imports app.scrapers, whose package imports us.
    from app.services.school_relations import same_site_institutions

    group = site_group_key(school.website_url)
    family = level_family(school.education_level)
    if not group:
        return FeeScope()
    on_site = [
        other
        for other in await same_site_institutions(db, school)
        if other.id != school.id and site_group_key(other.website_url) == group
    ]
    shares_site = any(level_family(other.education_level) != family for other in on_site)
    siblings = [other for other in on_site if level_family(other.education_level) == "school"]
    if family != "school" or not siblings:
        return FeeScope(shares_site=shares_site)
    groups = (
        await db.execute(
            select(SchoolLocationAgeGroupShift.age_group)
            .join(SchoolLocation, SchoolLocation.id == SchoolLocationAgeGroupShift.location_id)
            .where(SchoolLocation.school_id == school.id)
        )
    ).scalars()

    def name_of(institution: School) -> str | None:
        return proper_name((institution.name_i18n or {}).get("bg"))

    own_name = name_of(school)
    if own_name and any(name_key(name_of(other)) == name_key(own_name) for other in siblings):
        own_name = None
    return FeeScope(frozenset(taught_grades(groups)), own_name, shares_site)


async def _check_price_evidence(
    db: AsyncSession,
    report: ValidationReport,
    school: School,
    rows: list[Pricing],
    *,
    apply_fixes: bool = True,
) -> None:
    """UF45 rules 1-4: check each row against the text of its linked, valid page.

    A null period the page states next to the amount is filled in (an auto-fix, skipped
    with ``apply_fixes=False``, which leaves the rows as they are); a stated period that
    disagrees is a warning; every other finding withholds the row.
    Rows without a valid linked page are left alone: the evidence gate withholds them.
    So are rows of an earlier academic year than the school's newest: extraction keeps
    them as fee history, and the page now shows the new year's fees.
    """
    # Whose page a row is linked to does not depend on its year: checked for every row.
    scope = await school_fee_scope(db, school)
    family = level_family(school.education_level)
    on_siblings_page = [
        row
        for row in rows
        if page_is_another_institutions(row.source_url, scope.grades, family, scope.shares_site)
    ]
    for row in on_siblings_page:
        _add_issue(
            report,
            code=_PRICE_EVIDENCE_ERROR_CODES[RULE_LEVEL],
            severity="error",
            field_path=f"pricing[{row.id}]",
            message=f"the page is a sibling institution's by its address: {row.source_url}",
        )
    rows = [row for row in rows if row not in on_siblings_page]
    years = {row.id: normalize_academic_year(row.academic_year) for row in rows}
    newest = max((year for year in years.values() if year), default=None)
    rows = [row for row in rows if not years[row.id] or years[row.id] == newest]
    page_ids = {row.source_page_id for row in rows if row.source_page_id is not None}
    if not page_ids:
        return
    pages = dict(
        (
            await db.execute(
                select(SourcePage.id, SourcePage.raw_markdown).where(
                    SourcePage.id.in_(page_ids), SourcePage.is_valid.is_(True)
                )
            )
        ).all()
    )
    for row in rows:
        text = pages.get(row.source_page_id)
        if not text:
            continue
        row_prefix = f"pricing[{row.id}]"
        offer_ends = None
        for finding in check_price_row(
            PriceRow.from_pricing(row), text, family, scope.grades, scope.own_name, scope.shares_site
        ):
            if finding.offer_ends is not None:
                offer_ends = finding.offer_ends
            if finding.rule == RULE_OFFER_ENDS:
                continue
            if finding.rule == RULE_PERIOD_MISSING:
                if not apply_fixes or not period_fits_category(
                    getattr(row.category, "value", row.category), finding.period
                ):
                    continue  # extraction left it empty on purpose
                period = PricePeriod(finding.period.lower())
                _add_fix(
                    report,
                    code="pricing_period_from_text",
                    field_path=f"{row_prefix}.period",
                    original_value=None,
                    fixed_value=period.value,
                    reason=f"The page states the period next to the amount: {finding.snippet[:160]}",
                )
                row.period = period
            elif finding.rule == RULE_PERIOD_CONFLICT:
                _add_issue(
                    report,
                    code="pricing_period_conflicts_with_text",
                    severity="warning",
                    field_path=f"{row_prefix}.period",
                    message=f"{finding.detail}: {finding.snippet[:160]}",
                )
            else:
                _add_issue(
                    report,
                    code=_PRICE_EVIDENCE_ERROR_CODES[finding.rule],
                    severity="error",
                    field_path=row_prefix,
                    message=f"{finding.detail}: {finding.snippet[:160]}",
                )
        pricing_checks.store_offer_end(row, offer_ends)


async def _check_display_name(
    db: AsyncSession, report: ValidationReport, school: School, attrs: dict[str, Any]
) -> None:
    """UF45 rule 5: a kindergarten showing the name of a school on the same site.

    A combined site carries the school's brand, and a kindergarten extracted from it
    picks that brand up (556 took 596's "Maple Bear Sofia"; 515 shows "BRITANICA Park
    School"). The kindergarten's display name is withheld, so it falls back to its
    registry name; the school keeps its own. Two school-level institutions of one brand
    (a basic school and a gymnasium) may share a name. Compared against the sibling's
    website candidate as well as its published name, so the result does not depend on
    which is validated first.
    """
    # Local import: school_relations imports app.scrapers, whose package imports us.
    from app.services.school_relations import same_site_institutions

    if level_family(school.education_level) != "kindergarten":
        return
    candidate = resolve_display_name_i18n(attrs)
    group = site_group_key(school.website_url)
    if not candidate or not group:
        return
    for other in await same_site_institutions(db, school):
        if (
            other.id == school.id
            or level_family(other.education_level) != "school"
            or site_group_key(other.website_url) != group
        ):
            continue
        other_names = [
            *resolve_display_name_i18n(other.attributes).values(),
            *resolve_name_i18n(
                other.name_i18n, attributes_for_publication(other.attributes, other.scrape_status)
            ).values(),
            # The published name keeps the type ("Частно основно училище „Пролет“"); the
            # kindergarten's brand candidate is the bare "Пролет".
            *register_core_names(other.name_i18n),
        ]
        shared = shared_names(candidate.values(), other_names)
        if shared:
            _add_issue(
                report,
                code="display_name_matches_sibling",
                severity="error",
                field_path=DISPLAY_NAME_FIELD_PATH,
                message=(
                    f"Display name {sorted(shared)[0]!r} is school {other.id}'s name on the "
                    "same site; the kindergarten's registry name is published instead."
                ),
            )
            return


def _tx_context(db: AsyncSession):
    return db.begin_nested() if db.in_transaction() else db.begin()


async def validate_school_data(
    db: AsyncSession,
    school_id: int,
    country_code: str = "bg",
    run_spot_check: bool = False,
) -> dict[str, Any]:
    """Run deterministic validation checks and persist a report."""
    report = ValidationReport(
        validated_at=_now_iso(),
        status="ok",
        issue_counts={"error": 0, "warning": 0},
    )

    try:
        async with _tx_context(db):
            # Locked and re-read: the caller's session may hold an older copy (the fees
            # agent's), and another stage may have written another attributes key since;
            # this transaction writes the whole JSON back.
            school_result = await db.execute(
                select(School)
                .where(School.id == school_id, School.country_code == country_code)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            school = school_result.scalar_one_or_none()
            if not school:
                return {
                    "school_id": school_id,
                    "status": "validation_failed",
                    "error": "School not found",
                }

            attrs = dict(school.attributes) if isinstance(school.attributes, dict) else {}
            prior_validation = (
                dict(attrs.get("data_validation"))
                if isinstance(attrs.get("data_validation"), dict)
                else {}
            )
            admission_info = (
                dict(school.admission_info) if isinstance(school.admission_info, dict) else {}
            )

            pricing_result = await db.execute(
                select(Pricing)
                .where(
                    Pricing.school_id == school.id,
                    Pricing.source == PriceSource.SCRAPED_WEBSITE,
                )
                .order_by(Pricing.id)
            )
            pricing_rows = pricing_result.scalars().all()

            pricing_checks._check_pricing_rows(report, pricing_rows)

            # Before the duplicate pass: a period filled from the page can make two rows
            # identical, and the copy must go in this run, not the next one.
            await _check_price_evidence(db, report, school, pricing_rows)

            await pricing_checks._remove_duplicate_pricing_rows(db, report, pricing_rows)

            extracted = attrs.get("extracted")
            extracted_dict = dict(extracted) if isinstance(extracted, dict) else None
            extracted_has_meaningful_payload = False
            source_text = ""

            if extracted_dict is not None:
                extracted_has_meaningful_payload = _has_meaningful_payload(extracted_dict)
                source_text = await field_checks._load_evidence_source_text(db, school, extracted_dict)
                field_checks._normalize_extracted_lists(report, extracted_dict)
                field_checks._check_extracted_programs(report, extracted_dict, source_text)
                field_checks._check_extracted_languages(report, extracted_dict, source_text)
                field_checks._check_extracted_founded_year(report, extracted_dict, source_text)
                field_checks._check_extracted_class_size(report, extracted_dict, source_text)
                field_checks._check_extracted_admission(report, extracted_dict, admission_info, source_text)
                field_checks._check_extracted_operations(report, extracted_dict, source_text)
                field_checks._check_extracted_summary_source(report, extracted_dict, source_text)

                attrs["extracted"] = extracted_dict

            if extracted_has_meaningful_payload:
                await field_checks._check_field_source_provenance(db, report, school)

            await _check_display_name(db, report, school, attrs)

            await persistence._persist_validation_report(
                db, report, school, attrs, admission_info, prior_validation
            )

        result: dict[str, Any] = {
            "school_id": school_id,
            "status": report.status,
            "issue_counts": report.issue_counts,
            "issues": len(report.issues),
            "auto_fixes": len(report.auto_fixes),
        }
    except Exception as exc:
        logger.exception("Validation failed for school %s: %s", school_id, exc)
        await persistence._persist_validation_failure(db, school_id, country_code, exc)
        return {
            "school_id": school_id,
            "status": "validation_failed",
            "error": str(exc),
        }

    if not db.in_transaction():
        # Committed: pin campus locations (the geocoding service commits each pin).
        # Nested inside extraction, extract_school does this after its own commit.
        await geocode_campus_locations(db, school_id, country_code=country_code)

    if run_spot_check:
        spot_result = await run_spot_check_for_school(db, school_id, country_code=country_code)
        result["spot_check"] = spot_result
    return result


async def run_spot_check_for_school(
    db: AsyncSession,
    school_id: int,
    country_code: str = "bg",
) -> dict[str, Any]:
    """Run one capable-model spot check and persist result in validation payload."""
    settings = get_settings()
    # Track whether caller already had an explicit transaction open.
    # If not, read queries in this function will auto-open one and nested writes
    # would otherwise never be committed.
    caller_had_tx = db.in_transaction()

    school_result = await db.execute(
        select(School).where(School.id == school_id, School.country_code == country_code)
    )
    school = school_result.scalar_one_or_none()
    if not school:
        return {"school_id": school_id, "status": "skipped", "reason": "School not found"}

    attrs = dict(school.attributes) if isinstance(school.attributes, dict) else {}
    extracted = attrs.get("extracted")
    if not isinstance(extracted, dict) or not _has_meaningful_payload(extracted):
        return {"school_id": school_id, "status": "skipped", "reason": "No extracted payload"}
    # A spot check annotates the current validation report; it must never create one,
    # since a report is what lets website data cross the publish boundary.
    if not has_current_validation_report(attrs):
        return {"school_id": school_id, "status": "skipped", "reason": "No validation report"}
    checked_report_at = attrs["data_validation"].get("validated_at")

    pages_result = await db.execute(
        select(SourcePage)
        .where(
            SourcePage.school_id == school_id,
            SourcePage.scrape_type == ScrapeType.WEBSITE,
            SourcePage.is_valid.is_(True),
            SourcePage.raw_markdown.isnot(None),
        )
        .order_by(SourcePage.id)
    )
    pages = pages_result.scalars().all()
    source_text_cap = max(2000, int(settings.validation_spot_check_max_content_chars))
    source_text = _build_spot_check_context(pages, max_chars=source_text_cap)
    if not source_text:
        return {"school_id": school_id, "status": "skipped", "reason": "No source page content"}

    school_name = (school.name_i18n or {}).get("bg") or (school.name_i18n or {}).get("en") or ""
    extracted_char_cap = max(1000, int(settings.validation_spot_check_max_extracted_chars))
    extracted_json = json.dumps(_to_json_value(extracted), ensure_ascii=False, sort_keys=True)[:extracted_char_cap]

    core_fields = ", ".join(SPOT_CHECK_CORE_FIELD_PREFIXES)
    system_prompt = (
        "You validate scraped school data against source text. "
        "Only evaluate these core field groups: "
        f"{core_fields}. "
        "Treat all other fields as informational and do not include them in discrepancies. "
        "Return has_discrepancy=true only for actionable core discrepancies. "
        "Every discrepancy must include field_path, kind, short issue, cheap_value, capable_value, and evidence. "
        "Omission means cheap_value is empty and capable_value is a concrete source value. "
        "Unsupported means cheap_value is concrete and capable_value is empty. "
        "Contradiction means both values are concrete and conflict. "
        "For omission and contradiction, evidence must be a short exact quote from source text. "
        "For unsupported, evidence must briefly identify the reviewed source context that lacks support. "
        "Do not emit discrepancies when both extracted and source values are absent/unknown."
    )
    user_prompt = (
        f"School: {school_name}\n\n"
        f"Extracted payload (JSON):\n{extracted_json}\n\n"
        f"Source text:\n{source_text}"
    )

    agent = create_agent(
        tier="capable",
        system_prompt=system_prompt,
        result_type=SpotCheckOutput,
        retries=1,
        output_retries=1,
    )

    @agent.output_validator
    def _validate_output(data: Any) -> Any:
        if isinstance(data, SpotCheckOutput):
            if data.has_discrepancy and not data.discrepancies:
                raise ModelRetry("has_discrepancy=true requires at least one discrepancy entry")
            normalized_discrepancies = []
            changed = False
            for discrepancy in data.discrepancies:
                kind = _normalize_discrepancy_kind(discrepancy.kind, discrepancy.issue)
                evidence = _normalize_discrepancy_evidence(discrepancy.evidence)
                if kind == "contradiction" and not evidence:
                    # Keep response usable without retrying the whole call.
                    kind = "omission"
                    changed = True
                update: dict[str, Any] = {}
                if kind != discrepancy.kind:
                    update["kind"] = kind
                if evidence != discrepancy.evidence:
                    update["evidence"] = evidence
                if update:
                    discrepancy = discrepancy.model_copy(update=update)
                    changed = True
                normalized_discrepancies.append(discrepancy)
            has_discrepancy = any(
                _is_actionable_discrepancy_kind(discrepancy.kind) for discrepancy in normalized_discrepancies
            )
            if data.has_discrepancy != has_discrepancy:
                changed = True
            if changed:
                return data.model_copy(
                    update={"has_discrepancy": has_discrepancy, "discrepancies": normalized_discrepancies}
                )
        return data

    input_tokens = 0
    output_tokens = 0
    token_cost_usd = 0.0
    try:
        raw_result = await asyncio.wait_for(
            execute_billable_request(
                lambda: agent.run(user_prompt),
                model=get_model("capable"),
                school_id=school_id,
                stage="validate-data",
            ),
            timeout=max(5.0, float(settings.validation_spot_check_timeout_seconds)),
        )
        input_tokens, output_tokens = get_usage(raw_result)
        token_cost_usd = extract_provider_cost_usd(raw_result) or calculate_cost(
            "capable", input_tokens, output_tokens
        )
        parsed = _parse_spot_check_output(raw_result)
        if parsed is None:
            return {
                "school_id": school_id,
                "status": "skipped",
                "reason": "Invalid spot-check output",
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "token_cost_usd": round(float(token_cost_usd), 6),
            }
        parsed = _normalize_spot_check_output(parsed)
        parsed = _filter_spot_check_evidence(parsed, source_text)
    except Exception as exc:
        # Agent/model failures are expected transient conditions (timeouts/provider issues).
        logger.warning("Spot-check failed for school %s: %s", school_id, exc)
        return {
            "school_id": school_id,
            "status": "failed",
            "error": str(exc),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "token_cost_usd": round(float(token_cost_usd), 6),
        }

    kind_counts = _count_spot_check_kinds(parsed)
    payload = parsed.model_dump(mode="json")
    payload["checked_at"] = _now_iso()
    payload["model_tier"] = "capable"
    payload["quality_scope"] = "core_fields_only"
    payload["kind_counts"] = kind_counts
    payload["input_tokens"] = input_tokens
    payload["output_tokens"] = output_tokens
    payload["token_cost_usd"] = round(float(token_cost_usd), 6)
    payload["model"] = get_model("capable")

    try:
        async with _tx_context(db):
            # Locked and re-read, as in validate_school_data: the whole JSON is written back.
            school_result = await db.execute(
                select(School)
                .where(School.id == school_id, School.country_code == country_code)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            school = school_result.scalar_one_or_none()
            if not school:
                return {"school_id": school_id, "status": "skipped", "reason": "School not found"}

            attrs = dict(school.attributes) if isinstance(school.attributes, dict) else {}
            current_report = attrs.get("data_validation")
            # Re-validation or URL validation may have replaced or cleared the report
            # during the model call; findings about the old payload don't belong on it.
            if not isinstance(current_report, dict) or current_report.get("validated_at") != checked_report_at:
                return {
                    "school_id": school_id,
                    "status": "skipped",
                    "reason": "Validation report changed",
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "token_cost_usd": round(float(token_cost_usd), 6),
                }
            validation_payload = dict(current_report)
            validation_payload["spot_check"] = payload
            attrs["data_validation"] = validation_payload
            school.attributes = attrs
            school.updated_at = datetime.datetime.now(datetime.timezone.utc)
            flag_modified(school, "attributes")
            db.add(school)
        if not caller_had_tx and db.in_transaction():
            await db.commit()
    except Exception as exc:
        # Persistence failures risk data loss and require stack traces for debugging.
        logger.exception("Failed to persist spot-check payload for school %s: %s", school_id, exc)
        if db.in_transaction():
            await db.rollback()
        return {
            "school_id": school_id,
            "status": "failed",
            "error": str(exc),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "token_cost_usd": round(float(token_cost_usd), 6),
        }

    return {
        "school_id": school_id,
        "status": "checked",
        "has_discrepancy": parsed.has_discrepancy,
        "discrepancies": len(parsed.discrepancies),
        "kind_counts": kind_counts,
        "summary": parsed.summary,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "token_cost_usd": round(float(token_cost_usd), 6),
    }


__all__ = [
    "validate_school_data",
    "run_spot_check_for_school",
    "has_current_validation_report",
]
