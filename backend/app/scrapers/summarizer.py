"""Stage 7 summarization orchestration and summary state helpers."""

from __future__ import annotations

import datetime
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.ai.summariser import (
    SummaryAcademic,
    SummaryIdentity,
    SummaryInput,
    SummaryOffering,
    SummaryOperations,
    SummaryPricing,
    generate_school_summary,
    validate_summary_i18n,
)
from app.models import School, SchoolLocation
from app.scrapers.extractor_helpers import _is_low_quality_display_name
from app.services.geocoding.bounds import get_city_bounds
from app.utils.display_gating import iter_blocking_field_paths
from app.utils.i18n_resolver import (
    derive_english_name,
    resolve_address_i18n,
    resolve_display_name_i18n,
    resolve_name_i18n,
)

SUMMARY_GENERATION_SCHEMA_VERSION = 18
SUMMARY_GENERATION_KEY = "summary_generation"
# P1.7: only fully-clean reports are summary-eligible. `needs_review` schools still
# carry error-level issues, and a summary is a whole-school narrative — we don't
# publish one until validation is clean, even though field-level display gating
# lets individual clean fields through.
SUMMARY_ELIGIBLE_VALIDATION_STATUSES = {"ok"}
_NARRATIVE_ADMIN_MARKERS = (
    "правилник",
    "документи",
    "заявления",
    "научете повече",
    "learn more",
    "приемам",
    "cookie",
    "cookies",
    "медии и партньори",
    "за родителите",
    "родители – документи",
    "родители - документи",
)
_NARRATIVE_EVENT_MARKERS = (
    "международен ден",
    "ден на",
    "седмица",
    "игри",
    "спортен празник",
    "среща с",
    "откриване на",
    "архив",
    "week of",
    "sports games",
    "celebration",
    "event",
)
_NARRATIVE_STAFF_MARKERS = (
    "педагогически съветник",
    "методично направление",
    "учители -",
    "advisor",
    "department",
)
_LOCALITY_RE = re.compile(
    r"(?:^|[,;]\s*)(?:гр|с|gr|s|city|village)\.?\s+([^,;\d]+?)(?=\s+\d{4}\b|[,;]|$)",
    flags=re.IGNORECASE,
)


@dataclass
class PreparedSummaryCandidate:
    summary_input: SummaryInput | None
    fingerprint: str | None
    validation_status: str | None
    reason: str | None = None
    up_to_date: bool = False


def _now_utc() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _now_iso() -> str:
    return _now_utc().isoformat()


