"""Shared helper functions for extraction normalization and deterministic parsing."""

from __future__ import annotations

import ast
import contextvars
import datetime
import json
import re
from urllib.parse import urlparse
from typing import Any, Optional

from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError, UnexpectedModelBehavior

from app.config import get_settings
from app.models.pricing import PriceCategory, PricePeriod
from app.models.school import School
from app.models.source_page import SourcePage
from app.scrapers.extraction_rules import get_rules
from app.schemas.extraction import (
    AdmissionExtractionOutput,
    ExtractedLanguageFocus,
    GeneralInfoExtractionOutput,
    OperationsExtractionOutput,
    PricingTermsExtractionOutput,
    ServicesExtractionOutput,
)

_ACTIVE_RULES: contextvars.ContextVar[Any] = contextvars.ContextVar(
    "extraction_rules_module",
    default=get_rules(None),
)

def _rules() -> Any:
    return _ACTIVE_RULES.get()

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
        has_context = any(marker in lowered_fragment for marker in _rules()._LANGUAGE_CONTEXT_MARKERS)
        for label, variants in _rules()._DETERMINISTIC_LANGUAGE_PATTERNS:
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
        has_context = any(marker in fragment for marker in _rules()._ACCREDITATION_CONTEXT_MARKERS)
        if not has_context:
            continue
        for label, keywords in _rules()._ACCREDITATION_KEYWORDS:
            if label in seen:
                continue
            if any(keyword in fragment for keyword in keywords):
                seen.add(label)
                found.append(label)
    return found

