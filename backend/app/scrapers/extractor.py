"""Stage 5 extraction: PydanticAI + OpenRouter-first implementation."""

from __future__ import annotations

import asyncio
import datetime
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from pydantic_ai import Agent, ModelRetry
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.providers.openrouter import OpenRouterProvider
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import get_model
from app.config import get_settings
from app.models.field_source import FieldSource, SourceConfidence, SourceType
from app.models.pricing import PriceSource, Pricing
from app.models.school import School, SchoolLocation
from app.models.scrape_log import ScrapeType
from app.models.source_page import SourcePage
from app.schemas.extraction import (
    AdmissionExtractionOutput,
    GeneralInfoExtractionOutput,
    OperationsExtractionOutput,
    PriceExtractionOutput,
    PricingTermsExtractionOutput,
    ServicesExtractionOutput,
)
from . import extractor_helpers as helpers

logger = logging.getLogger(__name__)

GENERAL_INFO_HINT_TOKENS: tuple[str, ...] = (
    "program",
    "programs",
    "curriculum",
    "method",
    "montessori",
    "waldorf",
    "ib",
    "cambridge",
    "stem",
    "club",
    "extracurricular",
    "activities",
    "activity",
    "facility",
    "facilities",
    "campus",
    "library",
    "lab",
    "laboratory",
    "sport",
    "pool",
    "клуб",
    "програма",
    "програми",
    "извънклас",
    "занимания",
    "база",
    "библиотека",
    "лаборатория",
)


@dataclass
class ExtractionLLMStats:
    """Lightweight telemetry for extraction calls."""

    total_calls: int = 0
    typed_validation_failures: int = 0
    relaxed_parse_attempts: int = 0
    relaxed_parse_successes: int = 0
    provider_failures: int = 0
    model_retries: int = 0
    hard_failures: int = 0
    capable_fallback_attempts: int = 0
    capable_fallback_successes: int = 0
    hard_failure_details: list[dict[str, Any]] = field(default_factory=list)

    def add_failure(self, exc: Exception, stage: str) -> None:
        if len(self.hard_failure_details) >= 50:
            return
        self.hard_failure_details.append(
            {
                "stage": stage,
                "exception_type": exc.__class__.__name__,
                "exception_message": str(exc),
            }
        )

    def as_dict(self) -> dict[str, Any]:
        hard_failure_rate = (
            float(self.hard_failures) / float(self.total_calls) if self.total_calls else 0.0
        )
        return {
            "total_calls": self.total_calls,
            "typed_validation_failures": self.typed_validation_failures,
            "relaxed_parse_attempts": self.relaxed_parse_attempts,
            "relaxed_parse_successes": self.relaxed_parse_successes,
            "provider_failures": self.provider_failures,
            "model_retries": self.model_retries,
            "hard_failures": self.hard_failures,
            "capable_fallback_attempts": self.capable_fallback_attempts,
            "capable_fallback_successes": self.capable_fallback_successes,
            "hard_failure_rate": round(hard_failure_rate, 4),
            "hard_failure_details": self.hard_failure_details,
        }


def _build_openrouter_model() -> OpenRouterModel:
    settings = get_settings()
    model_name = (get_model("cheap") or "openrouter/google/gemini-2.5-flash-lite").replace("openrouter/", "")
    provider = OpenRouterProvider(api_key=settings.openrouter_api_key)
    return OpenRouterModel(model_name=model_name, provider=provider)


def _csv_items(raw: str) -> list[str]:
    return [item.strip() for item in (raw or "").split(",") if item.strip()]


def _normalize_openrouter_model_name(raw: str) -> str:
    value = (raw or "").strip()
    return value.replace("openrouter/", "", 1) if value.startswith("openrouter/") else value


def _build_openrouter_model_settings() -> dict[str, Any]:
    settings = get_settings()
    model_settings: dict[str, Any] = {
        "temperature": float(settings.extraction_temperature),
        "openrouter_usage": {"include": True},
    }

    primary_model = _normalize_openrouter_model_name(
        get_model("cheap") or "openrouter/google/gemini-2.5-flash-lite"
    )
    routed_models = [
        _normalize_openrouter_model_name(model)
        for model in _csv_items(settings.extraction_openrouter_models)
    ]
    routed_models = [model for model in routed_models if model and model != primary_model]
    if routed_models:
        model_settings["openrouter_models"] = routed_models

    provider_config: dict[str, Any] = {}
    provider_order = _csv_items(settings.extraction_openrouter_provider_order)
    if provider_order:
        provider_config["order"] = provider_order
    if not bool(settings.extraction_openrouter_provider_allow_fallbacks):
        provider_config["allow_fallbacks"] = False
    provider_sort = (settings.extraction_openrouter_provider_sort or "").strip().lower()
    if provider_sort in {"price", "throughput", "latency"}:
        provider_config["sort"] = provider_sort
    if provider_config:
        model_settings["openrouter_provider"] = provider_config

    return model_settings


