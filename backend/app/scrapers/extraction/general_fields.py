"""General-info fields (languages, founded year, class size, admissions, operations, services, pricing terms): deterministic extraction and normalisation of the model's answer."""

from __future__ import annotations

import datetime
import re
from typing import Any

from app.schemas.extraction import (
    AdmissionExtractionOutput,
    ExtractedLanguageFocus,
    GeneralInfoExtractionOutput,
    OperationsExtractionOutput,
    PricingTermsExtractionOutput,
    ServicesExtractionOutput,
)
from app.scrapers.extraction.display_naming import (
    _normalize_display_name_i18n,
)
from app.scrapers.extraction.llm_results import (
    _score_general_info_output,
)
from app.scrapers.extraction.section_lines import (
    _collect_matching_lines,
    _extract_working_hours_value,
    _filter_section_values,
    _is_probable_section_noise_line,
    _line_matches_any_pattern,
    _reconcile_pricing_include_exclude,
)
from app.scrapers.extraction.summary_sources import (
    _normalize_summary_source_output,
)
from app.scrapers.extraction.text_values import (
    _extract_clean_text_candidates,
    _normalize_scalar_text,
    _normalize_text_list,
    _pick_primary_text_lang,
    _sanitize_label,
    _split_language_entries,
    _split_text_by_lang,
)
from app.scrapers.extraction_rules import base as rules


def _extract_languages_deterministic(text: str) -> list[ExtractedLanguageFocus]:
    if not text:
        return []
    seen: set[str] = set()
    results: list[ExtractedLanguageFocus] = []
    fragments = re.split(r"[\n\r.!?;]+", text)
    max_items = 8

    for fragment in fragments:
        lowered_fragment = fragment.lower().strip()
        if not lowered_fragment:
            continue
        has_context = any(marker in lowered_fragment for marker in rules._LANGUAGE_CONTEXT_MARKERS)
        for label, variants in rules._DETERMINISTIC_LANGUAGE_PATTERNS:
            if label.lower() in seen:
                continue
            for variant in variants:
                if not re.search(rf"(?<![\w-]){re.escape(variant)}(?![\w-])", lowered_fragment):
                    continue
                if not has_context and not any(marker in lowered_fragment for marker in ("english", "английски")):
                    # Keep false positives low by requiring language-ish context for most hits.
                    break
                seen.add(label.lower())
                results.append(ExtractedLanguageFocus(language=label, level=None))
                break
            if len(results) >= max_items:
                return results
    return results


def _extract_founded_year_deterministic(text: str) -> str | None:
    if not text:
        return None
    current_year = datetime.datetime.now(datetime.UTC).year
    patterns = (
        r"(?:основан[ао]?|създаден[ао]?|учреден[ао]?|established|founded)\D{0,24}((?:19|20)\d{2})",
        r"((?:19|20)\d{2})\D{0,24}(?:основан[ао]?|създаден[ао]?|учреден[ао]?|established|founded)",
    )
    candidates: list[int] = []
    lowered = text.lower()
    for pattern in patterns:
        for match in re.finditer(pattern, lowered, flags=re.IGNORECASE):
            year_text = next((group for group in match.groups() if group), None)
            if not year_text:
                continue
            year = int(year_text)
            if 1850 <= year <= current_year:
                candidates.append(year)
    if not candidates:
        return None
    return str(min(candidates))


def _extract_class_size_deterministic(text: str) -> str | None:
    if not text:
        return None
    lowered = text.lower()
    patterns = (
        r"(?:клас(?:ове)?|груп(?:а|и)|class(?:es)?|group(?:s)?)\D{0,25}(?:до|up to|по|of|с)?\D{0,8}(\d{1,2})\D{0,12}(?:деца|ученици|students|children)?",
        r"(?:до|up to|around|около)?\D{0,6}(\d{1,2})\D{0,12}(?:деца|ученици|students|children)\D{0,15}(?:в|по|per)?\D{0,8}(?:клас|груп|class|group)?",
    )
    candidates: list[int] = []
    for pattern in patterns:
        for match in re.finditer(pattern, lowered, flags=re.IGNORECASE):
            num_text = next((group for group in match.groups() if group), None)
            if not num_text:
                continue
            value = int(num_text)
            if 5 <= value <= 40:
                candidates.append(value)
    if not candidates:
        return None
    return f"{min(candidates)} students"