def _extract_admission_info_deterministic(text: str) -> AdmissionExtractionOutput:
    deadlines = _collect_matching_lines(
        text,
        _rules()._ADMISSION_DEADLINE_PATTERNS,
        require_patterns=_rules()._ADMISSION_DEADLINE_REQUIRE_PATTERNS,
        exclude_patterns=_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS,
    )
    required_documents = _collect_matching_lines(
        text,
        _rules()._ADMISSION_DOCUMENT_PATTERNS,
        require_patterns=_rules()._ADMISSION_REQUIRED_DOCUMENTS_REQUIRE_PATTERNS,
        exclude_patterns=_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS,
    )
    application_steps = _collect_matching_lines(
        text,
        _rules()._ADMISSION_STEPS_PATTERNS,
        require_patterns=_rules()._ADMISSION_APPLICATION_STEPS_REQUIRE_PATTERNS,
        exclude_patterns=_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS,
    )
    entrance_requirements = _collect_matching_lines(
        text,
        _rules()._ADMISSION_ENTRANCE_PATTERNS,
        exclude_patterns=_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS,
    )
    available_spots = _collect_matching_lines(
        text,
        _rules()._ADMISSION_SPOTS_PATTERNS,
        require_patterns=_rules()._ADMISSION_AVAILABLE_SPOTS_REQUIRE_PATTERNS,
        exclude_patterns=_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS,
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
        _rules()._OPERATIONS_DAY_OPTIONS_PATTERNS,
        exclude_patterns=_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS,
    )
    daily_schedule = _collect_matching_lines(
        text,
        _rules()._OPERATIONS_DAILY_SCHEDULE_PATTERNS,
        require_patterns=_rules()._OPERATIONS_DAILY_SCHEDULE_REQUIRE_PATTERNS,
        exclude_patterns=_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS,
    )
    meals = _collect_matching_lines(
        text,
        _rules()._OPERATIONS_MEALS_PATTERNS,
        require_patterns=_rules()._OPERATIONS_MEALS_REQUIRE_PATTERNS,
        exclude_patterns=_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS,
    )
    transport = _collect_matching_lines(
        text,
        _rules()._OPERATIONS_TRANSPORT_PATTERNS,
        exclude_patterns=_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS,
    )
    uniforms = _collect_matching_lines(
        text,
        _rules()._OPERATIONS_UNIFORMS_PATTERNS,
        exclude_patterns=_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS,
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
        _rules()._SERVICES_SUPPORT_PATTERNS,
        require_patterns=_rules()._SERVICES_SUPPORT_REQUIRE_PATTERNS,
        exclude_patterns=(*_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS, *_rules()._SERVICES_SUPPORT_EXCLUDE_PATTERNS),
    )
    safety_features = _collect_matching_lines(
        text,
        _rules()._SERVICES_SAFETY_PATTERNS,
        require_patterns=_rules()._SERVICES_SAFETY_REQUIRE_PATTERNS,
        exclude_patterns=_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS,
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
        _rules()._PRICING_TERMS_DISCOUNT_PATTERNS,
        require_patterns=_rules()._PRICING_TERMS_DISCOUNTS_REQUIRE_PATTERNS,
        exclude_patterns=_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS,
    )
    installments = _collect_matching_lines(
        text,
        _rules()._PRICING_TERMS_INSTALLMENTS_PATTERNS,
        require_patterns=_rules()._PRICING_TERMS_INSTALLMENTS_REQUIRE_PATTERNS,
        exclude_patterns=_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS,
    )
    included_items = _collect_matching_lines(
        text,
        _rules()._PRICING_TERMS_INCLUDED_PATTERNS,
        require_patterns=_rules()._PRICING_TERMS_INCLUDED_REQUIRE_PATTERNS,
        exclude_patterns=_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS,
    )
    excluded_items = _collect_matching_lines(
        text,
        _rules()._PRICING_TERMS_EXCLUDED_PATTERNS,
        require_patterns=_rules()._PRICING_TERMS_EXCLUDED_REQUIRE_PATTERNS,
        exclude_patterns=_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS,
    )
    deposits = _collect_matching_lines(
        text,
        _rules()._PRICING_TERMS_DEPOSIT_PATTERNS,
        require_patterns=_rules()._PRICING_TERMS_DEPOSIT_REQUIRE_PATTERNS,
        exclude_patterns=_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS,
    )
    application_fees = _collect_matching_lines(
        text,
        _rules()._PRICING_TERMS_APPLICATION_FEE_PATTERNS,
        require_patterns=_rules()._PRICING_TERMS_APPLICATION_FEE_REQUIRE_PATTERNS,
        exclude_patterns=_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS,
    )
    registration_fees = _collect_matching_lines(
        text,
        _rules()._PRICING_TERMS_REGISTRATION_FEE_PATTERNS,
        require_patterns=_rules()._PRICING_TERMS_REGISTRATION_FEE_REQUIRE_PATTERNS,
        exclude_patterns=_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS,
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

def _collect_matching_lines(
    text: str,
    patterns: tuple[str, ...],
    require_patterns: tuple[str, ...] | None = None,
    exclude_patterns: tuple[str, ...] | None = None,
    max_items: int = 6,
) -> list[str]:
    if not text:
        return []
    lines = [line.strip() for line in re.split(r"[\n\r]+", text) if line.strip()]
    collected: list[str] = []
    seen: set[str] = set()
    for line in lines:
        lowered = line.lower()
        if exclude_patterns and _line_matches_any_pattern(lowered, exclude_patterns):
            continue
        if _is_probable_section_noise_line(lowered):
            continue
        if not any(re.search(pattern, lowered, flags=re.IGNORECASE) for pattern in patterns):
            continue
        if require_patterns and not _line_matches_any_pattern(lowered, require_patterns):
            continue
        normalized = _sanitize_label(line, max_len=180)
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        collected.append(normalized)
        if len(collected) >= max_items:
            break
    return collected

def _line_matches_any_pattern(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)

def _normalize_heading_key(text: str) -> str:
    normalized = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text or "")
    normalized = re.sub(r"^\s*[-*#>\d().]+\s*", "", normalized)
    normalized = normalized.strip().strip(":;,.!?")
    normalized = re.sub(r"\s+", " ", normalized).lower()
    return normalized

def _is_heading_only_value(text: str, markers: tuple[str, ...]) -> bool:
    heading = _normalize_heading_key(text)
    if not heading:
        return False
    return heading in {marker.strip().lower() for marker in markers}

def _reconcile_pricing_include_exclude(
    included_items: list[str],
    excluded_items: list[str],
) -> tuple[list[str], list[str]]:
    include_values = _normalize_text_list(included_items)
    exclude_values = _normalize_text_list(excluded_items)

    moved_to_excluded: list[str] = []
    cleaned_includes: list[str] = []
    for value in include_values:
        if _line_matches_any_pattern(value.lower(), _rules()._PRICING_NEGATED_INCLUDE_PATTERNS):
            moved_to_excluded.append(value)
        else:
            cleaned_includes.append(value)

    merged_excluded = _merge_text_values(exclude_values, moved_to_excluded)
    return cleaned_includes, merged_excluded