def _parse_agent_output(result: Any, result_type: type) -> Any | None:
    output = getattr(result, "output", None)
    if isinstance(output, result_type):
        return output
    if isinstance(output, dict):
        return result_type.model_validate(output)

    data = getattr(result, "data", None)
    if isinstance(data, result_type):
        return data
    if isinstance(data, dict):
        return result_type.model_validate(data)

    return None


def _extract_openrouter_cost_usd(result: Any) -> float:
    all_messages = []
    if callable(getattr(result, "all_messages", None)):
        try:
            all_messages = result.all_messages()
        except Exception:
            all_messages = []

    total_cost = 0.0
    for message in all_messages:
        provider_details = getattr(message, "provider_details", None)
        if not isinstance(provider_details, dict):
            continue
        raw_cost = provider_details.get("cost")
        if raw_cost is None:
            continue
        try:
            total_cost += float(raw_cost)
        except (TypeError, ValueError):
            continue

    if total_cost > 0:
        return total_cost

    response = getattr(result, "response", None)
    response_details = getattr(response, "provider_details", None) if response is not None else None
    if isinstance(response_details, dict):
        raw_cost = response_details.get("cost")
        try:
            return float(raw_cost or 0.0)
        except (TypeError, ValueError):
            return 0.0
    return 0.0


def _normalize_address_for_compare(value: str | None) -> str:
    return " ".join((value or "").casefold().replace('"', "").split())


def _address_has_street_signal(value: str | None) -> bool:
    return any(
        marker in (value or "").casefold()
        for marker in ("ул.", "бул.", "ж.к.", "жк.", "кв.", "пл.", "street", "st.", "boulevard", "blvd")
    )


def _address_has_street_number(value: str | None) -> bool:
    if not value:
        return False
    match = re.search(
        r"(?:ул\.|бул\.|street|st\.|boulevard|blvd|road|rd\.|avenue|ave\.)"
        r"([^,\n]{0,80})",
        value,
        flags=re.IGNORECASE,
    )
    if not match:
        return False

    street_segment = match.group(1)
    building_match = re.search(r"(?:[№#]\s*\d+[A-Za-zА-Яа-я]?|\b\d+[A-Za-zА-Яа-я]?\b)", street_segment)
    if not building_match:
        return False

    prefix = street_segment[: building_match.start()]
    if re.search(r"\b(?:ет\.?|ап\.?|апартамент|офис|office|suite|floor)\b", prefix, flags=re.IGNORECASE):
        return False

    suffix = street_segment[building_match.end() :]
    if re.search(
        r"^\s*(?:[,/-]?\s*(?:ет\.?|ап\.?|апартамент|офис|office|suite|floor)\b)",
        suffix,
        flags=re.IGNORECASE,
    ):
        return False

    return True


def _address_is_precise_enough_for_override(value: str | None) -> bool:
    lowered = (value or "").casefold()
    if not lowered:
        return False
    has_precise_marker = any(
        marker in lowered
        for marker in ("ул.", "бул.", "street", "st.", "boulevard", "blvd", "road", "rd.", "avenue", "ave.")
    )
    return has_precise_marker and _address_has_street_number(value)


def _address_looks_like_registry_office(value: str | None) -> bool:
    lowered = (value or "").casefold()
    if not lowered:
        return False
    office_markers = ("ап.", "апартамент", "офис", "office", "suite")
    floor_markers = ("ет.", "floor")
    return any(marker in lowered for marker in office_markers) or (
        any(marker in lowered for marker in floor_markers) and any(marker in lowered for marker in office_markers)
    )


def _should_replace_primary_address(
    current_address: str | None,
    website_address: str | None,
    location_count: int,
) -> bool:
    if not website_address:
        return False
    if not _address_is_precise_enough_for_override(website_address):
        return False
    if not current_address:
        return True
    if _normalize_address_for_compare(current_address) == _normalize_address_for_compare(website_address):
        return False
    if location_count > 1:
        return False
    if _address_looks_like_registry_office(current_address):
        return True
    if not _address_has_street_signal(current_address) and _address_has_street_signal(website_address):
        return True
    return False