def _extract_accreditations_deterministic(text: str) -> list[str]:
    if not text:
        return []
    fragments = [fragment.strip().lower() for fragment in re.split(r"[\n\r.!?;]+", text) if fragment.strip()]
    found: list[str] = []
    seen: set[str] = set()

    for fragment in fragments:
        has_context = any(marker in fragment for marker in rules._ACCREDITATION_CONTEXT_MARKERS)
        if not has_context:
            continue
        for label, keywords in rules._ACCREDITATION_KEYWORDS:
            if label in seen:
                continue
            if any(keyword in fragment for keyword in keywords):
                seen.add(label)
                found.append(label)
    return found


def _extract_admission_info_deterministic(text: str) -> AdmissionExtractionOutput:
    deadlines = _collect_matching_lines(
        text,
        rules._ADMISSION_DEADLINE_PATTERNS,
        require_patterns=rules._ADMISSION_DEADLINE_REQUIRE_PATTERNS,
        exclude_patterns=rules._SECTION_DEFAULT_EXCLUDE_PATTERNS,
    )
    required_documents = _collect_matching_lines(
        text,
        rules._ADMISSION_DOCUMENT_PATTERNS,
        require_patterns=rules._ADMISSION_REQUIRED_DOCUMENTS_REQUIRE_PATTERNS,
        exclude_patterns=rules._SECTION_DEFAULT_EXCLUDE_PATTERNS,
    )
    application_steps = _collect_matching_lines(
        text,
        rules._ADMISSION_STEPS_PATTERNS,
        require_patterns=rules._ADMISSION_APPLICATION_STEPS_REQUIRE_PATTERNS,
        exclude_patterns=rules._SECTION_DEFAULT_EXCLUDE_PATTERNS,
    )
    entrance_requirements = _collect_matching_lines(
        text,
        rules._ADMISSION_ENTRANCE_PATTERNS,
        exclude_patterns=rules._SECTION_DEFAULT_EXCLUDE_PATTERNS,
    )
    available_spots = _collect_matching_lines(
        text,
        rules._ADMISSION_SPOTS_PATTERNS,
        require_patterns=rules._ADMISSION_AVAILABLE_SPOTS_REQUIRE_PATTERNS,
        exclude_patterns=rules._SECTION_DEFAULT_EXCLUDE_PATTERNS,
    )

    parsed = AdmissionExtractionOutput(
        deadlines=deadlines,
        required_documents=required_documents,
        application_steps=application_steps,
        entrance_requirements=entrance_requirements,
        available_spots=available_spots,
        has_useful_info=False,
    )
    parsed.has_useful_info = any(
        (
            parsed.deadlines,
            parsed.required_documents,
            parsed.application_steps,
            parsed.entrance_requirements,
            parsed.available_spots,
        )
    )
    return parsed


