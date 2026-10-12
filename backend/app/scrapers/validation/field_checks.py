"""Stage 6 checks on ``attributes.extracted``: each field is normalised and kept only where the source text supports it."""

from __future__ import annotations

import datetime
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.field_source import FieldSource, SourceType
from app.models.school import School
from app.models.scrape_log import ScrapeType
from app.models.source_page import SourcePage
from app.schemas.extraction import ExtractedLanguageFocus, SummarySourceExtractionOutput
from app.schemas.validation import ValidationReport
from app.scrapers.extraction import general_fields, summary_sources
from app.scrapers.validation.report_entries import _add_fix, _add_issue
from app.utils.display_gating import admission_value_is_semantically_valid


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
    people = r"(?:деца|ученици|students|children)"
    container = r"(?:клас|класа|класове|група|групи|class|classes|group|groups)"
    for number in numbers:
        escaped = re.escape(number)
        # A pupil count on its own can be a news item, and ``9. клас`` is a grade,
        # not nine pupils per class. Require the number, pupil noun, and class/group
        # noun to participate in the same short size statement.
        if re.search(
            rf"(?<!\d){escaped}(?!\d)[^\n]{{0,18}}{people}[^\n]{{0,18}}{container}",
            source,
        ):
            return True
        if re.search(
            rf"{container}[^\n]{{0,25}}(?<!\d){escaped}(?!\d)[^\n]{{0,12}}{people}",
            source,
        ):
            return True
        if re.search(
            rf"{container}[^\n]{{0,18}}(?:size|capacity|размер|капацитет)"
            rf"[^\n]{{0,12}}(?<!\d){escaped}(?!\d)",
            source,
        ):
            return True
    return False


def _admission_value_is_semantically_valid(
    field_name: str,
    value: str,
    *,
    today: datetime.date | None = None,
) -> bool:
    return admission_value_is_semantically_valid(field_name, value, today=today)


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
    language_candidates = general_fields._normalize_languages(
        [ExtractedLanguageFocus.model_validate(item) for item in (languages or [])]
    )
    normalized_model = summary_sources._normalize_summary_source_output(
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

    normalized["canonical_tags"] = summary_sources._derive_summary_source_canonical_tags(
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


async def _load_evidence_source_text(
    db: AsyncSession, school: School, extracted_dict: dict[str, Any]
) -> str:
    source_text = ""
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
    return source_text


def _normalize_extracted_lists(report: ValidationReport, extracted_dict: dict[str, Any]) -> None:
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


def _check_extracted_programs(
    report: ValidationReport, extracted_dict: dict[str, Any], source_text: str
) -> None:
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


def _check_extracted_languages(
    report: ValidationReport, extracted_dict: dict[str, Any], source_text: str
) -> None:
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


def _check_extracted_founded_year(
    report: ValidationReport, extracted_dict: dict[str, Any], source_text: str
) -> None:
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


def _check_extracted_class_size(
    report: ValidationReport, extracted_dict: dict[str, Any], source_text: str
) -> None:
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


def _check_extracted_admission(
    report: ValidationReport,
    extracted_dict: dict[str, Any],
    admission_info: dict[str, Any],
    source_text: str,
) -> None:
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
            semantic_values = [
                value
                for value in evidence_values
                if _admission_value_is_semantically_valid(admission_key, value)
            ]
            if semantic_values != evidence_values:
                _add_fix(
                    report,
                    code=f"admission_{admission_key}_semantically_invalid",
                    field_path=f"attributes.extracted.admission.{admission_key}",
                    original_value=evidence_values,
                    fixed_value=semantic_values,
                    reason=(
                        "Dropped stale, fragmentary, procurement, or navigation "
                        "content that is not a publishable admission fact."
                    ),
                )
            normalized_admission[admission_key] = semantic_values
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


def _check_extracted_operations(
    report: ValidationReport, extracted_dict: dict[str, Any], source_text: str
) -> None:
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


def _check_extracted_summary_source(
    report: ValidationReport, extracted_dict: dict[str, Any], source_text: str
) -> None:
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


async def _check_field_source_provenance(
    db: AsyncSession, report: ValidationReport, school: School
) -> None:
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