async def _sync_primary_location_from_contact_address(
    db: AsyncSession,
    school: School,
    contact_info: dict[str, Any] | None,
) -> dict[str, Any] | None:
    website_address = helpers._normalize_contact_address_candidate((contact_info or {}).get("address") or "")
    coordinates = (contact_info or {}).get("coordinates")
    coord_lat = coordinates.get("lat") if isinstance(coordinates, dict) else None
    coord_lng = coordinates.get("lng") if isinstance(coordinates, dict) else None
    if not website_address:
        return None

    locations = (
        await db.execute(
            select(SchoolLocation)
            .where(SchoolLocation.school_id == school.id)
            .order_by(SchoolLocation.is_primary.desc(), SchoolLocation.id.asc())
        )
    ).scalars().all()
    if not locations:
        return None

    primary_location = locations[0]
    address_i18n = dict(primary_location.address_i18n or {})
    current_bg = helpers._normalize_contact_address_candidate(address_i18n.get("bg") or "")
    tags = [tag for tag in list(primary_location.location_tags or []) if not str(tag).startswith("coords_source=")]
    same_address = _normalize_address_for_compare(current_bg) == _normalize_address_for_compare(website_address)

    if same_address and coord_lat is not None and coord_lng is not None:
        primary_location.lat = float(coord_lat)
        primary_location.lng = float(coord_lng)
        if "coords_source=website_map_link" not in tags:
            tags.append("coords_source=website_map_link")
        if "address_source=website_contact" not in tags:
            tags.append("address_source=website_contact")
        primary_location.location_tags = tags
        db.add(primary_location)
        return {
            "location_id": primary_location.id,
            "address": website_address,
            "replaced_address": None,
        }

    if not _should_replace_primary_address(current_bg, website_address, len(locations)):
        return None

    address_i18n["bg"] = website_address
    address_i18n.pop("en", None)
    primary_location.address_i18n = address_i18n
    if coord_lat is not None and coord_lng is not None:
        primary_location.lat = float(coord_lat)
        primary_location.lng = float(coord_lng)
        if "coords_source=website_map_link" not in tags:
            tags.append("coords_source=website_map_link")
    elif current_bg and _normalize_address_for_compare(current_bg) != _normalize_address_for_compare(website_address):
        primary_location.lat = None
        primary_location.lng = None
    if "address_source=website_contact" not in tags:
        tags.append("address_source=website_contact")
    primary_location.location_tags = tags
    db.add(primary_location)

    return {
        "location_id": primary_location.id,
        "address": website_address,
        "replaced_address": current_bg,
    }


async def _run_typed_agent(
    *,
    system_prompt: str,
    user_prompt: str,
    result_type: type,
    timeout_seconds: float,
    llm_stats: ExtractionLLMStats,
) -> tuple[Any | None, int, int, float]:
    settings = get_settings()
    retries_state = {"count": 0}

    model = _build_openrouter_model()
    agent = Agent(
        model=model,
        system_prompt=system_prompt,
        output_type=result_type,
        retries=1,
        output_retries=max(0, int(settings.extraction_output_retries)),
        model_settings=_build_openrouter_model_settings(),
    )

    @agent.output_validator
    def _semantic_output_validator(data: Any) -> Any:
        if isinstance(data, PriceExtractionOutput):
            if data.has_pricing_info and not data.prices:
                retries_state["count"] += 1
                raise ModelRetry("has_pricing_info=true requires at least one pricing row")
        if isinstance(data, GeneralInfoExtractionOutput):
            has_payload = any(
                [
                    data.display_name_i18n,
                    data.languages,
                    data.facilities,
                    data.programs,
                    data.extracurricular,
                    data.class_size,
                    data.founded_year,
                    data.accreditations,
                    data.admission.has_useful_info,
                    data.operations.has_useful_info,
                    data.services.has_useful_info,
                    data.pricing_terms.has_useful_info,
                ]
            )
            if data.has_useful_info and not has_payload:
                retries_state["count"] += 1
                raise ModelRetry("has_useful_info=true requires at least one populated field")
        return data

    llm_stats.total_calls += 1
    try:
        result = await asyncio.wait_for(agent.run(user_prompt), timeout=timeout_seconds)
        llm_stats.model_retries += retries_state["count"]
        parsed = _parse_agent_output(result, result_type)
        input_tokens, output_tokens = helpers._get_usage(result)
        token_cost_usd = _extract_openrouter_cost_usd(result)
        return parsed, input_tokens, output_tokens, token_cost_usd
    except Exception as exc:
        if helpers._is_output_validation_error(exc):
            llm_stats.typed_validation_failures += 1
        if helpers._is_model_or_provider_error(exc):
            llm_stats.provider_failures += 1
        llm_stats.hard_failures += 1
        llm_stats.add_failure(exc, stage=result_type.__name__)
        logger.warning("Extraction call failed (%s): %s", result_type.__name__, exc)
        return None, 0, 0, 0.0