def _summary_generation_metadata(attributes: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(attributes, Mapping):
        return {}
    payload = attributes.get(SUMMARY_GENERATION_KEY)
    if isinstance(payload, Mapping):
        return dict(payload)
    return {}


def clear_summary_state(school: School, *, downgrade_status: bool = False) -> bool:
    """Clear generated summary payload and metadata."""
    changed = False
    if school.summary_i18n:
        school.summary_i18n = {}
        changed = True

    attrs = dict(school.attributes or {})
    if SUMMARY_GENERATION_KEY in attrs:
        attrs.pop(SUMMARY_GENERATION_KEY, None)
        school.attributes = attrs
        changed = True

    if downgrade_status and school.scrape_status == "summarized":
        school.scrape_status = "extracted"
        changed = True

    if changed:
        school.updated_at = _now_utc()
    return changed


def _has_meaningful_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Mapping):
        return any(_has_meaningful_value(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_has_meaningful_value(item) for item in value)
    if isinstance(value, bool):
        return value
    return True


def _normalize_text_list(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        text = _clean_summary_fragment(value)
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def _clean_narrative_fragment(value: Any) -> str:
    text = _clean_summary_fragment(value)
    if not text:
        return ""
    lowered = text.casefold()
    if any(marker in lowered for marker in _NARRATIVE_ADMIN_MARKERS):
        return ""
    if any(marker in lowered for marker in _NARRATIVE_STAFF_MARKERS):
        return ""
    if any(marker in lowered for marker in _NARRATIVE_EVENT_MARKERS):
        if re.search(r"\b20\d{2}(?:/\d{2,4})?\b", lowered) or len(text) <= 100:
            return ""
    if "[" in text or "]" in text:
        return ""
    return text


def _normalize_narrative_list(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        text = _clean_narrative_fragment(value)
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def _clean_summary_fragment(value: Any) -> str:
    text = str(value or "")
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = text.replace("**", "")
    text = re.sub(r"^[#*\-\u2022\u2013\u2014\s]+", "", text)
    text = re.sub(r"\s+[|/]\s+", " / ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""
    # Drop heading-like fragments that carry no summary value.
    alpha = re.sub(r"[^A-Za-zА-Яа-я]", "", text)
    if (
        not re.search(r"\d", text)
        and alpha
        and len(text) <= 40
        and text.upper() == text
        and (" " in text or "-" in text or len(text) > 8)
    ):
        return ""
    if text in {"Краен срок", "Работно време", "Контакти"}:
        return ""
    return text


def _format_language_focus(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    seen: set[str] = set()
    formatted: list[str] = []
    for item in values:
        if isinstance(item, Mapping):
            language = _clean_summary_fragment(item.get("language"))
            level = _clean_summary_fragment(item.get("level"))
            text = f"{language} ({level})" if language and level else language
        else:
            text = _clean_summary_fragment(item)
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        formatted.append(text)
    return formatted


def _combine_pricing_terms(pricing_terms: Mapping[str, Any] | None) -> list[str]:
    if not isinstance(pricing_terms, Mapping):
        return []
    keys = (
        "discounts",
        "installments",
        "included_items",
        "excluded_items",
        "deposits",
        "application_fees",
        "registration_fees",
    )
    combined: list[str] = []
    for key in keys:
        combined.extend(_normalize_text_list(pricing_terms.get(key)))
    return combined


def _blocked_summary_sections(validation_payload: Mapping[str, Any] | None) -> tuple[set[str], bool]:
    blocked: set[str] = set()
    identity_optional_blocked = False

    # Shared with the API display gate: `iter_blocking_field_paths` yields exactly the
    # error-level issue + actionable spot-check discrepancy paths. This function maps
    # each path onto the coarse summary *section* it withholds.
    for path in iter_blocking_field_paths(validation_payload):
        if not path:
            continue
        if path.startswith("pricing["):
            blocked.add("pricing")
        elif path.startswith("attributes.display_name_i18n"):
            identity_optional_blocked = True
        elif path.startswith("attributes.extracted.admission") or path.startswith("attributes.extracted.operations"):
            blocked.add("operations")
        elif path.startswith("attributes.extracted.services"):
            blocked.add("operations")
        elif path.startswith("attributes.extracted.pricing_terms"):
            blocked.add("pricing")
        elif path.startswith("admission_info.website_extracted"):
            blocked.add("operations")
        elif path.startswith("attributes.extracted"):
            blocked.add("offering")

    return blocked, identity_optional_blocked


def _select_primary_location(school: School) -> SchoolLocation | None:
    if not school.locations:
        return None
    for location in school.locations:
        if location.is_primary:
            return location
    return school.locations[0]


def _extract_locality(value: str | None) -> str | None:
    match = _LOCALITY_RE.search(str(value or "").strip())
    if not match:
        return None
    locality = re.sub(r"\s+", " ", match.group(1)).strip(" .")
    return locality or None


def _primary_location_locality_i18n(
    school: School,
    location: SchoolLocation | None,
    address_i18n: Mapping[str, str],
) -> dict[str, str]:
    if location is None:
        return {}

    locality = {
        lang: extracted
        for lang in ("bg", "en")
        if (extracted := _extract_locality(address_i18n.get(lang)))
    }
    if locality:
        if "bg" not in locality and locality.get("en"):
            locality["bg"] = locality["en"]
        if "en" not in locality and locality.get("bg"):
            locality["en"] = derive_english_name(locality["bg"]) or locality["bg"]
        return locality

    bounds = get_city_bounds(school.country_code, school.city)
    if (
        bounds
        and location.lat is not None
        and location.lng is not None
        and bounds["south"] <= location.lat <= bounds["north"]
        and bounds["west"] <= location.lng <= bounds["east"]
    ):
        normalized_city = str(school.city or "").strip().lower()
        if normalized_city == "sofia":
            return {"bg": "София", "en": "Sofia"}
        raw_city = str(school.city or "").strip()
        if raw_city:
            return {"bg": raw_city, "en": raw_city}
    return {}


def _build_identity(
    school: School,
    attrs: Mapping[str, Any],
    *,
    identity_optional_blocked: bool,
) -> SummaryIdentity:
    effective_attrs = dict(attrs)
    if identity_optional_blocked:
        effective_attrs.pop("display_name_i18n", None)
        effective_attrs.pop("display_name_evidence", None)

    sanitized_display_name = resolve_display_name_i18n(effective_attrs)
    if any(_is_low_quality_display_name(value) for value in sanitized_display_name.values()):
        sanitized_display_name = {}
        effective_attrs.pop("display_name_i18n", None)
        effective_attrs.pop("display_name_evidence", None)

    resolved_name = resolve_name_i18n(school.name_i18n, effective_attrs)
    if not resolved_name:
        raw_name = dict(school.name_i18n or {})
        bg_name = raw_name.get("bg") or raw_name.get("en")
        resolved_name = {"bg": bg_name} if bg_name else {}
        if "en" not in resolved_name and bg_name:
            en_name = derive_english_name(bg_name)
            if en_name:
                resolved_name["en"] = en_name

    display_name = sanitized_display_name

    primary_location = _select_primary_location(school)
    primary_address_i18n: dict[str, str] = {}
    district: str | None = None
    if not identity_optional_blocked and primary_location is not None:
        primary_address_i18n = resolve_address_i18n(primary_location.address_i18n)
        district = primary_location.district
    locality_i18n = _primary_location_locality_i18n(
        school,
        primary_location,
        primary_address_i18n,
    )

    return SummaryIdentity(
        name_i18n=resolved_name,
        display_name_i18n=display_name,
        school_type=school.school_type,
        education_level=school.education_level,
        city=None,
        locality_i18n=locality_i18n,
        primary_address_i18n=primary_address_i18n,
        district=district,
    )


def _derive_name_based_canonical_tags(school: School, attrs: Mapping[str, Any]) -> list[str]:
    display_name = attrs.get("display_name_i18n") if isinstance(attrs.get("display_name_i18n"), Mapping) else {}
    values = list((school.name_i18n or {}).values())
    values.extend(display_name.values())
    normalized = f" {' '.join(str(value).casefold() for value in values if value)} "
    tags: list[str] = []

    def add(tag: str) -> None:
        if tag and tag not in tags:
            tags.append(tag)

    if any(token in normalized for token in ("montessori", "монтесори")):
        add("Montessori")
    if any(token in normalized for token in ("waldorf", "валдорф")):
        add("Waldorf")
    if "cambridge" in normalized or "кембридж" in normalized:
        add("Cambridge")
    if any(token in normalized for token in ("ib programme", "ib program", "international baccalaureate", " ib ")):
        add("IB")
    if "ранно чуждоезиково" in normalized or "early foreign language" in normalized:
        add("Early foreign language education")
    if any(token in normalized for token in ("немска", "немски език", "german school", "nemska gimnaziya", "german-focused")):
        add("German-focused")
    if any(token in normalized for token in ("френска", "френски език", "french school", "frenska gimnaziya", "french-focused")):
        add("French-focused")
    if any(token in normalized for token in ("испанска", "испански език", "spanish school", "spanish-focused")):
        add("Spanish-focused")
    return tags


def _build_offering(attrs: Mapping[str, Any], school: School) -> SummaryOffering | None:
    extracted = attrs.get("extracted")
    if not isinstance(extracted, Mapping):
        return None
    summary_source = (
        extracted.get("summary_source")
        if school.school_type in {"private", "international"} and isinstance(extracted.get("summary_source"), Mapping)
        else {}
    )
    canonical_tags = _normalize_text_list(summary_source.get("canonical_tags"))
    for tag in _derive_name_based_canonical_tags(school, attrs):
        if tag not in canonical_tags:
            canonical_tags.append(tag)

    offering = SummaryOffering(
        languages=_format_language_focus(extracted.get("languages")),
        programs=_normalize_text_list(extracted.get("programs")),
        facilities=_normalize_text_list(extracted.get("facilities")),
        extracurricular=_normalize_text_list(extracted.get("extracurricular")),
        accreditations=_normalize_text_list(extracted.get("accreditations")),
        founded_year=_clean_summary_fragment(extracted.get("founded_year")) or None,
        class_size=_clean_summary_fragment(extracted.get("class_size")) or None,
        positioning=_clean_narrative_fragment(summary_source.get("positioning")) or None,
        teaching_approach=_normalize_narrative_list(summary_source.get("teaching_approach")),
        student_experience=_normalize_narrative_list(summary_source.get("student_experience")),
        community_signals=_normalize_narrative_list(summary_source.get("community_signals")),
        differentiators=_normalize_narrative_list(summary_source.get("differentiators")),
        canonical_tags=canonical_tags,
    )
    return offering if _has_meaningful_value(offering.model_dump(exclude_none=True)) else None


def _build_operations(attrs: Mapping[str, Any]) -> SummaryOperations | None:
    extracted = attrs.get("extracted")
    if not isinstance(extracted, Mapping):
        return None
    admission = extracted.get("admission") if isinstance(extracted.get("admission"), Mapping) else {}
    operations = extracted.get("operations") if isinstance(extracted.get("operations"), Mapping) else {}
    services = extracted.get("services") if isinstance(extracted.get("services"), Mapping) else {}

    section = SummaryOperations(
        admission_deadlines=_normalize_text_list(admission.get("deadlines")),
        required_documents=_normalize_text_list(admission.get("required_documents")),
        application_steps=_normalize_text_list(admission.get("application_steps")),
        entrance_requirements=_normalize_text_list(admission.get("entrance_requirements")),
        available_spots=_normalize_text_list(admission.get("available_spots")),
        working_hours=_clean_summary_fragment(operations.get("working_hours")) or None,
        day_options=_normalize_text_list(operations.get("day_options")),
        meals=_normalize_text_list(operations.get("meals")),
        transport=_normalize_text_list(operations.get("transport")),
        uniforms=_normalize_text_list(operations.get("uniforms")),
        support_services=_normalize_text_list(services.get("support_services")),
        safety_features=_normalize_text_list(services.get("safety_features")),
    )
    return section if _has_meaningful_value(section.model_dump(exclude_none=True)) else None


def _build_pricing(attrs: Mapping[str, Any], school: School) -> SummaryPricing | None:
    extracted = attrs.get("extracted")
    pricing_terms = {}
    if isinstance(extracted, Mapping) and isinstance(extracted.get("pricing_terms"), Mapping):
        pricing_terms = extracted["pricing_terms"]

    categories: list[str] = []
    seen: set[str] = set()
    for row in school.pricing:
        category_value = getattr(row.category, "value", row.category)
        period_value = getattr(row.period, "value", row.period)
        if not category_value or not period_value:
            continue
        label = f"{category_value} ({period_value})"
        key = label.casefold()
        if key in seen:
            continue
        seen.add(key)
        categories.append(label)

    section = SummaryPricing(
        has_pricing=bool(categories or _has_meaningful_value(pricing_terms)),
        fee_categories=categories,
        pricing_terms=_combine_pricing_terms(pricing_terms),
    )
    return section if _has_meaningful_value(section.model_dump(exclude_none=True)) else None


def _build_academic(school: School) -> SummaryAcademic | None:
    exam_types = sorted(
        {
            str(getattr(result.exam_type, "value", result.exam_type))
            for result in school.exam_results
            if getattr(result, "exam_type", None)
        }
    )
    exam_years = sorted({int(result.year) for result in school.exam_results if result.year}, reverse=True)

    admission_info = dict(school.admission_info or {})
    rounds = admission_info.get("rounds") if isinstance(admission_info.get("rounds"), list) else []
    historical_rounds = admission_info.get("historical_rounds") if isinstance(admission_info.get("historical_rounds"), list) else []
    min_score = admission_info.get("min_score")
    historical_min_scores = admission_info.get("historical_min_scores")

    section = SummaryAcademic(
        exam_result_types=exam_types,
        recent_exam_years=exam_years[:3],
        has_admission_thresholds=bool(rounds or historical_rounds or min_score is not None or historical_min_scores),
        has_points_history=bool(rounds or historical_rounds),
        has_min_score_history=bool(min_score is not None or historical_min_scores),
    )
    return section if _has_meaningful_value(section.model_dump(exclude_none=True)) else None


def _summary_input_fingerprint(summary_input: SummaryInput) -> str:
    payload = {
        "_schema_version": SUMMARY_GENERATION_SCHEMA_VERSION,
        "summary_input": summary_input.model_dump(mode="json", exclude_none=True),
    }
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def prepare_summary_candidate(school: School) -> PreparedSummaryCandidate:
    attrs = dict(school.attributes or {})
    validation_payload = attrs.get("data_validation")
    if not isinstance(validation_payload, Mapping):
        return PreparedSummaryCandidate(None, None, None, reason="No current Stage 6 validation report")
    if int(validation_payload.get("_schema_version", validation_payload.get("schema_version", 0)) or 0) != 1:
        return PreparedSummaryCandidate(None, None, None, reason="No current Stage 6 validation report")

    validation_status = str(validation_payload.get("status") or "").lower()
    if validation_status not in SUMMARY_ELIGIBLE_VALIDATION_STATUSES:
        return PreparedSummaryCandidate(
            None,
            None,
            validation_status or None,
            reason=f"Validation status {validation_status or 'unknown'} is not eligible",
        )

    blocked_sections, identity_optional_blocked = _blocked_summary_sections(validation_payload)
    identity = _build_identity(school, attrs, identity_optional_blocked=identity_optional_blocked)
    if not _has_meaningful_value(identity.model_dump(exclude_none=True)):
        return PreparedSummaryCandidate(None, None, validation_status, reason="Missing summary identity")

    offering = None if "offering" in blocked_sections else _build_offering(attrs, school)
    operations = None if "operations" in blocked_sections else _build_operations(attrs)
    pricing = None if "pricing" in blocked_sections else _build_pricing(attrs, school)
    academic = None if "academic" in blocked_sections else _build_academic(school)

    if not any(section is not None for section in (offering, operations, pricing, academic)):
        return PreparedSummaryCandidate(
            None,
            None,
            validation_status,
            reason="No summary-worthy facts after sanitization",
        )

    summary_input = SummaryInput(
        identity=identity,
        offering=offering,
        operations=operations,
        pricing=pricing,
        academic=academic,
    )
    fingerprint = _summary_input_fingerprint(summary_input)
    metadata = _summary_generation_metadata(attrs)
    up_to_date = bool(
        school.summary_i18n
        and metadata.get("_schema_version") == SUMMARY_GENERATION_SCHEMA_VERSION
        and metadata.get("input_fingerprint") == fingerprint
    )
    return PreparedSummaryCandidate(
        summary_input=summary_input,
        fingerprint=fingerprint,
        validation_status=validation_status,
        reason="Summary is up to date" if up_to_date else None,
        up_to_date=up_to_date,
    )


async def summarize_school(
    db: AsyncSession,
    school_id: int,
    country_code: str = "bg",
) -> dict[str, Any]:
    """Generate summary for a single school when stale or missing."""
    query = (
        select(School)
        .options(
            selectinload(School.locations).selectinload(SchoolLocation.age_group_shifts),
            selectinload(School.pricing),
            selectinload(School.exam_results),
        )
        .where(School.id == school_id, School.country_code == country_code)
    )
    result = await db.execute(query)
    school = result.scalar_one_or_none()
    if school is None:
        return {"school_id": school_id, "status": "summary_failed", "reason": "School not found"}

    prepared = prepare_summary_candidate(school)
    if prepared.summary_input is None:
        return {
            "school_id": school_id,
            "status": "skipped",
            "reason": prepared.reason or "School is not eligible for summarization",
        }
    if prepared.up_to_date:
        return {
            "school_id": school_id,
            "status": "skipped",
            "reason": prepared.reason or "Summary is already current",
        }

    attrs = dict(school.attributes or {})
    try:
        summary_result = await generate_school_summary(prepared.summary_input, school_id=school_id)
        summary_result["summary_i18n"] = validate_summary_i18n(
            summary_result.get("summary_i18n")
        )
    except Exception as exc:
        clear_summary_state(school, downgrade_status=True)
        attrs = dict(school.attributes or {})
        attrs[SUMMARY_GENERATION_KEY] = {
            "_schema_version": SUMMARY_GENERATION_SCHEMA_VERSION,
            "attempted_at": _now_iso(),
            "input_fingerprint": prepared.fingerprint,
            "validation_status": prepared.validation_status,
            "last_error": str(exc),
        }
        school.attributes = attrs
        school.updated_at = _now_utc()
        db.add(school)
        await db.commit()
        return {
            "school_id": school_id,
            "status": "summary_failed",
            "reason": str(exc),
        }

    school.summary_i18n = summary_result["summary_i18n"]
    attrs[SUMMARY_GENERATION_KEY] = {
        "_schema_version": SUMMARY_GENERATION_SCHEMA_VERSION,
        "generated_at": _now_iso(),
        "input_fingerprint": prepared.fingerprint,
        "model_tier": summary_result["model_tier"],
        "model": summary_result["model"],
        "generation_mode": summary_result.get("generation_mode"),
        "fallback_reason": summary_result.get("fallback_reason"),
        "validation_status": prepared.validation_status,
        "input_tokens": int(summary_result.get("input_tokens", 0) or 0),
        "output_tokens": int(summary_result.get("output_tokens", 0) or 0),
        "token_cost_usd": float(summary_result.get("token_cost_usd", 0.0) or 0.0),
    }
    school.attributes = attrs
    school.scrape_status = "summarized"
    school.updated_at = _now_utc()
    db.add(school)
    await db.commit()
    return {
        "school_id": school_id,
        "status": "summarized",
        "reason": "Summary generated",
        "input_tokens": int(summary_result.get("input_tokens", 0) or 0),
        "output_tokens": int(summary_result.get("output_tokens", 0) or 0),
        "token_cost_usd": float(summary_result.get("token_cost_usd", 0.0) or 0.0),
    }


async def get_schools_requiring_summary(
    db: AsyncSession,
    country_code: str = "bg",
    city: str | None = None,
    limit: int | None = None,
) -> list[School]:
    """Return schools that are eligible and stale for Stage 7."""
    query = (
        select(School)
        .options(
            selectinload(School.locations).selectinload(SchoolLocation.age_group_shifts),
            selectinload(School.pricing),
            selectinload(School.exam_results),
        )
        .where(
            School.country_code == country_code,
            School.scrape_status.in_(["extracted", "summarized"]),
        )
    )
    if city:
        query = query.where(School.city == city)

    result = await db.execute(query)
    schools = result.scalars().unique().all()
    selected: list[School] = []
    for school in schools:
        prepared = prepare_summary_candidate(school)
        if prepared.summary_input is None or prepared.up_to_date:
            continue
        selected.append(school)
        if limit and len(selected) >= limit:
            break
    return selected


__all__ = [
    "PreparedSummaryCandidate",
    "SUMMARY_ELIGIBLE_VALIDATION_STATUSES",
    "SUMMARY_GENERATION_KEY",
    "SUMMARY_GENERATION_SCHEMA_VERSION",
    "clear_summary_state",
    "get_schools_requiring_summary",
    "prepare_summary_candidate",
    "summarize_school",
]