def _extract_operations_info_deterministic(text: str) -> OperationsExtractionOutput:
    working_hours = _extract_working_hours_value(text)
    day_options = _collect_matching_lines(
        text,
        rules._OPERATIONS_DAY_OPTIONS_PATTERNS,
        exclude_patterns=rules._SECTION_DEFAULT_EXCLUDE_PATTERNS,
    )
    daily_schedule = _collect_matching_lines(
        text,
        rules._OPERATIONS_DAILY_SCHEDULE_PATTERNS,
        require_patterns=rules._OPERATIONS_DAILY_SCHEDULE_REQUIRE_PATTERNS,
        exclude_patterns=rules._SECTION_DEFAULT_EXCLUDE_PATTERNS,
    )
    meals = _collect_matching_lines(
        text,
        rules._OPERATIONS_MEALS_PATTERNS,
        require_patterns=rules._OPERATIONS_MEALS_REQUIRE_PATTERNS,
        exclude_patterns=rules._SECTION_DEFAULT_EXCLUDE_PATTERNS,
    )
    transport = _collect_matching_lines(
        text,
        rules._OPERATIONS_TRANSPORT_PATTERNS,
        exclude_patterns=rules._SECTION_DEFAULT_EXCLUDE_PATTERNS,
    )
    uniforms = _collect_matching_lines(
        text,
        rules._OPERATIONS_UNIFORMS_PATTERNS,
        exclude_patterns=rules._SECTION_DEFAULT_EXCLUDE_PATTERNS,
    )

    parsed = OperationsExtractionOutput(
        working_hours=working_hours,
        day_options=day_options,
        daily_schedule=daily_schedule,
        meals=meals,
        transport=transport,
        uniforms=uniforms,
        has_useful_info=False,
    )
    parsed.has_useful_info = any(
        (
            parsed.working_hours,
            parsed.day_options,
            parsed.daily_schedule,
            parsed.meals,
            parsed.transport,
            parsed.uniforms,
        )
    )
    return parsed


def _extract_services_info_deterministic(text: str) -> ServicesExtractionOutput:
    support_services = _collect_matching_lines(
        text,
        rules._SERVICES_SUPPORT_PATTERNS,
        require_patterns=rules._SERVICES_SUPPORT_REQUIRE_PATTERNS,
        exclude_patterns=(*rules._SECTION_DEFAULT_EXCLUDE_PATTERNS, *rules._SERVICES_SUPPORT_EXCLUDE_PATTERNS),
    )
    safety_features = _collect_matching_lines(
        text,
        rules._SERVICES_SAFETY_PATTERNS,
        require_patterns=rules._SERVICES_SAFETY_REQUIRE_PATTERNS,
        exclude_patterns=rules._SECTION_DEFAULT_EXCLUDE_PATTERNS,
    )
    parsed = ServicesExtractionOutput(
        support_services=support_services,
        safety_features=safety_features,
        has_useful_info=False,
    )
    parsed.has_useful_info = any((parsed.support_services, parsed.safety_features))
    return parsed


def _extract_pricing_terms_deterministic(text: str) -> PricingTermsExtractionOutput:
    discounts = _collect_matching_lines(
        text,
        rules._PRICING_TERMS_DISCOUNT_PATTERNS,
        require_patterns=rules._PRICING_TERMS_DISCOUNTS_REQUIRE_PATTERNS,
        exclude_patterns=rules._SECTION_DEFAULT_EXCLUDE_PATTERNS,
    )
    installments = _collect_matching_lines(
        text,
        rules._PRICING_TERMS_INSTALLMENTS_PATTERNS,
        require_patterns=rules._PRICING_TERMS_INSTALLMENTS_REQUIRE_PATTERNS,
        exclude_patterns=rules._SECTION_DEFAULT_EXCLUDE_PATTERNS,
    )
    included_items = _collect_matching_lines(
        text,
        rules._PRICING_TERMS_INCLUDED_PATTERNS,
        require_patterns=rules._PRICING_TERMS_INCLUDED_REQUIRE_PATTERNS,
        exclude_patterns=rules._SECTION_DEFAULT_EXCLUDE_PATTERNS,
    )
    excluded_items = _collect_matching_lines(
        text,
        rules._PRICING_TERMS_EXCLUDED_PATTERNS,
        require_patterns=rules._PRICING_TERMS_EXCLUDED_REQUIRE_PATTERNS,
        exclude_patterns=rules._SECTION_DEFAULT_EXCLUDE_PATTERNS,
    )
    deposits = _collect_matching_lines(
        text,
        rules._PRICING_TERMS_DEPOSIT_PATTERNS,
        require_patterns=rules._PRICING_TERMS_DEPOSIT_REQUIRE_PATTERNS,
        exclude_patterns=rules._SECTION_DEFAULT_EXCLUDE_PATTERNS,
    )
    application_fees = _collect_matching_lines(
        text,
        rules._PRICING_TERMS_APPLICATION_FEE_PATTERNS,
        require_patterns=rules._PRICING_TERMS_APPLICATION_FEE_REQUIRE_PATTERNS,
        exclude_patterns=rules._SECTION_DEFAULT_EXCLUDE_PATTERNS,
    )
    registration_fees = _collect_matching_lines(
        text,
        rules._PRICING_TERMS_REGISTRATION_FEE_PATTERNS,
        require_patterns=rules._PRICING_TERMS_REGISTRATION_FEE_REQUIRE_PATTERNS,
        exclude_patterns=rules._SECTION_DEFAULT_EXCLUDE_PATTERNS,
    )
    parsed = PricingTermsExtractionOutput(
        discounts=discounts,
        installments=installments,
        included_items=included_items,
        excluded_items=excluded_items,
        deposits=deposits,
        application_fees=application_fees,
        registration_fees=registration_fees,
        has_useful_info=False,
    )
    parsed.has_useful_info = any(
        (
            parsed.discounts,
            parsed.installments,
            parsed.included_items,
            parsed.excluded_items,
            parsed.deposits,
            parsed.application_fees,
            parsed.registration_fees,
        )
    )
    return parsed