def _is_probable_section_noise_line(text: str) -> bool:
    if not text:
        return True

    lowered = text.lower()
    if _line_matches_any_pattern(lowered, _rules()._SECTION_REGULATORY_NOISE_PATTERNS):
        return True
    if _line_matches_any_pattern(lowered, _rules()._MENU_BREADCRUMB_NOISE_PATTERNS):
        return True

    nav_hits = sum(
        1
        for pattern in _rules()._SECTION_NAV_NOISE_PATTERNS
        if re.search(pattern, lowered, flags=re.IGNORECASE)
    )
    if nav_hits >= 2:
        return True

    if nav_hits >= 1 and re.search(r"(?:\||>|»|/).*(?:\||>|»|/)", lowered):
        return True

    return False

def _extract_working_hours_value(text: str) -> str | None:
    if not text:
        return None
    snippets = _collect_matching_lines(
        text,
        _rules()._OPERATIONS_WORKING_HOURS_PATTERNS,
        exclude_patterns=_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS,
        max_items=1,
    )
    if snippets:
        return snippets[0]
    time_match = re.search(r"\b([01]?\d|2[0-3])[:.][0-5]\d\b\s*[-–]\s*\b([01]?\d|2[0-3])[:.][0-5]\d\b", text)
    if not time_match:
        return None
    return _sanitize_label(time_match.group(0), max_len=_rules()._GENERAL_INFO_ITEM_MAX_LEN)

def _extract_contact_info_deterministic(text: str) -> dict[str, Any] | None:
    """Extract phone numbers and email addresses from page text using regex.

    This is deterministic (no LLM) and processes all page content. Returns None
    when nothing useful is found.
    """
    if not text:
        return None

    contact: dict[str, list[str]] = {}

    # Extract phone numbers
    phones: list[str] = []
    seen_phones: set[str] = set()
    for match in _rules()._PHONE_PATTERN.finditer(text):
        raw = match.group(0).strip()
        # Normalize: collapse internal whitespace and separators
        normalized = re.sub(r"[\s./\-]+", "", raw)
        if normalized in seen_phones:
            continue
        if len(normalized) < 9:  # Too short to be a real phone
            continue
        seen_phones.add(normalized)
        phones.append(raw.strip())
        if len(phones) >= 3:  # Max 3 phone numbers per school
            break

    if phones:
        contact["phones"] = phones

    # Extract email addresses
    emails: list[str] = []
    seen_emails: set[str] = set()
    for match in _rules()._EMAIL_PATTERN.finditer(text):
        raw = match.group(0).strip()
        # Normalize [at] / [ a t ] → @, collapse spaces around @
        normalized = re.sub(r"\s*\[\s*a\s*t\s*\]\s*", "@", raw, flags=re.IGNORECASE)
        normalized = re.sub(r"\s+", "", normalized).lower()
        # Filter obvious placeholder/noise emails
        if any(noise in normalized for noise in _rules()._CONTACT_NOISE_TOKENS):
            continue
        if normalized in seen_emails:
            continue
        seen_emails.add(normalized)
        emails.append(normalized)
        if len(emails) >= 3:  # Max 3 email addresses
            break

    if emails:
        contact["emails"] = emails

    return contact if contact else None

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

