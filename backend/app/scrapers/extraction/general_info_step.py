"""Steps of the general-info extraction in ``extractor._extract_general_info``.

Each function is one phase of that orchestrator. This module must not import
``app.scrapers.extractor``; the LLM calls and the helpers defined there are passed in by
the orchestrator, so tests that patch ``extractor._run_typed_agent`` still intercept them.
"""

from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.field_source import FieldSource, SourceConfidence, SourceType
from app.models.school import School
from app.models.source_page import SourcePage
from app.schemas.extraction import GeneralInfoExtractionOutput
from app.scrapers.extraction import llm_results, text_values
from app.scrapers.summary_pipeline import clear_summary_state
from app.utils.website_data import prepare_validation_rollover

GENERAL_INFO_SYSTEM_PROMPT = (
    "Extract general school information into structured output. "
    "If the website shows a public-facing school or brand name that differs from the registry name, "
    "capture it in display_name_i18n using explicit website language variants only. "
    "If the same brand text is used in multiple languages, you may repeat the exact same text in both variants. "
    "Do not copy the registry/legal name into display_name_i18n unless the website itself shows it as the display name. "
    "Populate languages, facilities, programs, extracurricular, class_size, founded_year, accreditations, "
    "nested admission/operations/services/pricing_terms sections, and summary_source. "
    "Return only concrete facts explicitly supported by content; do not invent. "
    "For languages, class_size, and founded_year: include values only when explicitly stated in the content. "
    "If not explicit, return languages=[] and class_size/founded_year as null. "
    "When content explicitly mentions facilities, programs, or extracurriculars, include them as short list items "
    "instead of leaving those arrays empty. "
    "For summary_source, extract only short factual phrases from about/mission/philosophy/program content. "
    "Use it to capture positioning, teaching approach, student experience, community signals, and differentiators. "
    "Do not write polished marketing prose, slogans, or full paragraphs. "
    "Do not include generic claims like 'quality education' or 'innovative school' unless the phrase is made specific by surrounding detail. "
    "Set has_useful_info=true whenever at least one concrete fact is extracted."
)

GENERAL_INFO_RECOVERY_PROMPT = (
    "Extract additional concrete school facts that are explicitly present. "
    "Prioritize filling missing display_name_i18n, languages, facilities, programs, extracurricular, "
    "class size, founded year, accreditations, admission, operations, services, pricing terms, and summary_source. "
    "Do not invent values. "
    "Only populate display_name_i18n when the website explicitly shows a public-facing name. "
    "For languages, class_size, and founded_year: only include them when explicitly stated; otherwise "
    "leave them empty/null. "
    "For summary_source, prefer concise evidence-backed phrases over polished prose."
)

Augment = Callable[[GeneralInfoExtractionOutput], GeneralInfoExtractionOutput]


async def _recover_low_quality_output(
    parsed: GeneralInfoExtractionOutput,
    detail_note: str | None,
    *,
    run_agent: Callable[..., Awaitable[Any]],
    augment: Augment,
    school: School,
    school_name: str,
    all_page_text: str,
    timeout_seconds: float,
    llm_stats: Any,
    min_quality_score: int,
    max_chars: int,
) -> tuple[GeneralInfoExtractionOutput, str | None, int, int, float]:
    """Re-ask over the whole site text when the first answer scores below the gate.

    Returns the answer, the detail note and the extra input tokens, output tokens and cost.
    """
    input_tokens = 0
    output_tokens = 0
    token_cost_usd = 0.0
    current_quality = llm_results._score_general_info_output(parsed)
    if current_quality < min_quality_score:
        recovery_prompt = GENERAL_INFO_RECOVERY_PROMPT
        recovery_user_prompt = f"School: {school_name}\n\nContent:\n{all_page_text[:max_chars]}"
        recovered, in2, out2, cost2 = await run_agent(
            system_prompt=recovery_prompt,
            user_prompt=recovery_user_prompt,
            result_type=GeneralInfoExtractionOutput,
            timeout_seconds=timeout_seconds,
            llm_stats=llm_stats,
            school_id=school.id,
        )
        input_tokens += in2
        output_tokens += out2
        token_cost_usd += cost2
        if recovered is not None:
            recovered_augmented = augment(recovered)
            recovered_quality = llm_results._score_general_info_output(recovered_augmented)
            if recovered_quality >= current_quality:
                parsed = recovered_augmented
                detail_note = (
                    f"quality gate recovery improved/kept quality ({current_quality}->{recovered_quality})"
                )
        elif detail_note is None:
            detail_note = "quality gate recovery call failed"
    return parsed, detail_note, input_tokens, output_tokens, token_cost_usd


