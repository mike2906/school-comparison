"""Stage 6 validation: deterministic checks + sampled capable-model spot-checks."""

from __future__ import annotations

import asyncio
import datetime
import hashlib
import json
import logging
import re
from decimal import Decimal
from typing import Any, Literal
from urllib.parse import urldefrag

from pydantic_ai import ModelRetry
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.ai.client import calculate_cost, create_agent, extract_provider_cost_usd, get_model
from app.config import get_settings
from app.models.field_source import FieldSource, SourceType
from app.models.pricing import PriceSource, Pricing
from app.models.school import School
from app.models.scrape_log import ScrapeType
from app.models.source_page import SourcePage
from app.schemas.extraction import ExtractedLanguageFocus, SummarySourceExtractionOutput
from app.schemas.validation import (
    SpotCheckOutput,
    ValidationAutoFix,
    ValidationIssue,
    ValidationReport,
)
from app.scrapers import extractor_helpers as extraction_helpers
from app.scrapers.summarizer import clear_summary_state

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


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _to_json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime.datetime):
        return value.isoformat()
    if isinstance(value, list):
        return [_to_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(k): _to_json_value(v) for k, v in value.items()}
    return value


def _add_issue(
    report: ValidationReport,
    *,
    code: str,
    severity: Literal["error", "warning"],
    field_path: str,
    message: str,
    auto_fixed: bool = False,
) -> None:
    report.issues.append(
        ValidationIssue(
            code=code,
            severity=severity,
            field_path=field_path,
            message=message,
            auto_fixed=auto_fixed,
        )
    )
    report.issue_counts[severity] = int(report.issue_counts.get(severity, 0)) + 1


def _add_fix(
    report: ValidationReport,
    *,
    code: str,
    field_path: str,
    original_value: Any,
    fixed_value: Any,
    reason: str,
) -> None:
    report.auto_fixes.append(
        ValidationAutoFix(
            code=code,
            field_path=field_path,
            original_value=_to_json_value(original_value),
            fixed_value=_to_json_value(fixed_value),
            reason=reason,
        )
    )