async def _extract_prices(
    db: AsyncSession,
    school: School,
    pages: list[SourcePage],
    timeout_seconds: float,
    llm_stats: ExtractionLLMStats,
) -> dict[str, Any]:
    settings = get_settings()
    selected_text, source_urls = helpers._select_pages(
        school=school,
        pages=pages,
        preferred_categories=["pricing", "admission", "contact"],
        use_case="pricing",
    )
    if not selected_text:
        return {
            "success": False,
            "count": 0,
            "detail": "No pricing-related content found",
            "input_tokens": 0,
            "output_tokens": 0,
        }

    school_name = (school.name_i18n or {}).get("bg") or (school.name_i18n or {}).get("en") or ""
    system_prompt = (
        "Extract school pricing into structured output. "
        "Use category values that map to tuition/food/transport/activities/registration/materials/extended_day/uniforms/extracurricular/camp. "
        "Use period values that map to monthly/yearly/one_time/quarter/term/semester. "
        "If no concrete pricing exists, return has_pricing_info=false and prices=[]."
    )
    user_prompt = f"School: {school_name}\n\nContent:\n{selected_text}"

    parsed, input_tokens, output_tokens, token_cost_usd = await _run_typed_agent(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        result_type=PriceExtractionOutput,
        timeout_seconds=timeout_seconds,
        llm_stats=llm_stats,
    )

    if parsed is None:
        return {
            "success": False,
            "count": 0,
            "detail": "Price extraction LLM call failed",
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "token_cost_usd": token_cost_usd,
        }

    if not parsed.has_pricing_info:
        if settings.extraction_clear_pricing_on_no_info:
            await db.execute(
                delete(Pricing).where(
                    Pricing.school_id == school.id,
                    Pricing.source == PriceSource.SCRAPED_WEBSITE,
                )
            )
            await db.execute(
                delete(FieldSource).where(
                    FieldSource.school_id == school.id,
                    FieldSource.source_type == SourceType.SCRAPED_WEBSITE,
                    FieldSource.category == "pricing",
                )
            )
            return {
                "success": True,
                "count": 0,
                "detail": "No pricing info detected (cleared existing scraped pricing)",
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "token_cost_usd": token_cost_usd,
            }

        return {
            "success": True,
            "count": 0,
            "detail": "No pricing info detected",
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "token_cost_usd": token_cost_usd,
        }

    source_url = source_urls[0] if source_urls else None
    pricing_rows: list[Pricing] = []
    source_rows: list[FieldSource] = []

    for extracted in parsed.prices:
        category = helpers._coerce_price_category(extracted.category)
        period = helpers._coerce_price_period(extracted.period)
        amount = helpers._to_optional_float(extracted.amount)
        amount_min = helpers._to_optional_float(extracted.amount_min)
        amount_max = helpers._to_optional_float(extracted.amount_max)

        if category is None or period is None:
            continue
        if amount is None and amount_min is None and amount_max is None:
            continue

        normalized_discounts = helpers._normalize_text_list(extracted.discounts)
        normalized_installments = helpers._normalize_text_list(extracted.installments)
        normalized_includes = helpers._normalize_text_list(extracted.includes)
        normalized_excludes = helpers._normalize_text_list(extracted.excludes)
        normalized_plan_name = helpers._normalize_scalar_text(extracted.plan_name, max_len=100)
        normalized_academic_year = helpers._normalize_scalar_text(extracted.academic_year, max_len=20)
        normalized_age_group = helpers._normalize_scalar_text(extracted.age_group, max_len=50)

        pricing_rows.append(
            Pricing(
                school_id=school.id,
                category=category,
                amount=amount,
                amount_min=amount_min,
                amount_max=amount_max,
                currency=(extracted.currency or "BGN")[:3].upper(),
                period=period,
                plan_name=normalized_plan_name,
                academic_year=normalized_academic_year,
                age_group=normalized_age_group,
                source=PriceSource.SCRAPED_WEBSITE,
                source_url=source_url,
                pricing_context={
                    "notes": extracted.notes,
                    "confidence": extracted.confidence,
                    "discounts": normalized_discounts,
                    "installments": normalized_installments,
                    "includes": normalized_includes,
                    "excludes": normalized_excludes,
                },
            )
        )

        source_rows.append(
            FieldSource(
                school_id=school.id,
                category="pricing",
                field_key=f"pricing.{category.value}.{period.value}",
                value_text=helpers._format_price_value_text(
                    amount,
                    amount_min,
                    amount_max,
                    extracted.currency,
                ),
                source_type=SourceType.SCRAPED_WEBSITE,
                source_url=source_url,
                scraped_at=helpers._utcnow_naive(),
                confidence=SourceConfidence.HIGH if (extracted.confidence or 0) >= 0.8 else SourceConfidence.MEDIUM,
                confidence_score=extracted.confidence,
            )
        )

    if not pricing_rows:
        return {
            "success": True,
            "count": 0,
            "detail": "Pricing info detected but no valid rows after normalization",
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "token_cost_usd": token_cost_usd,
        }

    await db.execute(
        delete(Pricing).where(
            Pricing.school_id == school.id,
            Pricing.source == PriceSource.SCRAPED_WEBSITE,
        )
    )
    await db.execute(
        delete(FieldSource).where(
            FieldSource.school_id == school.id,
            FieldSource.source_type == SourceType.SCRAPED_WEBSITE,
            FieldSource.category == "pricing",
        )
    )

    for row in pricing_rows:
        db.add(row)
    for row in source_rows:
        db.add(row)

    return {
        "success": True,
        "count": len(pricing_rows),
        "detail": f"Extracted {len(pricing_rows)} pricing rows",
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "token_cost_usd": token_cost_usd,
    }