async def _recover_display_name(
    parsed: GeneralInfoExtractionOutput,
    detail_note: str | None,
    *,
    fallback: Callable[..., Awaitable[Any]],
    augment: Augment,
    school: School,
    school_name: str,
    selected_text: str,
    timeout_seconds: float,
    llm_stats: Any,
) -> tuple[GeneralInfoExtractionOutput, str | None, int, int, float]:
    """Ask the capable model for a missing display name and fold it into the answer."""
    recovered_display_name, in3, out3, cost3 = await fallback(
        school_name=school_name,
        text=selected_text,
        country_code=school.country_code,
        timeout_seconds=timeout_seconds,
        llm_stats=llm_stats,
        school_id=school.id,
    )
    if recovered_display_name:
        parsed.display_name_i18n = recovered_display_name
        parsed = augment(parsed)
        detail_note = (
            f"{detail_note}; recovered display name with capable fallback"
            if detail_note
            else "Recovered display name with capable fallback"
        )
    return parsed, detail_note, in3, out3, cost3


def _append_detail_note(detail_note: str | None, note: str | None) -> str | None:
    if note:
        return f"{detail_note}; {note}" if detail_note else note
    return detail_note


def _add_source(
    db: AsyncSession,
    school: School,
    source_url: str | None,
    field_key: str,
    value_json: Any = None,
    value_text: str | None = None,
    path_prefix: str = "attributes",
) -> None:
    if value_json is None and not value_text:
        return
    if isinstance(value_json, list) and not value_json:
        return
    db.add(
        FieldSource(
            school_id=school.id,
            category="general_info",
            field_key=f"{path_prefix}.{field_key}",
            value_json=value_json,
            value_text=value_text,
            source_type=SourceType.SCRAPED_WEBSITE,
            source_url=source_url,
            scraped_at=text_values._utcnow_naive(),
            confidence=SourceConfidence.MEDIUM,
            confidence_score=0.7,
        )
    )


async def _store_general_info(
    db: AsyncSession,
    school: School,
    pages: list[SourcePage],
    normalized: GeneralInfoExtractionOutput,
    extracted_i18n: Any,
    display_name_i18n: Any,
    display_name_evidence: Any,
    extracted: dict[str, Any],
) -> dict[str, Any]:
    """Write the extracted attributes and the admission section onto the school.

    Returns the admission payload.
    """
    attrs = prepare_validation_rollover(school.attributes)
    # UF42(b): extra campuses and stated age groups are only candidates here; validation
    # creates them when it clears the withholding marker set just above.
    from app.scrapers.campus_sync import record_candidates_at_extraction

    await record_candidates_at_extraction(db, school, pages, attrs)
    admission_info = dict(school.admission_info) if isinstance(school.admission_info, dict) else {}

    admission_payload = normalized.admission.model_dump()

    attrs["extracted"] = extracted
    # The accepted Stage 6 report remains available for audit while the replacement
    # payload is withheld. Validation promotes its successor atomically below.
    attrs.pop("operations", None)
    attrs.pop("services", None)
    attrs.pop("pricing_terms", None)

    if extracted_i18n:
        attrs["extracted_i18n"] = extracted_i18n
    else:
        attrs.pop("extracted_i18n", None)

    if display_name_i18n:
        attrs["display_name_i18n"] = display_name_i18n
        if display_name_evidence:
            attrs["display_name_evidence"] = display_name_evidence
        else:
            attrs.pop("display_name_evidence", None)
    else:
        attrs.pop("display_name_i18n", None)
        attrs.pop("display_name_evidence", None)

    school.attributes = attrs
    clear_summary_state(school)

    if normalized.admission.has_useful_info:
        admission_info["website_extracted"] = admission_payload
    else:
        admission_info.pop("website_extracted", None)
    school.admission_info = admission_info
    return admission_payload