def _normalize_text_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        if item is None:
            continue
        text = str(item).strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def _normalize_extracted_languages(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in value:
        language = ""
        level: str | None = None
        if isinstance(item, dict):
            language = str(item.get("language") or "").strip()
            raw_level = item.get("level")
            level = str(raw_level).strip() if raw_level is not None else None
            if level == "":
                level = None
        elif isinstance(item, str):
            language = item.strip()
        if not language:
            continue
        key = (language.casefold(), (level or "").casefold())
        if key in seen:
            continue
        seen.add(key)
        out.append({"language": language, "level": level})
    return out


def _normalize_founded_year(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, int):
        text = str(value)
    elif isinstance(value, float):
        if not value.is_integer():
            return None
        text = str(int(value))
    else:
        text = str(value).strip()
        if text.endswith(".0"):
            head = text[:-2]
            if head.isdigit():
                text = head
    if len(text) != 4 or not text.isdigit():
        return None
    year = int(text)
    current_year = datetime.datetime.now(datetime.timezone.utc).year
    if year < 1800 or year > current_year:
        return None
    return str(year)


_LANGUAGE_ALIASES: dict[str, set[str]] = {
    "english": {"english", "английски"},
    "bulgarian": {"bulgarian", "български"},
    "german": {"german", "deutsch", "немски"},
    "french": {"french", "français", "френски"},
    "spanish": {"spanish", "español", "испански"},
    "italian": {"italian", "италиянски"},
    "russian": {"russian", "руски"},
    "turkish": {"turkish", "турски"},
    "chinese": {"chinese", "китайски"},
}


def _language_terms(value: str) -> set[str]:
    text = (value or "").strip().casefold()
    if not text:
        return set()
    terms = {text}
    for canonical, aliases in _LANGUAGE_ALIASES.items():
        alias_set = {canonical, *aliases}
        if text in alias_set:
            terms.update(alias_set)
            break
    return {term.casefold() for term in terms if term}


def _source_mentions_language(source_text: str, language: str) -> bool:
    source = source_text.casefold()
    for term in _language_terms(language):
        pattern = rf"(?<![\w-]){re.escape(term)}(?![\w-])"
        if re.search(pattern, source):
            return True
    return False


def _source_mentions_founded_year(source_text: str, founded_year: str) -> bool:
    if not founded_year:
        return False
    return re.search(rf"(?<!\d){re.escape(founded_year)}(?!\d)", source_text.casefold()) is not None


def _source_mentions_class_size(source_text: str, class_size: str) -> bool:
    if not class_size:
        return False
    source = source_text.casefold()
    numbers = {m.group(0) for m in re.finditer(r"\b\d{1,2}\b", str(class_size))}
    if not numbers:
        return False
    context_patterns = (
        r"(?:деца|ученици|students|children)",
        r"(?:клас|класа|класове|група|групи|class|classes|group|groups)",
    )
    for number in numbers:
        if re.search(rf"(?<!\d){re.escape(number)}(?!\d)\s*{context_patterns[0]}", source):
            return True
        if re.search(rf"{context_patterns[1]}[^\n]{{0,25}}(?<!\d){re.escape(number)}(?!\d)", source):
            return True
    return False


def _normalize_match_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").casefold()).strip()


def _source_mentions_text_value(source_text: str, value: str) -> bool:
    normalized_value = _normalize_match_text(value)
    if not normalized_value:
        return False
    normalized_source = _normalize_match_text(source_text)
    if not normalized_source:
        return False
    if len(normalized_value) >= 6 and normalized_value in normalized_source:
        return True
    tokens = re.findall(r"[a-zа-я0-9]+", normalized_value, flags=re.IGNORECASE)
    if not tokens:
        return False
    if len(tokens) == 1:
        token = tokens[0]
        return re.search(rf"(?<![\w-]){re.escape(token)}(?![\w-])", normalized_source) is not None
    for idx in range(len(tokens) - 1):
        bigram = f"{tokens[idx]} {tokens[idx + 1]}"
        if bigram in normalized_source:
            return True
    return False


def _filter_values_by_source_evidence(source_text: str, values: list[str]) -> list[str]:
    return [value for value in values if _source_mentions_text_value(source_text, value)]


def _normalize_summary_source(
    value: Any,
    source_text: str,
    *,
    languages: list[dict[str, Any]] | list[Any] | None = None,
    programs: list[str] | None = None,
) -> dict[str, Any]:
    parsed = SummarySourceExtractionOutput.model_validate(value or {})
    language_candidates = extraction_helpers._normalize_languages(
        [ExtractedLanguageFocus.model_validate(item) for item in (languages or [])]
    )
    normalized_model = extraction_helpers._normalize_summary_source_output(
        parsed,
        language_candidates=language_candidates,
        program_candidates=_normalize_text_list(programs or []),
    )
    normalized = normalized_model.model_dump()

    if source_text:
        normalized_source = _normalize_match_text(source_text)

        def has_summary_source_evidence(text: str) -> bool:
            if _source_mentions_text_value(source_text, text):
                return True
            tokens = [
                token
                for token in re.findall(r"[a-zа-я0-9]+", _normalize_match_text(text), flags=re.IGNORECASE)
                if len(token) >= 4
            ]
            if len(tokens) < 2:
                return False
            return sum(1 for token in set(tokens) if token in normalized_source) >= 2

        positioning = normalized.get("positioning")
        if positioning and not has_summary_source_evidence(positioning):
            normalized["positioning"] = None

        for key in (
            "teaching_approach",
            "student_experience",
            "community_signals",
            "differentiators",
        ):
            normalized[key] = [
                item
                for item in _normalize_text_list(normalized.get(key))
                if has_summary_source_evidence(item)
            ]
    else:
        normalized["positioning"] = None
        for key in (
            "teaching_approach",
            "student_experience",
            "community_signals",
            "differentiators",
        ):
            normalized[key] = []

    normalized["canonical_tags"] = extraction_helpers._derive_summary_source_canonical_tags(
        positioning=normalized.get("positioning"),
        teaching_approach=_normalize_text_list(normalized.get("teaching_approach")),
        student_experience=_normalize_text_list(normalized.get("student_experience")),
        community_signals=_normalize_text_list(normalized.get("community_signals")),
        differentiators=_normalize_text_list(normalized.get("differentiators")),
        language_candidates=language_candidates,
        program_candidates=_normalize_text_list(programs or []),
        explicit_tags=_normalize_text_list(normalized.get("canonical_tags")),
    )

    normalized["has_useful_info"] = any(
        (
            normalized.get("positioning"),
            normalized.get("teaching_approach"),
            normalized.get("student_experience"),
            normalized.get("community_signals"),
            normalized.get("differentiators"),
            normalized.get("canonical_tags"),
        )
    )
    return normalized


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


def _pricing_signature(row: Pricing) -> tuple[Any, ...]:
    context_json = json.dumps(_to_json_value(row.pricing_context or {}), sort_keys=True, ensure_ascii=True)
    return (
        row.school_id,
        row.age_group,
        row.category.value if hasattr(row.category, "value") else row.category,
        row.academic_year,
        row.amount,
        row.amount_min,
        row.amount_max,
        row.currency,
        row.period.value if hasattr(row.period, "value") else row.period,
        row.plan_name,
        row.source.value if hasattr(row.source, "value") else row.source,
        row.source_url,
        context_json,
    )


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
            school_result = await db.execute(
                select(School).where(School.id == school_id, School.country_code == country_code)
            )
            school = school_result.scalar_one_or_none()
            if not school:
                return {
                    "school_id": school_id,
                    "status": "validation_failed",
                    "error": "School not found",
                }

            attrs = dict(school.attributes) if isinstance(school.attributes, dict) else {}
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

            for row in pricing_rows:
                row_prefix = f"pricing[{row.id}]"

                normalized_currency = (row.currency or "").strip().upper()
                if len(normalized_currency) != 3 or not normalized_currency.isalpha():
                    normalized_currency = "BGN"
                if row.currency != normalized_currency:
                    _add_fix(
                        report,
                        code="pricing_currency_normalized",
                        field_path=f"{row_prefix}.currency",
                        original_value=row.currency,
                        fixed_value=normalized_currency,
                        reason="Currency codes must be 3-letter uppercase alphabetic values.",
                    )
                    row.currency = normalized_currency

                for field_name in ("amount", "amount_min", "amount_max"):
                    raw_value = getattr(row, field_name)
                    if raw_value is None:
                        continue
                    try:
                        numeric_value = float(raw_value)
                    except (TypeError, ValueError):
                        _add_issue(
                            report,
                            code="invalid_numeric_price_value",
                            severity="error",
                            field_path=f"{row_prefix}.{field_name}",
                            message=f"{field_name} must be numeric when present.",
                        )
                        continue
                    if numeric_value < 0:
                        _add_issue(
                            report,
                            code="negative_price_amount",
                            severity="error",
                            field_path=f"{row_prefix}.{field_name}",
                            message=f"{field_name} cannot be negative.",
                        )

                if row.amount_min is not None and row.amount_max is not None:
                    try:
                        amount_min = float(row.amount_min)
                        amount_max = float(row.amount_max)
                    except (TypeError, ValueError):
                        amount_min = None
                        amount_max = None
                    if amount_min is not None and amount_max is not None and amount_min > amount_max:
                        original = {"amount_min": row.amount_min, "amount_max": row.amount_max}
                        row.amount_min, row.amount_max = row.amount_max, row.amount_min
                        _add_fix(
                            report,
                            code="pricing_range_swapped",
                            field_path=row_prefix,
                            original_value=original,
                            fixed_value={"amount_min": row.amount_min, "amount_max": row.amount_max},
                            reason="amount_min was greater than amount_max.",
                        )

                if row.amount is not None and row.amount_min is not None and row.amount_max is not None:
                    try:
                        amount = float(row.amount)
                        amount_min = float(row.amount_min)
                        amount_max = float(row.amount_max)
                    except (TypeError, ValueError):
                        amount = None
                        amount_min = None
                        amount_max = None
                    if amount is not None and amount_min is not None and amount_max is not None:
                        if amount < amount_min or amount > amount_max:
                            _add_issue(
                                report,
                                code="amount_outside_range",
                                severity="warning",
                                field_path=f"{row_prefix}.amount",
                                message="amount is outside the row's amount_min/amount_max range.",
                            )

                if not (row.source_url or "").strip():
                    _add_issue(
                        report,
                        code="missing_pricing_source_url",
                        severity="warning",
                        field_path=f"{row_prefix}.source_url",
                        message="Scraped pricing row is missing source_url.",
                    )

            seen_signatures: dict[tuple[Any, ...], int] = {}
            duplicate_ids: list[tuple[int, int]] = []
            for row in pricing_rows:
                signature = _pricing_signature(row)
                existing = seen_signatures.get(signature)
                if existing is None:
                    seen_signatures[signature] = row.id
                    continue
                duplicate_ids.append((row.id, existing))

            if duplicate_ids:
                duplicate_id_set = {dup_id for dup_id, _ in duplicate_ids}
                duplicate_prefixes = tuple(f"pricing[{dup_id}]" for dup_id in duplicate_id_set)
                # Keep report trace focused on surviving rows plus explicit duplicate-removal entries.
                report.auto_fixes = [
                    fix for fix in report.auto_fixes if not fix.field_path.startswith(duplicate_prefixes)
                ]
                await db.execute(
                    delete(Pricing).where(Pricing.id.in_([dup_id for dup_id, _ in duplicate_ids]))
                )
                for duplicate_id, keep_id in duplicate_ids:
                    _add_fix(
                        report,
                        code="duplicate_pricing_row_removed",
                        field_path=f"pricing[{duplicate_id}]",
                        original_value={"id": duplicate_id, "duplicate_of": keep_id},
                        fixed_value=None,
                        reason="Removed exact duplicate scraped pricing row.",
                    )

            extracted = attrs.get("extracted")
            extracted_dict = dict(extracted) if isinstance(extracted, dict) else None
            extracted_has_meaningful_payload = False
            source_text = ""

            if extracted_dict is not None:
                extracted_has_meaningful_payload = _has_meaningful_payload(extracted_dict)
                needs_evidence_checks = any(
                    extracted_dict.get(key)
                    for key in (
                        "languages",
                        "class_size",
                        "founded_year",
                        "programs",
                        "admission",
                        "operations",
                        "summary_source",
                    )
                )
                if needs_evidence_checks:
                    pages_result = await db.execute(
                        select(SourcePage.raw_markdown)
                        .where(
                            SourcePage.school_id == school.id,
                            SourcePage.scrape_type == ScrapeType.WEBSITE,
                            SourcePage.is_valid.is_(True),
                            SourcePage.raw_markdown.isnot(None),
                        )
                        .order_by(SourcePage.id)
                    )
                    source_text = "\n\n".join(
                        (row[0] or "").strip() for row in pages_result.all() if (row[0] or "").strip()
                    )

                for list_key in ("facilities", "programs", "extracurricular", "accreditations"):
                    original_list = extracted_dict.get(list_key)
                    normalized_list = _normalize_text_list(original_list)
                    if original_list != normalized_list:
                        _add_fix(
                            report,
                            code=f"normalized_{list_key}",
                            field_path=f"attributes.extracted.{list_key}",
                            original_value=original_list,
                            fixed_value=normalized_list,
                            reason="Removed empty/duplicate values and trimmed whitespace.",
                        )
                        extracted_dict[list_key] = normalized_list

                normalized_programs = extracted_dict.get("programs")
                if not isinstance(normalized_programs, list):
                    # Defensive fallback for legacy payloads.
                    normalized_programs = _normalize_text_list(normalized_programs)
                    extracted_dict["programs"] = normalized_programs
                if source_text and normalized_programs:
                    evidence_programs = _filter_values_by_source_evidence(source_text, normalized_programs)
                    if evidence_programs != normalized_programs:
                        _add_fix(
                            report,
                            code="programs_missing_evidence",
                            field_path="attributes.extracted.programs",
                            original_value=normalized_programs,
                            fixed_value=evidence_programs,
                            reason="Dropped program entries not explicitly supported by source text.",
                        )
                        normalized_programs = evidence_programs
                        extracted_dict["programs"] = normalized_programs

                original_languages = extracted_dict.get("languages")
                normalized_languages = _normalize_extracted_languages(original_languages)
                if original_languages != normalized_languages:
                    _add_fix(
                        report,
                        code="normalized_languages",
                        field_path="attributes.extracted.languages",
                        original_value=original_languages,
                        fixed_value=normalized_languages,
                        reason="Normalized languages list and removed empty/duplicate entries.",
                    )
                    extracted_dict["languages"] = normalized_languages
                if source_text and normalized_languages:
                    evidence_languages = [
                        item
                        for item in normalized_languages
                        if _source_mentions_language(source_text, str(item.get("language") or ""))
                    ]
                    if evidence_languages != normalized_languages:
                        _add_fix(
                            report,
                            code="languages_missing_evidence",
                            field_path="attributes.extracted.languages",
                            original_value=normalized_languages,
                            fixed_value=evidence_languages,
                            reason="Dropped language entries not explicitly supported by source text.",
                        )
                        normalized_languages = evidence_languages
                        extracted_dict["languages"] = normalized_languages

                original_founded_year = extracted_dict.get("founded_year")
                normalized_founded_year = _normalize_founded_year(original_founded_year)
                if original_founded_year != normalized_founded_year:
                    _add_fix(
                        report,
                        code="normalized_founded_year",
                        field_path="attributes.extracted.founded_year",
                        original_value=original_founded_year,
                        fixed_value=normalized_founded_year,
                        reason="Founded year must be a 4-digit year between 1800 and current year.",
                    )
                    extracted_dict["founded_year"] = normalized_founded_year
                if source_text and normalized_founded_year and not _source_mentions_founded_year(source_text, normalized_founded_year):
                    _add_fix(
                        report,
                        code="founded_year_missing_evidence",
                        field_path="attributes.extracted.founded_year",
                        original_value=normalized_founded_year,
                        fixed_value=None,
                        reason="Cleared founded_year because the year is not explicitly present in source text.",
                    )
                    normalized_founded_year = None
                    extracted_dict["founded_year"] = None

                original_class_size = extracted_dict.get("class_size")
                normalized_class_size = str(original_class_size).strip() if original_class_size is not None else None
                if normalized_class_size == "":
                    normalized_class_size = None
                if original_class_size != normalized_class_size:
                    _add_fix(
                        report,
                        code="normalized_class_size",
                        field_path="attributes.extracted.class_size",
                        original_value=original_class_size,
                        fixed_value=normalized_class_size,
                        reason="Normalized class_size whitespace/empty values.",
                    )
                    extracted_dict["class_size"] = normalized_class_size
                if source_text and normalized_class_size and not _source_mentions_class_size(source_text, normalized_class_size):
                    _add_fix(
                        report,
                        code="class_size_missing_evidence",
                        field_path="attributes.extracted.class_size",
                        original_value=normalized_class_size,
                        fixed_value=None,
                        reason="Cleared class_size because no explicit numeric evidence exists in source text.",
                    )
                    extracted_dict["class_size"] = None

                extracted_admission = (
                    extracted_dict.get("admission") if isinstance(extracted_dict.get("admission"), dict) else {}
                )
                if source_text and extracted_admission:
                    admission_keys = (
                        "deadlines",
                        "required_documents",
                        "application_steps",
                        "entrance_requirements",
                        "available_spots",
                    )
                    normalized_admission = dict(extracted_admission)
                    for admission_key in admission_keys:
                        original_values = extracted_admission.get(admission_key)
                        normalized_values = _normalize_text_list(original_values)
                        if normalized_values:
                            evidence_values = _filter_values_by_source_evidence(source_text, normalized_values)
                        else:
                            evidence_values = normalized_values
                        if evidence_values != normalized_values:
                            _add_fix(
                                report,
                                code=f"admission_{admission_key}_missing_evidence",
                                field_path=f"attributes.extracted.admission.{admission_key}",
                                original_value=normalized_values,
                                fixed_value=evidence_values,
                                reason="Dropped admission entries not explicitly supported by source text.",
                            )
                        normalized_admission[admission_key] = evidence_values
                    has_useful_info = any(normalized_admission.get(k) for k in admission_keys)
                    if bool(normalized_admission.get("has_useful_info")) != has_useful_info:
                        _add_fix(
                            report,
                            code="admission_has_useful_info_normalized",
                            field_path="attributes.extracted.admission.has_useful_info",
                            original_value=normalized_admission.get("has_useful_info"),
                            fixed_value=has_useful_info,
                            reason="Aligned has_useful_info with non-empty evidence-backed admission fields.",
                        )
                    normalized_admission["has_useful_info"] = has_useful_info
                    extracted_admission = normalized_admission
                    extracted_dict["admission"] = normalized_admission
                if bool(extracted_admission.get("has_useful_info")):
                    if admission_info.get("website_extracted") != extracted_admission:
                        _add_fix(
                            report,
                            code="admission_sync",
                            field_path="admission_info.website_extracted",
                            original_value=admission_info.get("website_extracted"),
                            fixed_value=extracted_admission,
                            reason=(
                                "Synced admission_info.website_extracted with "
                                "attributes.extracted.admission."
                            ),
                        )
                        admission_info["website_extracted"] = extracted_admission
                elif "website_extracted" in admission_info:
                    original_website_admission = admission_info.get("website_extracted")
                    admission_info.pop("website_extracted", None)
                    _add_fix(
                        report,
                        code="admission_sync_removed",
                        field_path="admission_info.website_extracted",
                        original_value=original_website_admission,
                        fixed_value=None,
                        reason=(
                            "Removed admission_info.website_extracted because extracted admission "
                            "has_useful_info is false."
                        ),
                    )

                extracted_operations = (
                    extracted_dict.get("operations")
                    if isinstance(extracted_dict.get("operations"), dict)
                    else {}
                )
                if source_text and extracted_operations:
                    normalized_operations = dict(extracted_operations)
                    original_working_hours = extracted_operations.get("working_hours")
                    working_hours = (
                        str(original_working_hours).strip()
                        if original_working_hours is not None
                        else None
                    )
                    if not working_hours or not _source_mentions_text_value(
                        source_text, working_hours
                    ):
                        working_hours = None
                    if working_hours != original_working_hours:
                        _add_fix(
                            report,
                            code="operations_working_hours_missing_evidence",
                            field_path="attributes.extracted.operations.working_hours",
                            original_value=original_working_hours,
                            fixed_value=working_hours,
                            reason=(
                                "Cleared working_hours because no explicit supporting "
                                "text exists in the scraped source pages."
                            ),
                        )
                    normalized_operations["working_hours"] = working_hours

                    original_daily_schedule = extracted_operations.get("daily_schedule")
                    daily_schedule = _normalize_text_list(original_daily_schedule)
                    evidence_daily_schedule = _filter_values_by_source_evidence(
                        source_text, daily_schedule
                    )
                    if evidence_daily_schedule != original_daily_schedule:
                        _add_fix(
                            report,
                            code="operations_daily_schedule_missing_evidence",
                            field_path="attributes.extracted.operations.daily_schedule",
                            original_value=original_daily_schedule,
                            fixed_value=evidence_daily_schedule,
                            reason=(
                                "Dropped daily_schedule entries not explicitly supported "
                                "by scraped source text."
                            ),
                        )
                    normalized_operations["daily_schedule"] = evidence_daily_schedule
                    operation_value_keys = (
                        "working_hours",
                        "day_options",
                        "daily_schedule",
                        "meals",
                        "transport",
                        "uniforms",
                    )
                    normalized_operations["has_useful_info"] = any(
                        normalized_operations.get(key) for key in operation_value_keys
                    )
                    extracted_dict["operations"] = normalized_operations

                original_summary_source = extracted_dict.get("summary_source")
                if original_summary_source is not None:
                    normalized_summary_source = _normalize_summary_source(
                        original_summary_source,
                        source_text,
                        languages=extracted_dict.get("languages"),
                        programs=extracted_dict.get("programs"),
                    )
                    if original_summary_source != normalized_summary_source:
                        _add_fix(
                            report,
                            code="normalized_summary_source",
                            field_path="attributes.extracted.summary_source",
                            original_value=original_summary_source,
                            fixed_value=normalized_summary_source,
                            reason="Removed empty, generic, duplicate, or unsupported summary-source entries.",
                        )
                        extracted_dict["summary_source"] = normalized_summary_source

                attrs["extracted"] = extracted_dict

            if extracted_has_meaningful_payload:
                field_source_result = await db.execute(
                    select(FieldSource.id)
                    .where(
                        FieldSource.school_id == school.id,
                        FieldSource.source_type == SourceType.SCRAPED_WEBSITE,
                        FieldSource.category.in_(["general_info", "pricing"]),
                    )
                    .limit(1)
                )
                if field_source_result.scalar_one_or_none() is None:
                    _add_issue(
                        report,
                        code="missing_field_source_provenance",
                        severity="warning",
                        field_path="attributes.extracted",
                        message=(
                            "Extracted payload has no scraped-website FieldSource provenance rows."
                        ),
                    )

            report.status = "needs_review" if report.issue_counts.get("error", 0) > 0 else "ok"
            report.validated_at = _now_iso()

            validation_payload = report.model_dump(mode="json", by_alias=True)
            attrs["data_validation"] = validation_payload

            school.attributes = attrs
            school.admission_info = admission_info
            if report.auto_fixes:
                clear_summary_state(school, downgrade_status=True)
            school.updated_at = datetime.datetime.now(datetime.timezone.utc)
            flag_modified(school, "attributes")
            flag_modified(school, "admission_info")
            db.add(school)

        result: dict[str, Any] = {
            "school_id": school_id,
            "status": report.status,
            "issue_counts": report.issue_counts,
            "issues": len(report.issues),
            "auto_fixes": len(report.auto_fixes),
        }
    except Exception as exc:
        logger.exception("Validation failed for school %s: %s", school_id, exc)
        if db.in_transaction():
            await db.rollback()
        return {
            "school_id": school_id,
            "status": "validation_failed",
            "error": str(exc),
        }

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
            agent.run(user_prompt),
            timeout=max(5.0, float(settings.validation_spot_check_timeout_seconds)),
        )
        input_tokens, output_tokens = extraction_helpers._get_usage(raw_result)
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
            school_result = await db.execute(
                select(School).where(School.id == school_id, School.country_code == country_code)
            )
            school = school_result.scalar_one_or_none()
            if not school:
                return {"school_id": school_id, "status": "skipped", "reason": "School not found"}

            attrs = dict(school.attributes) if isinstance(school.attributes, dict) else {}
            validation_payload = (
                dict(attrs.get("data_validation"))
                if isinstance(attrs.get("data_validation"), dict)
                else {
                    "_schema_version": 1,
                    "validated_at": _now_iso(),
                    "status": "ok",
                    "issue_counts": {"error": 0, "warning": 0},
                    "issues": [],
                    "auto_fixes": [],
                }
            )
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