def _merge_language_candidates(
    llm_languages: list[ExtractedLanguageFocus],
    deterministic_languages: list[ExtractedLanguageFocus],
) -> list[ExtractedLanguageFocus]:
    merged: list[ExtractedLanguageFocus] = []
    seen: set[tuple[str, str]] = set()
    for candidate in list(llm_languages) + list(deterministic_languages):
        key = (
            (candidate.language or "").strip().lower(),
            (candidate.level or "").strip().lower(),
        )
        if not key[0] or key in seen:
            continue
        seen.add(key)
        merged.append(candidate)
    return merged


def _merge_founded_year(llm_year: str | None, deterministic_year: str | None) -> str | None:
    if deterministic_year is None:
        return llm_year
    if llm_year is None:
        return deterministic_year
    llm_match = re.search(r"(?:19|20)\d{2}", llm_year)
    if llm_match:
        return llm_match.group(0)
    return deterministic_year


def _merge_class_size(llm_class_size: str | None, deterministic_class_size: str | None) -> str | None:
    if deterministic_class_size is None:
        return llm_class_size
    if llm_class_size is None:
        return deterministic_class_size
    llm_num = _extract_class_size_number(llm_class_size)
    if llm_num is not None and 5 <= llm_num <= 40:
        return f"{llm_num} students"
    return deterministic_class_size


def _extract_class_size_number(value: str) -> int | None:
    match = re.search(r"\b(\d{1,2})\b", value or "")
    if not match:
        return None
    return int(match.group(1))