async def _write_general_info_sources(
    db: AsyncSession,
    school: School,
    normalized: GeneralInfoExtractionOutput,
    display_name_i18n: Any,
    contact_info: Any,
    location_address_update: dict[str, Any] | None,
    admission_payload: dict[str, Any],
    source_url: str | None,
) -> None:
    """Replace the school's general-info evidence rows."""
    operations_payload = normalized.operations.model_dump()
    services_payload = normalized.services.model_dump()
    pricing_terms_payload = normalized.pricing_terms.model_dump()
    summary_source_payload = normalized.summary_source.model_dump()

    await db.execute(
        delete(FieldSource).where(
            FieldSource.school_id == school.id,
            FieldSource.source_type == SourceType.SCRAPED_WEBSITE,
            FieldSource.category == "general_info",
        )
    )

    add_source = functools.partial(_add_source, db, school, source_url)

    add_source("languages", value_json=[entry.model_dump() for entry in normalized.languages])
    if display_name_i18n:
        add_source("display_name_i18n", value_json=display_name_i18n)
    add_source("facilities", value_json=normalized.facilities)
    add_source("programs", value_json=normalized.programs)
    add_source("extracurricular", value_json=normalized.extracurricular)
    add_source("accreditations", value_json=normalized.accreditations)
    add_source("class_size", value_text=normalized.class_size)
    add_source("founded_year", value_text=normalized.founded_year)
    if normalized.admission.has_useful_info:
        add_source("admission", value_json=admission_payload)
        add_source("website_extracted", value_json=admission_payload, path_prefix="admission_info")
    if normalized.operations.has_useful_info:
        add_source("operations", value_json=operations_payload)
    if normalized.services.has_useful_info:
        add_source("services", value_json=services_payload)
    if normalized.pricing_terms.has_useful_info:
        add_source("pricing_terms", value_json=pricing_terms_payload)
    if normalized.summary_source.has_useful_info:
        add_source("summary_source", value_json=summary_source_payload)
    if contact_info:
        add_source("contact", value_json=contact_info)
        if isinstance(contact_info.get("address"), str):
            add_source("contact.address", value_text=contact_info["address"])
    if location_address_update:
        db.add(
            FieldSource(
                school_id=school.id,
                category="general_info",
                field_key="locations.primary.address_i18n.bg",
                field_path="locations.primary.address_i18n.bg",
                value_text=location_address_update["address"],
                source_type=SourceType.SCRAPED_WEBSITE,
                source_url=source_url,
                scraped_at=text_values._utcnow_naive(),
                confidence=SourceConfidence.HIGH,
                confidence_score=0.85,
                notes=(
                    f"Replaced previous location address: {location_address_update['replaced_address']}"
                    if location_address_update.get("replaced_address")
                    else "Filled missing primary location address from website contact content"
                ),
            )
        )


def _general_info_detail(
    normalized: GeneralInfoExtractionOutput,
    display_name_i18n: Any,
    contact_info: Any,
    location_address_update: Any,
    detail_note: str | None,
) -> str:
    has_any_info = any(
        [
            normalized.languages,
            display_name_i18n,
            normalized.facilities,
            normalized.programs,
            normalized.extracurricular,
            normalized.class_size,
            normalized.founded_year,
            normalized.accreditations,
            normalized.admission.has_useful_info,
            normalized.operations.has_useful_info,
            normalized.services.has_useful_info,
            normalized.pricing_terms.has_useful_info,
            normalized.summary_source.has_useful_info,
            contact_info,
            location_address_update,
        ]
    )

    detail = "General info extracted" if has_any_info else "No useful general info detected"
    if detail_note and has_any_info:
        detail = f"{detail} ({detail_note})"
    if location_address_update:
        detail = f"{detail}; repaired primary location address from website contact page"
    return detail