async def _extract_general_info(
    db: AsyncSession,
    school: School,
    pages: list[SourcePage],
    timeout_seconds: float,
    llm_stats: ExtractionLLMStats,
) -> dict[str, Any]:
    settings = get_settings()
    all_page_text = "\n".join((p.raw_markdown or "") for p in pages)
    selected_text, source_urls = helpers._select_pages(
        school=school,
        pages=pages,
        preferred_categories=["about", "contact", "programs", "facilities", "admission", "pricing"],
        use_case="general_info",
        include_tokens=GENERAL_INFO_HINT_TOKENS,
    )
    if not selected_text:
        return {
            "success": False,
            "detail": "No general-info content found",
            "input_tokens": 0,
            "output_tokens": 0,
        }

    school_name = (school.name_i18n or {}).get("bg") or (school.name_i18n or {}).get("en") or ""
    system_prompt = (
        "Extract general school information into structured output. "
        "If the website shows a public-facing school or brand name that differs from the registry name, "
        "capture it in display_name_i18n using explicit website language variants only. "
        "If the same brand text is used in multiple languages, you may repeat the exact same text in both variants. "
        "Do not copy the registry/legal name into display_name_i18n unless the website itself shows it as the display name. "
        "Populate languages, facilities, programs, extracurricular, class_size, founded_year, accreditations, "
        "and nested admission/operations/services/pricing_terms sections. "
        "Return only concrete facts explicitly supported by content; do not invent. "
        "For languages, class_size, and founded_year: include values only when explicitly stated in the content. "
        "If not explicit, return languages=[] and class_size/founded_year as null. "
        "When content explicitly mentions facilities, programs, or extracurriculars, include them as short list items "
        "instead of leaving those arrays empty. "
        "Set has_useful_info=true whenever at least one concrete fact is extracted."
    )
    known_aliases = [str(value) for value in dict(school.attributes or {}).get("name_aliases", []) if str(value or "").strip()]

    user_prompt = f"Registry name: {school_name}\n\nWebsite content:\n{selected_text}"

    parsed, input_tokens, output_tokens, token_cost_usd = await _run_typed_agent(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        result_type=GeneralInfoExtractionOutput,
        timeout_seconds=timeout_seconds,
        llm_stats=llm_stats,
    )
    detail_note: str | None = None
    if parsed is None:
        parsed = _build_deterministic_general_info_output(
            all_page_text,
            registry_name=school_name,
            country_code=school.country_code,
            website_url=school.website_url,
            known_aliases=known_aliases,
        )
        detail_note = "General-info extraction LLM call failed; used deterministic fallback"
    else:
        parsed = _augment_general_info_with_deterministic(
            parsed,
            all_page_text,
            registry_name=school_name,
            country_code=school.country_code,
            website_url=school.website_url,
            known_aliases=known_aliases,
        )
        min_quality_score = max(0, int(settings.extraction_general_info_min_quality_score))
        current_quality = helpers._score_general_info_output(parsed)
        if current_quality < min_quality_score:
            recovery_prompt = (
                "Extract additional concrete school facts that are explicitly present. "
                "Prioritize filling missing display_name_i18n, languages, facilities, programs, extracurricular, "
                "class size, founded year, accreditations, admission, operations, services, and pricing terms. "
                "Do not invent values. "
                "Only populate display_name_i18n when the website explicitly shows a public-facing name. "
                "For languages, class_size, and founded_year: only include them when explicitly stated; otherwise "
                "leave them empty/null."
            )
            max_chars = max(2000, int(settings.extraction_max_content_chars))
            recovery_user_prompt = f"School: {school_name}\n\nContent:\n{all_page_text[:max_chars]}"
            recovered, in2, out2, cost2 = await _run_typed_agent(
                system_prompt=recovery_prompt,
                user_prompt=recovery_user_prompt,
                result_type=GeneralInfoExtractionOutput,
                timeout_seconds=timeout_seconds,
                llm_stats=llm_stats,
            )
            input_tokens += in2
            output_tokens += out2
            token_cost_usd += cost2
            if recovered is not None:
                recovered_augmented = _augment_general_info_with_deterministic(
                    recovered,
                    all_page_text,
                    registry_name=school_name,
                    country_code=school.country_code,
                    website_url=school.website_url,
                    known_aliases=known_aliases,
                )
                recovered_quality = helpers._score_general_info_output(recovered_augmented)
                if recovered_quality >= current_quality:
                    parsed = recovered_augmented
                    detail_note = (
                        f"quality gate recovery improved/kept quality ({current_quality}->{recovered_quality})"
                    )
            elif detail_note is None:
                detail_note = "quality gate recovery call failed"

    normalized, extracted_i18n, display_name_i18n = helpers._normalize_general_info_output(parsed, school.country_code)
    contact_info = helpers._extract_contact_info_deterministic(all_page_text)
    location_address_update = await _sync_primary_location_from_contact_address(db, school, contact_info)

    attrs = dict(school.attributes) if isinstance(school.attributes, dict) else {}
    admission_info = dict(school.admission_info) if isinstance(school.admission_info, dict) else {}

    admission_payload = normalized.admission.model_dump()
    operations_payload = normalized.operations.model_dump()
    services_payload = normalized.services.model_dump()
    pricing_terms_payload = normalized.pricing_terms.model_dump()

    extracted: dict[str, Any] = {
        "_schema_version": 1,
        "languages": [entry.model_dump() for entry in normalized.languages],
        "facilities": normalized.facilities,
        "programs": normalized.programs,
        "extracurricular": normalized.extracurricular,
        "class_size": normalized.class_size,
        "founded_year": normalized.founded_year,
        "accreditations": normalized.accreditations,
        "admission": admission_payload,
        "operations": operations_payload,
        "services": services_payload,
        "pricing_terms": pricing_terms_payload,
    }
    if contact_info:
        extracted["contact"] = contact_info

    attrs["extracted"] = extracted
    # Invalidate previous Stage 6 report because extracted payload just changed.
    attrs.pop("data_validation", None)
    attrs.pop("operations", None)
    attrs.pop("services", None)
    attrs.pop("pricing_terms", None)

    if extracted_i18n:
        attrs["extracted_i18n"] = extracted_i18n
    else:
        attrs.pop("extracted_i18n", None)

    if display_name_i18n:
        attrs["display_name_i18n"] = display_name_i18n

    school.attributes = attrs

    if normalized.admission.has_useful_info:
        admission_info["website_extracted"] = admission_payload
    else:
        admission_info.pop("website_extracted", None)
    school.admission_info = admission_info

    await db.execute(
        delete(FieldSource).where(
            FieldSource.school_id == school.id,
            FieldSource.source_type == SourceType.SCRAPED_WEBSITE,
            FieldSource.category == "general_info",
        )
    )

    source_url = source_urls[0] if source_urls else None

    def add_source(
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
                scraped_at=helpers._utcnow_naive(),
                confidence=SourceConfidence.MEDIUM,
                confidence_score=0.7,
            )
        )

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
                scraped_at=helpers._utcnow_naive(),
                confidence=SourceConfidence.HIGH,
                confidence_score=0.85,
                notes=(
                    f"Replaced previous location address: {location_address_update['replaced_address']}"
                    if location_address_update.get("replaced_address")
                    else "Filled missing primary location address from website contact content"
                ),
            )
        )

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
            contact_info,
            location_address_update,
        ]
    )

    detail = "General info extracted" if has_any_info else "No useful general info detected"
    if detail_note and has_any_info:
        detail = f"{detail} ({detail_note})"
    if location_address_update:
        detail = f"{detail}; repaired primary location address from website contact page"

    return {
        "success": True,
        "detail": detail,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "token_cost_usd": token_cost_usd,
    }