def _normalize_general_info_output(
    parsed: GeneralInfoExtractionOutput,
    country_code: str,
) -> tuple[GeneralInfoExtractionOutput, dict[str, Any] | None, dict[str, str] | None]:
    """Post-process extraction output for cleaner UI-safe fields + optional i18n split."""
    display_name_i18n = _normalize_display_name_i18n(parsed.display_name_i18n, country_code)
    language_entries = _normalize_languages(parsed.languages)
    facilities = _normalize_text_list(parsed.facilities)
    programs = _normalize_text_list(parsed.programs)
    extracurricular = _normalize_text_list(parsed.extracurricular)
    accreditations = _normalize_text_list(parsed.accreditations)
    class_size = _normalize_scalar_text(parsed.class_size, rules._GENERAL_INFO_CLASS_SIZE_MAX_LEN)
    founded_year = _normalize_founded_year(parsed.founded_year)
    admission = _normalize_admission_output(parsed.admission)
    operations = _normalize_operations_output(parsed.operations)
    services = _normalize_services_output(parsed.services)
    pricing_terms = _normalize_pricing_terms_output(parsed.pricing_terms)
    summary_source = _normalize_summary_source_output(
        parsed.summary_source,
        language_candidates=language_entries,
        program_candidates=programs,
    )

    primary_lang = _pick_primary_text_lang(
        country_code=country_code,
        values=(
            [entry.language for entry in language_entries]
            + facilities
            + programs
            + extracurricular
            + accreditations
            + admission.deadlines
            + admission.required_documents
            + admission.application_steps
            + admission.entrance_requirements
            + admission.available_spots
            + operations.day_options
            + operations.daily_schedule
            + operations.meals
            + operations.transport
            + operations.uniforms
            + services.support_services
            + services.safety_features
            + pricing_terms.discounts
            + pricing_terms.installments
            + pricing_terms.included_items
            + pricing_terms.excluded_items
            + pricing_terms.deposits
            + pricing_terms.application_fees
            + pricing_terms.registration_fees
        ),
    )

    split_languages = _split_language_entries(language_entries, primary_lang)
    split_facilities = _split_text_by_lang(facilities, primary_lang)
    split_programs = _split_text_by_lang(programs, primary_lang)
    split_extracurricular = _split_text_by_lang(extracurricular, primary_lang)
    split_accreditations = _split_text_by_lang(accreditations, primary_lang)

    normalized = GeneralInfoExtractionOutput(
        languages=split_languages["primary"],
        facilities=split_facilities["primary"],
        programs=split_programs["primary"],
        extracurricular=split_extracurricular["primary"],
        class_size=class_size,
        founded_year=founded_year,
        accreditations=split_accreditations["primary"],
        admission=admission,
        operations=operations,
        services=services,
        pricing_terms=pricing_terms,
        summary_source=summary_source,
        has_useful_info=False,
    )
    normalized.has_useful_info = _score_general_info_output(normalized) > 0

    extracted_i18n = _build_general_info_i18n(
        split_languages=split_languages,
        split_facilities=split_facilities,
        split_programs=split_programs,
        split_extracurricular=split_extracurricular,
        split_accreditations=split_accreditations,
    )
    return normalized, extracted_i18n, display_name_i18n


def _normalize_admission_output(value: AdmissionExtractionOutput) -> AdmissionExtractionOutput:
    normalized = AdmissionExtractionOutput(
        deadlines=_filter_section_values(
            value.deadlines,
            require_patterns=rules._ADMISSION_DEADLINE_REQUIRE_PATTERNS,
            exclude_patterns=rules._SECTION_DEFAULT_EXCLUDE_PATTERNS,
        ),
        required_documents=_filter_section_values(
            value.required_documents,
            require_patterns=rules._ADMISSION_REQUIRED_DOCUMENTS_REQUIRE_PATTERNS,
            exclude_patterns=rules._SECTION_DEFAULT_EXCLUDE_PATTERNS,
            heading_only_markers=rules._ADMISSION_REQUIRED_DOCUMENTS_HEADING_MARKERS,
        ),
        application_steps=_filter_section_values(
            value.application_steps,
            require_patterns=rules._ADMISSION_APPLICATION_STEPS_REQUIRE_PATTERNS,
            exclude_patterns=rules._SECTION_DEFAULT_EXCLUDE_PATTERNS,
        ),
        entrance_requirements=_filter_section_values(
            value.entrance_requirements,
            exclude_patterns=rules._SECTION_DEFAULT_EXCLUDE_PATTERNS,
        ),
        available_spots=_filter_section_values(
            value.available_spots,
            require_patterns=rules._ADMISSION_AVAILABLE_SPOTS_REQUIRE_PATTERNS,
            exclude_patterns=rules._SECTION_DEFAULT_EXCLUDE_PATTERNS,
        ),
        has_useful_info=False,
    )
    normalized.has_useful_info = any(
        (
            normalized.deadlines,
            normalized.required_documents,
            normalized.application_steps,
            normalized.entrance_requirements,
            normalized.available_spots,
        )
    )
    return normalized