def _merge_text_values(primary: list[str], secondary: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for value in list(primary) + list(secondary):
        normalized = str(value or "").strip()
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        merged.append(normalized)
    return merged

def _select_pages(
    school: School,
    pages: list[SourcePage],
    preferred_categories: list[str],
    use_case: str = "general_info",
    include_tokens: tuple[str, ...] | None = None,
) -> tuple[str, list[str]]:
    """Build bounded prompt content with simple relevance ranking."""
    settings = get_settings()
    max_chars = max(2000, int(settings.extraction_max_content_chars))
    if use_case == "general_info" or use_case.startswith("general_"):
        # Field-focused extraction runs multiple passes; keep each prompt compact.
        max_chars = min(max_chars, 8000)

    preferred = {category.lower() for category in preferred_categories}
    school_host = _canonical_host(school.website_url)

    # Keep pages on the same domain when available to avoid polluting extraction with
    # third-party directories/news portals that may have been navigated.
    same_host_pages = [
        page
        for page in pages
        if _host_matches(_canonical_host(page.source_url), school_host)
    ]
    candidate_pages = same_host_pages or pages

    def score_page(page: SourcePage) -> int:
        score = 0
        category = (page.page_category or "").lower()
        page_url = (page.source_url or "").lower()
        page_text = (page.raw_markdown or "").lower()[:2500]
        if category in preferred:
            score += 100
        elif category:
            score += 20

        # Home/root URLs are usually useful fallback context.
        if page.source_url and re.search(r"https?://[^/]+/?$", page.source_url):
            score += 40

        # Slightly prefer shorter paths and recent pages.
        path_len = len(page.source_url or "")
        score += max(0, 30 - min(path_len, 30))

        page_host = _canonical_host(page.source_url)
        if _host_matches(page_host, school_host):
            score += 80

        if include_tokens:
            url_hits = sum(1 for token in include_tokens if token in page_url)
            text_hits = sum(1 for token in include_tokens if token in page_text)
            if url_hits:
                score += min(90, 35 * url_hits)
            if text_hits:
                score += min(120, 25 * text_hits)

        if use_case == "general_info":
            if category in {"about", "contact"}:
                score += 40
            if any(
                token in page_url
                for token in (
                    "about",
                    "za-nas",
                    "team",
                    "ekip",
                    "program",
                    "obuchenie",
                    "curriculum",
                    "mission",
                    "vision",
                )
            ):
                score += 60
            if any(
                token in page_url
                for token in (
                    "admission",
                    "priem",
                    "policy",
                    "privacy",
                    "gdpr",
                    "rules",
                    "regulation",
                    "internal",
                    "document",
                    "forms",
                    "application",
                    "terms",
                    "conditions",
                    "legal",
                )
            ):
                score -= 90
            if category in {"admission", "pricing"}:
                score -= 35
        elif use_case == "pricing":
            if any(token in page_url for token in ("pricing", "prices", "fees", "tuition", "taksi", "ceni", "price")):
                score += 60
        elif use_case.startswith("general_"):
            if category in {"about", "contact"}:
                score += 45
            if any(token in page_url for token in ("admission", "priem", "policy", "gdpr", "terms", "legal")):
                score -= 90
        return score

    sorted_pages = sorted(candidate_pages, key=score_page, reverse=True)

    content_parts: list[str] = []
    urls_used: list[str] = []
    current_chars = 0
    max_pages = 3 if (use_case == "general_info" or use_case.startswith("general_")) else 4

    for page in sorted_pages:
        if len(urls_used) >= max_pages:
            break

        text = (page.raw_markdown or "").strip()
        if not text:
            continue

        header = f"--- SOURCE: {page.source_url} ---\n"
        candidate = header + text
        remaining = max_chars - current_chars
        if remaining <= 0:
            break

        if len(candidate) > remaining:
            if remaining < 500:
                break
            candidate = candidate[:remaining]

        content_parts.append(candidate)
        urls_used.append(page.source_url or "")
        current_chars += len(candidate)

    return "\n\n".join(content_parts), [url for url in urls_used if url]

def _normalize_general_info_output(
    parsed: GeneralInfoExtractionOutput,
    country_code: str,
) -> tuple[GeneralInfoExtractionOutput, dict[str, Any] | None]:
    """Post-process extraction output for cleaner UI-safe fields + optional i18n split."""
    language_entries = _normalize_languages(parsed.languages)
    facilities = _normalize_text_list(parsed.facilities)
    programs = _normalize_text_list(parsed.programs)
    extracurricular = _normalize_text_list(parsed.extracurricular)
    accreditations = _normalize_text_list(parsed.accreditations)
    class_size = _normalize_scalar_text(parsed.class_size, _rules()._GENERAL_INFO_CLASS_SIZE_MAX_LEN)
    founded_year = _normalize_founded_year(parsed.founded_year)
    admission = _normalize_admission_output(parsed.admission)
    operations = _normalize_operations_output(parsed.operations)
    services = _normalize_services_output(parsed.services)
    pricing_terms = _normalize_pricing_terms_output(parsed.pricing_terms)

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
    return normalized, extracted_i18n

def _normalize_admission_output(value: AdmissionExtractionOutput) -> AdmissionExtractionOutput:
    normalized = AdmissionExtractionOutput(
        deadlines=_filter_section_values(
            value.deadlines,
            require_patterns=_rules()._ADMISSION_DEADLINE_REQUIRE_PATTERNS,
            exclude_patterns=_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS,
        ),
        required_documents=_filter_section_values(
            value.required_documents,
            require_patterns=_rules()._ADMISSION_REQUIRED_DOCUMENTS_REQUIRE_PATTERNS,
            exclude_patterns=_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS,
            heading_only_markers=_rules()._ADMISSION_REQUIRED_DOCUMENTS_HEADING_MARKERS,
        ),
        application_steps=_filter_section_values(
            value.application_steps,
            require_patterns=_rules()._ADMISSION_APPLICATION_STEPS_REQUIRE_PATTERNS,
            exclude_patterns=_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS,
        ),
        entrance_requirements=_filter_section_values(
            value.entrance_requirements,
            exclude_patterns=_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS,
        ),
        available_spots=_filter_section_values(
            value.available_spots,
            require_patterns=_rules()._ADMISSION_AVAILABLE_SPOTS_REQUIRE_PATTERNS,
            exclude_patterns=_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS,
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
    working_hours = _normalize_scalar_text(value.working_hours, _rules()._GENERAL_INFO_ITEM_MAX_LEN)
    if working_hours:
        lowered_working_hours = working_hours.lower()
        if _line_matches_any_pattern(lowered_working_hours, _rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS):
            working_hours = None
        elif _is_probable_section_noise_line(lowered_working_hours):
            working_hours = None
        elif not (
            _line_matches_any_pattern(lowered_working_hours, _rules()._OPERATIONS_WORKING_HOURS_PATTERNS)
            or _rules()._WORKING_HOURS_TIME_PATTERN.search(working_hours)
        ):
            working_hours = None

    normalized = OperationsExtractionOutput(
        working_hours=working_hours,
        day_options=_filter_section_values(
            value.day_options,
            exclude_patterns=_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS,
        ),
        daily_schedule=_filter_section_values(
            value.daily_schedule,
            require_patterns=_rules()._OPERATIONS_DAILY_SCHEDULE_REQUIRE_PATTERNS,
            exclude_patterns=_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS,
        ),
        meals=_filter_section_values(
            value.meals,
            require_patterns=_rules()._OPERATIONS_MEALS_REQUIRE_PATTERNS,
            exclude_patterns=_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS,
            heading_only_markers=_rules()._OPERATIONS_MEALS_HEADING_MARKERS,
        ),
        transport=_filter_section_values(
            value.transport,
            exclude_patterns=_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS,
        ),
        uniforms=_filter_section_values(
            value.uniforms,
            exclude_patterns=_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS,
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
            require_patterns=_rules()._SERVICES_SUPPORT_REQUIRE_PATTERNS,
            exclude_patterns=(*_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS, *_rules()._SERVICES_SUPPORT_EXCLUDE_PATTERNS),
        ),
        safety_features=_filter_section_values(
            value.safety_features,
            require_patterns=_rules()._SERVICES_SAFETY_REQUIRE_PATTERNS,
            exclude_patterns=_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS,
        ),
        has_useful_info=False,
    )
    normalized.has_useful_info = any((normalized.support_services, normalized.safety_features))
    return normalized

def _normalize_pricing_terms_output(value: PricingTermsExtractionOutput) -> PricingTermsExtractionOutput:
    included_items = _filter_section_values(
        value.included_items,
        require_patterns=_rules()._PRICING_TERMS_INCLUDED_REQUIRE_PATTERNS,
        exclude_patterns=_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS,
    )
    excluded_items = _filter_section_values(
        value.excluded_items,
        require_patterns=_rules()._PRICING_TERMS_EXCLUDED_REQUIRE_PATTERNS,
        exclude_patterns=_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS,
    )
    included_items, excluded_items = _reconcile_pricing_include_exclude(
        included_items,
        excluded_items,
    )

    normalized = PricingTermsExtractionOutput(
        discounts=_filter_section_values(
            value.discounts,
            require_patterns=_rules()._PRICING_TERMS_DISCOUNTS_REQUIRE_PATTERNS,
            exclude_patterns=_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS,
        ),
        installments=_filter_section_values(
            value.installments,
            require_patterns=_rules()._PRICING_TERMS_INSTALLMENTS_REQUIRE_PATTERNS,
            exclude_patterns=_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS,
        ),
        included_items=included_items,
        excluded_items=excluded_items,
        deposits=_filter_section_values(
            value.deposits,
            require_patterns=_rules()._PRICING_TERMS_DEPOSIT_REQUIRE_PATTERNS,
            exclude_patterns=_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS,
        ),
        application_fees=_filter_section_values(
            value.application_fees,
            require_patterns=_rules()._PRICING_TERMS_APPLICATION_FEE_REQUIRE_PATTERNS,
            exclude_patterns=_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS,
        ),
        registration_fees=_filter_section_values(
            value.registration_fees,
            require_patterns=_rules()._PRICING_TERMS_REGISTRATION_FEE_REQUIRE_PATTERNS,
            exclude_patterns=_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS,
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
            if len(cleaned) >= _rules()._GENERAL_INFO_LIST_MAX_ITEMS:
                return cleaned
    return cleaned

def _normalize_text_list(values: list[str]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()

    for value in values:
        for candidate in _extract_clean_text_candidates(value):
            label = _sanitize_label(candidate)
            if not label:
                continue
            key = label.lower()
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(label)
            if len(cleaned) >= _rules()._GENERAL_INFO_LIST_MAX_ITEMS:
                return cleaned
    return cleaned

def _filter_section_values(
    values: list[str],
    require_patterns: tuple[str, ...] | None = None,
    exclude_patterns: tuple[str, ...] | None = None,
    heading_only_markers: tuple[str, ...] | None = None,
) -> list[str]:
    filtered: list[str] = []
    seen: set[str] = set()
    for raw_value in values:
        raw_text = str(raw_value or "").strip()
        if not raw_text:
            continue
        lowered_raw = raw_text.lower()
        if exclude_patterns and _line_matches_any_pattern(lowered_raw, exclude_patterns):
            continue
        if _is_probable_section_noise_line(lowered_raw):
            continue

        for candidate in _extract_clean_text_candidates(raw_value):
            label = _sanitize_label(candidate)
            if not label:
                continue
            lowered = label.lower()
            if exclude_patterns and _line_matches_any_pattern(lowered, exclude_patterns):
                continue
            if _is_probable_section_noise_line(lowered):
                continue
            if require_patterns and not _line_matches_any_pattern(lowered, require_patterns):
                continue
            if heading_only_markers and _is_heading_only_value(label, heading_only_markers):
                continue
            key = label.lower()
            if key in seen:
                continue
            seen.add(key)
            filtered.append(label)
            if len(filtered) >= _rules()._GENERAL_INFO_LIST_MAX_ITEMS:
                return filtered
    return filtered

def _normalize_scalar_text(raw_value: Any, max_len: int) -> str | None:
    candidates = _extract_clean_text_candidates(raw_value)
    if not candidates:
        return None
    for candidate in candidates:
        label = _sanitize_label(candidate, max_len=max_len)
        if label:
            return label
    return None

def _normalize_founded_year(raw_value: Any) -> str | None:
    text = _normalize_scalar_text(raw_value, max_len=32)
    if not text:
        return None
    match = re.search(r"(?:19|20)\d{2}", text)
    if match:
        return match.group(0)
    return text

def _extract_clean_text_candidates(raw_value: Any) -> list[str]:
    if raw_value is None:
        return []

    if isinstance(raw_value, (dict, list, tuple)):
        values = _flatten_structured_values(raw_value)
        return [value for value in values if value]

    text = str(raw_value).strip()
    if not text:
        return []

    structured = _try_parse_structured_text(text)
    if structured is not None:
        values = _flatten_structured_values(structured)
        return [value for value in values if value]

    # Split common multi-value separators.
    fragments = re.split(r"[;\n|]+", text)
    if len(fragments) == 1 and "," in text and len(text) < 120:
        fragments = [part.strip() for part in text.split(",")]
    return [fragment.strip() for fragment in fragments if fragment.strip()]

def _try_parse_structured_text(text: str) -> Any | None:
    candidate = text.strip()
    if not candidate:
        return None
    if not ((candidate.startswith("{") and candidate.endswith("}")) or (candidate.startswith("[") and candidate.endswith("]"))):
        return None

    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass

    try:
        return ast.literal_eval(candidate)
    except (ValueError, SyntaxError):
        return None

def _flatten_structured_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, dict):
        preferred: list[str] = []
        for key in _rules()._GENERAL_INFO_TEXT_KEYS:
            if key in value:
                preferred.extend(_flatten_structured_values(value.get(key)))
        if preferred:
            return preferred

        collected: list[str] = []
        for nested in value.values():
            collected.extend(_flatten_structured_values(nested))
        return collected
    if isinstance(value, (list, tuple, set)):
        collected: list[str] = []
        for nested in value:
            collected.extend(_flatten_structured_values(nested))
        return collected
    text = str(value).strip()
    return [text] if text else []

def _sanitize_label(raw_text: str, max_len: int | None = None) -> str | None:
    if max_len is None:
        max_len = _rules()._GENERAL_INFO_ITEM_MAX_LEN

    text = str(raw_text).replace("\x00", "").strip()
    if not text:
        return None

    text = re.sub(r"^[\"'`]+|[\"'`]+$", "", text)
    text = re.sub(r"\s+", " ", text).strip(" ,;|")
    if not text:
        return None

    lowered = text.lower()
    if lowered in _rules()._GENERAL_INFO_JUNK_VALUES:
        return None
    if (text.startswith("{") and text.endswith("}")) or (text.startswith("[") and text.endswith("]")):
        return None
    if len(text) > max_len:
        return None

    # Drop values that are mostly punctuation or URLs.
    if re.fullmatch(r"[\W_]+", text):
        return None
    if text.lower().startswith(("http://", "https://", "www.")):
        return None
    return text

def _pick_primary_text_lang(country_code: str, values: list[str]) -> str:
    default = "bg" if (country_code or "").lower() == "bg" else "en"
    counts = {"bg": 0, "en": 0}
    for value in values:
        bucket = _text_lang_bucket(value)
        if bucket in counts:
            counts[bucket] += 1
    if counts[default] > 0:
        return default
    if counts["bg"] == counts["en"]:
        return default
    return "bg" if counts["bg"] > counts["en"] else "en"

def _split_text_by_lang(values: list[str], primary_lang: str) -> dict[str, list[str]]:
    by_lang = {"bg": [], "en": [], "other": []}
    for value in values:
        by_lang[_text_lang_bucket(value)].append(value)

    primary_values = by_lang[primary_lang] + by_lang["other"]
    secondary_lang = "en" if primary_lang == "bg" else "bg"
    secondary_values = by_lang[secondary_lang]
    return {"primary": primary_values, "bg": by_lang["bg"], "en": by_lang["en"], "secondary": secondary_values}

def _split_language_entries(
    values: list[ExtractedLanguageFocus],
    primary_lang: str,
) -> dict[str, list[ExtractedLanguageFocus]]:
    by_lang = {"bg": [], "en": [], "other": []}
    for value in values:
        by_lang[_text_lang_bucket(value.language)].append(value)

    secondary_lang = "en" if primary_lang == "bg" else "bg"
    return {
        "primary": by_lang[primary_lang] + by_lang["other"],
        "bg": by_lang["bg"],
        "en": by_lang["en"],
        "secondary": by_lang[secondary_lang],
    }

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

def _text_lang_bucket(text: str) -> str:
    sample = (text or "").strip()
    if not sample:
        return "other"
    cyrillic_count = len(re.findall(r"[А-Яа-я]", sample))
    latin_count = len(re.findall(r"[A-Za-z]", sample))
    if cyrillic_count and not latin_count:
        return "bg"
    if latin_count and not cyrillic_count:
        return "en"
    if cyrillic_count > latin_count:
        return "bg"
    if latin_count > cyrillic_count:
        return "en"
    return "other"

def _get_usage(result: Any) -> tuple[int, int]:
    """Extract input/output tokens from pydantic-ai result."""
    usage_obj = result.usage() if callable(getattr(result, "usage", None)) else getattr(result, "usage", None)
    if usage_obj is None:
        return 0, 0

    input_tokens = getattr(usage_obj, "input_tokens", None)
    if input_tokens is None:
        input_tokens = getattr(usage_obj, "request_tokens", 0)

    output_tokens = getattr(usage_obj, "output_tokens", None)
    if output_tokens is None:
        output_tokens = getattr(usage_obj, "response_tokens", 0)

    return int(input_tokens or 0), int(output_tokens or 0)

def _is_model_or_provider_error(exc: Exception) -> bool:
    """Allow capable fallback only for model/provider-level failures."""
    if isinstance(exc, (ModelHTTPError, ModelAPIError)):
        return True
    message = str(exc).lower()
    return any(marker in message for marker in _rules()._PROVIDER_ERROR_MARKERS)

def _is_output_validation_error(exc: Exception) -> bool:
    stack = [exc]
    seen: set[int] = set()

    while stack:
        current = stack.pop()
        current_id = id(current)
        if current_id in seen:
            continue
        seen.add(current_id)

        message = str(current).lower()
        if any(marker in message for marker in _rules()._OUTPUT_VALIDATION_ERROR_MARKERS):
            return True
        if isinstance(current, UnexpectedModelBehavior) and "retry" in message and "validation" in message:
            return True

        cause = getattr(current, "__cause__", None)
        context = getattr(current, "__context__", None)
        if isinstance(cause, Exception):
            stack.append(cause)
        if isinstance(context, Exception):
            stack.append(context)

        nested = getattr(current, "exceptions", None)
        if isinstance(nested, (list, tuple)):
            for child in nested:
                if isinstance(child, Exception):
                    stack.append(child)

    return False

def _score_general_info_output(parsed: GeneralInfoExtractionOutput) -> int:
    """Heuristic quality score for general-info extraction payloads."""
    score = 0
    if any(_is_usable_text(entry.language) for entry in parsed.languages):
        score += 1
    if _count_usable_text_values(parsed.facilities) > 0:
        score += 1
    if _count_usable_text_values(parsed.programs) > 0:
        score += 1
    if _count_usable_text_values(parsed.extracurricular) > 0:
        score += 1
    if _count_usable_text_values(parsed.accreditations) > 0:
        score += 1
    if _is_usable_text(parsed.class_size):
        score += 1
    if _is_usable_text(parsed.founded_year):
        score += 1
    if _count_usable_text_values(parsed.admission.deadlines) > 0:
        score += 1
    if _count_usable_text_values(parsed.admission.required_documents) > 0:
        score += 1
    if _count_usable_text_values(parsed.admission.application_steps) > 0:
        score += 1
    if _count_usable_text_values(parsed.admission.available_spots) > 0:
        score += 1
    if _is_usable_text(parsed.operations.working_hours):
        score += 1
    if _count_usable_text_values(parsed.operations.day_options) > 0:
        score += 1
    if _count_usable_text_values(parsed.operations.meals) > 0:
        score += 1
    if _count_usable_text_values(parsed.services.support_services) > 0:
        score += 1
    if _count_usable_text_values(parsed.services.safety_features) > 0:
        score += 1
    if _count_usable_text_values(parsed.pricing_terms.discounts) > 0:
        score += 1
    return score

def _count_usable_text_values(values: list[str]) -> int:
    return sum(1 for value in values if _is_usable_text(value))

def _is_usable_text(value: Any) -> bool:
    if value is None:
        return False
    return _sanitize_label(str(value)) is not None

def _normalize_key(raw: str) -> str:
    return re.sub(r"[^a-zа-я0-9]+", "", (raw or "").strip().lower())

def _coerce_price_category(raw: Optional[str | PriceCategory]) -> Optional[PriceCategory]:
    if raw is None:
        return None
    if isinstance(raw, PriceCategory):
        return raw
    if isinstance(raw, str) and "." in raw:
        tail = raw.split(".")[-1]
        normalized_tail = _normalize_key(tail)
        if normalized_tail in _rules()._CATEGORY_ALIASES:
            return _rules()._CATEGORY_ALIASES[normalized_tail]
    normalized = _normalize_key(raw)
    return _rules()._CATEGORY_ALIASES.get(normalized)

def _coerce_price_period(raw: Optional[str | PricePeriod]) -> Optional[PricePeriod]:
    if raw is None:
        return None
    if isinstance(raw, PricePeriod):
        return raw
    if isinstance(raw, str) and "." in raw:
        tail = raw.split(".")[-1]
        normalized_tail = _normalize_key(tail)
        if normalized_tail in _rules()._PERIOD_ALIASES:
            return _rules()._PERIOD_ALIASES[normalized_tail]
    normalized = _normalize_key(raw)
    return _rules()._PERIOD_ALIASES.get(normalized)

def _to_optional_float(raw: Any) -> Optional[float]:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)

    text = str(raw).strip().replace("\xa0", " ")
    if not text:
        return None

    # Keep digits + separators, then normalize decimal separator.
    cleaned = re.sub(r"[^0-9,.-]", "", text).replace(",", ".")
    if cleaned.count(".") > 1:
        # If we got thousands separators mixed in, keep last dot as decimal.
        head, tail = cleaned.rsplit(".", 1)
        cleaned = head.replace(".", "") + "." + tail

    try:
        return float(cleaned)
    except ValueError:
        return None

def _format_price_value_text(
    amount: Optional[float],
    amount_min: Optional[float],
    amount_max: Optional[float],
    currency: Optional[str],
) -> Optional[str]:
    code = (currency or "BGN")[:3].upper()
    if amount is not None:
        return f"{amount:.2f} {code}"
    if amount_min is not None and amount_max is not None:
        return f"{amount_min:.2f}-{amount_max:.2f} {code}"
    if amount_min is not None:
        return f"from {amount_min:.2f} {code}"
    if amount_max is not None:
        return f"up to {amount_max:.2f} {code}"
    return None

def _canonical_host(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    try:
        host = (urlparse(url).hostname or "").lower().strip()
    except ValueError:
        return None
    if not host:
        return None
    if host.startswith("www."):
        host = host[4:]
    return host or None

def _host_matches(page_host: Optional[str], school_host: Optional[str]) -> bool:
    if not page_host or not school_host:
        return False
    return page_host == school_host or page_host.endswith(f".{school_host}")

def _utcnow_naive() -> datetime.datetime:
    """Return UTC as naive datetime for columns declared without timezone."""
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