def _merge_admission_info(
    llm: AdmissionExtractionOutput,
    deterministic: AdmissionExtractionOutput,
) -> AdmissionExtractionOutput:
    merged = AdmissionExtractionOutput(
        deadlines=helpers._merge_text_values(llm.deadlines, deterministic.deadlines),
        required_documents=helpers._merge_text_values(
            llm.required_documents, deterministic.required_documents
        ),
        application_steps=helpers._merge_text_values(
            llm.application_steps, deterministic.application_steps
        ),
        entrance_requirements=helpers._merge_text_values(
            llm.entrance_requirements, deterministic.entrance_requirements
        ),
        available_spots=helpers._merge_text_values(llm.available_spots, deterministic.available_spots),
        has_useful_info=False,
    )
    merged.has_useful_info = any(
        (
            merged.deadlines,
            merged.required_documents,
            merged.application_steps,
            merged.entrance_requirements,
            merged.available_spots,
        )
    )
    return merged


def _merge_operations_info(
    llm: OperationsExtractionOutput,
    deterministic: OperationsExtractionOutput,
) -> OperationsExtractionOutput:
    merged = OperationsExtractionOutput(
        working_hours=llm.working_hours or deterministic.working_hours,
        day_options=helpers._merge_text_values(llm.day_options, deterministic.day_options),
        daily_schedule=helpers._merge_text_values(llm.daily_schedule, deterministic.daily_schedule),
        meals=helpers._merge_text_values(llm.meals, deterministic.meals),
        transport=helpers._merge_text_values(llm.transport, deterministic.transport),
        uniforms=helpers._merge_text_values(llm.uniforms, deterministic.uniforms),
        has_useful_info=False,
    )
    merged.has_useful_info = any(
        (
            merged.working_hours,
            merged.day_options,
            merged.daily_schedule,
            merged.meals,
            merged.transport,
            merged.uniforms,
        )
    )
    return merged