def _normalize_operations_output(value: OperationsExtractionOutput) -> OperationsExtractionOutput:
    working_hours = _normalize_scalar_text(value.working_hours, rules._GENERAL_INFO_ITEM_MAX_LEN)
    if working_hours:
        lowered_working_hours = working_hours.lower()
        if _line_matches_any_pattern(lowered_working_hours, rules._SECTION_DEFAULT_EXCLUDE_PATTERNS):
            working_hours = None
        elif _is_probable_section_noise_line(lowered_working_hours):
            working_hours = None
        elif not (
            _line_matches_any_pattern(lowered_working_hours, rules._OPERATIONS_WORKING_HOURS_PATTERNS)
            or rules._WORKING_HOURS_TIME_PATTERN.search(working_hours)
        ):
            working_hours = None

    normalized = OperationsExtractionOutput(
        working_hours=working_hours,
        day_options=_filter_section_values(
            value.day_options,
            exclude_patterns=rules._SECTION_DEFAULT_EXCLUDE_PATTERNS,
        ),
        daily_schedule=_filter_section_values(
            value.daily_schedule,
            require_patterns=rules._OPERATIONS_DAILY_SCHEDULE_REQUIRE_PATTERNS,
            exclude_patterns=rules._SECTION_DEFAULT_EXCLUDE_PATTERNS,
        ),
        meals=_filter_section_values(
            value.meals,
            require_patterns=rules._OPERATIONS_MEALS_REQUIRE_PATTERNS,
            exclude_patterns=rules._SECTION_DEFAULT_EXCLUDE_PATTERNS,
            heading_only_markers=rules._OPERATIONS_MEALS_HEADING_MARKERS,
        ),
        transport=_filter_section_values(
            value.transport,
            exclude_patterns=rules._SECTION_DEFAULT_EXCLUDE_PATTERNS,
        ),
        uniforms=_filter_section_values(
            value.uniforms,
            exclude_patterns=rules._SECTION_DEFAULT_EXCLUDE_PATTERNS,
        ),
        has_useful_info=False,
    )
    normalized.has_useful_info = any(
        (
            normalized.working_hours,
            normalized.day_options,
            normalized.daily_schedule,
            normalized.meals,
            normalized.transport,
            normalized.uniforms,
        )
    )
    return normalized


def _normalize_services_output(value: ServicesExtractionOutput) -> ServicesExtractionOutput:
    normalized = ServicesExtractionOutput(
        support_services=_filter_section_values(
            value.support_services,
            require_patterns=rules._SERVICES_SUPPORT_REQUIRE_PATTERNS,
            exclude_patterns=(*rules._SECTION_DEFAULT_EXCLUDE_PATTERNS, *rules._SERVICES_SUPPORT_EXCLUDE_PATTERNS),
        ),
        safety_features=_filter_section_values(
            value.safety_features,
            require_patterns=rules._SERVICES_SAFETY_REQUIRE_PATTERNS,
            exclude_patterns=rules._SECTION_DEFAULT_EXCLUDE_PATTERNS,
        ),
        has_useful_info=False,
    )
    normalized.has_useful_info = any((normalized.support_services, normalized.safety_features))
    return normalized