def _merge_services_info(
    llm: ServicesExtractionOutput,
    deterministic: ServicesExtractionOutput,
) -> ServicesExtractionOutput:
    merged = ServicesExtractionOutput(
        support_services=helpers._merge_text_values(
            llm.support_services, deterministic.support_services
        ),
        safety_features=helpers._merge_text_values(llm.safety_features, deterministic.safety_features),
        has_useful_info=False,
    )
    merged.has_useful_info = any((merged.support_services, merged.safety_features))
    return merged


def _merge_pricing_terms_info(
    llm: PricingTermsExtractionOutput,
    deterministic: PricingTermsExtractionOutput,
) -> PricingTermsExtractionOutput:
    merged = PricingTermsExtractionOutput(
        discounts=helpers._merge_text_values(llm.discounts, deterministic.discounts),
        installments=helpers._merge_text_values(llm.installments, deterministic.installments),
        included_items=helpers._merge_text_values(
            llm.included_items, deterministic.included_items
        ),
        excluded_items=helpers._merge_text_values(
            llm.excluded_items, deterministic.excluded_items
        ),
        deposits=helpers._merge_text_values(llm.deposits, deterministic.deposits),
        application_fees=helpers._merge_text_values(
            llm.application_fees, deterministic.application_fees
        ),
        registration_fees=helpers._merge_text_values(
            llm.registration_fees, deterministic.registration_fees
        ),
        has_useful_info=False,
    )
    merged.has_useful_info = any(
        (
            merged.discounts,
            merged.installments,
            merged.included_items,
            merged.excluded_items,
            merged.deposits,
            merged.application_fees,
            merged.registration_fees,
        )
    )
    return merged


def _augment_general_info_with_deterministic(
    llm_output: GeneralInfoExtractionOutput,
    all_page_text: str,
    registry_name: str | None,
    country_code: str,
    website_url: str | None,
    known_aliases: list[str] | None,
) -> GeneralInfoExtractionOutput:
    deterministic_display_name = helpers._extract_display_name_i18n_deterministic(
        all_page_text,
        registry_name=registry_name,
        country_code=country_code,
        website_url=website_url,
        known_aliases=known_aliases,
    )
    deterministic_languages = helpers._extract_languages_deterministic(all_page_text)
    deterministic_founded_year = helpers._extract_founded_year_deterministic(all_page_text)
    deterministic_class_size = helpers._extract_class_size_deterministic(all_page_text)
    deterministic_accreditations = helpers._extract_accreditations_deterministic(all_page_text)
    deterministic_admission = helpers._extract_admission_info_deterministic(all_page_text)
    deterministic_operations = helpers._extract_operations_info_deterministic(all_page_text)
    deterministic_services = helpers._extract_services_info_deterministic(all_page_text)
    deterministic_pricing_terms = helpers._extract_pricing_terms_deterministic(all_page_text)

    merged = GeneralInfoExtractionOutput(
        display_name_i18n=helpers._merge_display_name_i18n(
            llm_output.display_name_i18n,
            deterministic_display_name,
            country_code,
        )
        or {},
        languages=helpers._merge_language_candidates(llm_output.languages, deterministic_languages),
        facilities=llm_output.facilities,
        programs=llm_output.programs,
        extracurricular=llm_output.extracurricular,
        class_size=helpers._merge_class_size(llm_output.class_size, deterministic_class_size),
        founded_year=helpers._merge_founded_year(llm_output.founded_year, deterministic_founded_year),
        accreditations=helpers._merge_text_values(
            llm_output.accreditations, deterministic_accreditations
        ),
        admission=_merge_admission_info(llm_output.admission, deterministic_admission),
        operations=_merge_operations_info(llm_output.operations, deterministic_operations),
        services=_merge_services_info(llm_output.services, deterministic_services),
        pricing_terms=_merge_pricing_terms_info(llm_output.pricing_terms, deterministic_pricing_terms),
        has_useful_info=False,
    )
    merged.has_useful_info = helpers._score_general_info_output(merged) > 0
    return merged


def _build_deterministic_general_info_output(
    all_page_text: str,
    registry_name: str | None,
    country_code: str,
    website_url: str | None,
    known_aliases: list[str] | None,
) -> GeneralInfoExtractionOutput:
    return _augment_general_info_with_deterministic(
        GeneralInfoExtractionOutput(),
        all_page_text,
        registry_name=registry_name,
        country_code=country_code,
        website_url=website_url,
        known_aliases=known_aliases,
    )


async def extract_school(
    db: AsyncSession,
    school_id: int,
    country_code: str,
) -> dict[str, Any]:
    """Extract pricing + general information for one school (canonical path)."""
    settings = get_settings()

    school_result = await db.execute(select(School).where(School.id == school_id))
    school = school_result.scalar_one_or_none()
    if not school:
        return {
            "school_id": school_id,
            "status": "extraction_failed",
            "error": "School not found",
            "llm_stats": ExtractionLLMStats().as_dict(),
        }

    pages_result = await db.execute(
        select(SourcePage).where(
            SourcePage.school_id == school_id,
            SourcePage.scrape_type == ScrapeType.WEBSITE,
            SourcePage.is_valid.is_(True),
            SourcePage.raw_markdown.isnot(None),
        )
    )
    pages = pages_result.scalars().all()
    content_pages = [page for page in pages if (page.raw_markdown or "").strip()]

    if not content_pages:
        school.scrape_status = "extraction_failed"
        await db.commit()
        return {
            "school_id": school_id,
            "status": "extraction_failed",
            "error": "No non-empty navigated source page content",
            "llm_stats": ExtractionLLMStats().as_dict(),
        }

    llm_stats = ExtractionLLMStats()
    stats: dict[str, Any] = {
        "school_id": school_id,
        "status": "extraction_failed",
        "pricing_count": 0,
        "pricing_success": False,
        "general_info_success": False,
        "details": [],
        "input_tokens": 0,
        "output_tokens": 0,
        "token_cost_usd": 0.0,
        "skipped": False,
        "llm_stats": llm_stats.as_dict(),
    }

    should_extract_prices = school.school_type in {"private", "international"}

    if should_extract_prices:
        price_result = await _extract_prices(
            db=db,
            school=school,
            pages=content_pages,
            timeout_seconds=settings.extraction_llm_timeout_seconds,
            llm_stats=llm_stats,
        )
        stats["pricing_count"] = price_result["count"]
        stats["pricing_success"] = price_result["success"]
        stats["input_tokens"] += price_result["input_tokens"]
        stats["output_tokens"] += price_result["output_tokens"]
        stats["token_cost_usd"] += float(price_result.get("token_cost_usd", 0.0) or 0.0)
        if price_result.get("detail"):
            stats["details"].append(price_result["detail"])

    general_result = await _extract_general_info(
        db=db,
        school=school,
        pages=content_pages,
        timeout_seconds=settings.extraction_llm_timeout_seconds,
        llm_stats=llm_stats,
    )
    stats["general_info_success"] = general_result["success"]
    stats["input_tokens"] += general_result["input_tokens"]
    stats["output_tokens"] += general_result["output_tokens"]
    stats["token_cost_usd"] += float(general_result.get("token_cost_usd", 0.0) or 0.0)
    if general_result.get("detail"):
        stats["details"].append(general_result["detail"])

    if stats["pricing_success"] or stats["general_info_success"]:
        school.scrape_status = "extracted"
        stats["status"] = "extracted"
    else:
        school.scrape_status = "extraction_failed"

    school.updated_at = datetime.datetime.now(datetime.timezone.utc)
    db.add(school)
    await db.commit()

    stats["token_cost_usd"] = round(float(stats["token_cost_usd"] or 0.0), 6)
    stats["llm_stats"] = llm_stats.as_dict()
    return stats