def _normalize_pricing_terms_output(value: PricingTermsExtractionOutput) -> PricingTermsExtractionOutput:
    included_items = _filter_section_values(
        value.included_items,
        require_patterns=rules._PRICING_TERMS_INCLUDED_REQUIRE_PATTERNS,
        exclude_patterns=rules._SECTION_DEFAULT_EXCLUDE_PATTERNS,
    )
    excluded_items = _filter_section_values(
        value.excluded_items,
        require_patterns=rules._PRICING_TERMS_EXCLUDED_REQUIRE_PATTERNS,
        exclude_patterns=rules._SECTION_DEFAULT_EXCLUDE_PATTERNS,
    )
    included_items, excluded_items = _reconcile_pricing_include_exclude(
        included_items,
        excluded_items,
    )

    normalized = PricingTermsExtractionOutput(
        discounts=_filter_section_values(
            value.discounts,
            require_patterns=rules._PRICING_TERMS_DISCOUNTS_REQUIRE_PATTERNS,
            exclude_patterns=rules._SECTION_DEFAULT_EXCLUDE_PATTERNS,
        ),
        installments=_filter_section_values(
            value.installments,
            require_patterns=rules._PRICING_TERMS_INSTALLMENTS_REQUIRE_PATTERNS,
            exclude_patterns=rules._SECTION_DEFAULT_EXCLUDE_PATTERNS,
        ),
        included_items=included_items,
        excluded_items=excluded_items,
        deposits=_filter_section_values(
            value.deposits,
            require_patterns=rules._PRICING_TERMS_DEPOSIT_REQUIRE_PATTERNS,
            exclude_patterns=rules._SECTION_DEFAULT_EXCLUDE_PATTERNS,
        ),
        application_fees=_filter_section_values(
            value.application_fees,
            require_patterns=rules._PRICING_TERMS_APPLICATION_FEE_REQUIRE_PATTERNS,
            exclude_patterns=rules._SECTION_DEFAULT_EXCLUDE_PATTERNS,
        ),
        registration_fees=_filter_section_values(
            value.registration_fees,
            require_patterns=rules._PRICING_TERMS_REGISTRATION_FEE_REQUIRE_PATTERNS,
            exclude_patterns=rules._SECTION_DEFAULT_EXCLUDE_PATTERNS,
        ),
        has_useful_info=False,
    )
    normalized.has_useful_info = any(
        (
            normalized.discounts,
            normalized.installments,
            normalized.included_items,
            normalized.excluded_items,
            normalized.deposits,
            normalized.application_fees,
            normalized.registration_fees,
        )
    )
    return normalized


def _normalize_languages(values: list[ExtractedLanguageFocus]) -> list[ExtractedLanguageFocus]:
    cleaned: list[ExtractedLanguageFocus] = []
    seen: set[tuple[str, str]] = set()
    for value in values:
        for language in _extract_clean_text_candidates(value.language):
            normalized_language = _sanitize_label(language)
            if not normalized_language:
                continue

            level_text = ""
            if value.level is not None:
                level_candidates = _extract_clean_text_candidates(value.level)
                if level_candidates:
                    sanitized_level = _sanitize_label(level_candidates[0])
                    if sanitized_level:
                        level_text = sanitized_level

            dedupe_key = (normalized_language.lower(), level_text.lower())
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)

            cleaned.append(
                ExtractedLanguageFocus(
                    language=normalized_language,
                    level=level_text or None,
                )
            )
            if len(cleaned) >= rules._GENERAL_INFO_LIST_MAX_ITEMS:
                return cleaned
    return cleaned


def _normalize_founded_year(raw_value: Any) -> str | None:
    text = _normalize_scalar_text(raw_value, max_len=32)
    if not text:
        return None
    match = re.search(r"(?:19|20)\d{2}", text)
    if match:
        return match.group(0)
    return text


def _build_general_info_i18n(
    split_languages: dict[str, list[ExtractedLanguageFocus]],
    split_facilities: dict[str, list[str]],
    split_programs: dict[str, list[str]],
    split_extracurricular: dict[str, list[str]],
    split_accreditations: dict[str, list[str]],
) -> dict[str, Any] | None:
    output: dict[str, Any] = {}
    for lang in ("bg", "en"):
        payload: dict[str, Any] = {}
        if split_languages[lang]:
            payload["languages"] = [entry.model_dump() for entry in split_languages[lang]]
        if split_facilities[lang]:
            payload["facilities"] = split_facilities[lang]
        if split_programs[lang]:
            payload["programs"] = split_programs[lang]
        if split_extracurricular[lang]:
            payload["extracurricular"] = split_extracurricular[lang]
        if split_accreditations[lang]:
            payload["accreditations"] = split_accreditations[lang]
        if payload:
            output[lang] = payload
    return output or None
