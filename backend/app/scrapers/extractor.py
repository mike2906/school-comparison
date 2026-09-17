"""Stage 5 extraction: PydanticAI + OpenRouter-first implementation."""

from __future__ import annotations

import asyncio
import datetime
import logging
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import tldextract
from pydantic import BaseModel, Field
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
    ExtractedPrice,
    GeneralInfoExtractionOutput,
    OperationsExtractionOutput,
    PriceExtractionOutput,
    PricingTermsExtractionOutput,
    ServicesExtractionOutput,
    SummarySourceExtractionOutput,
)
from app.scrapers.summarizer import clear_summary_state
from app.scrapers.validator import validate_school_data
from app.services.geocoding.base import GeocodingResult
from app.services.geocoding.write_gate import apply_geocode_result_to_location
from app.utils.website_data import WEBSITE_DATA_WITHHELD_KEY, prepare_validation_rollover
from app.services.provider_costs import execute_billable_request
from . import extractor_helpers as helpers

logger = logging.getLogger(__name__)

# Use tldextract's bundled PSL snapshot only: resolver runs must never refresh
# network data. Private suffixes separate school-owned subdomains from common
# hosting platforms; the extras cover platforms used by this corpus that are
# not currently declared in the PSL private section.
_HOST_SUFFIX_EXTRACTOR = tldextract.TLDExtract(
    cache_dir=None,
    suffix_list_urls=(),
    include_psl_private_domains=True,
    extra_suffixes=(
        "idwebbg.com",
        "sites.google.com",
        "weebly.com",
        "webnode.page",
        "wordpress.com",
    ),
)

GENERAL_INFO_HINT_TOKENS: tuple[str, ...] = (
    "program",
    "programs",
    "curriculum",
    "method",
    "approach",
    "philosophy",
    "mission",
    "values",
    "community",
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
    "подход",
    "философ",
    "мисия",
    "ценности",
    "общност",
    "извънклас",
    "занимания",
    "база",
    "библиотека",
    "лаборатория",
)

# Preferred `_select_pages` category orderings. Kept as module constants (not inline
# literals) so the deterministic path in `deterministic.py` imports the same lists and
# page selection can never silently diverge between production and the golden corpus.
PRICING_PAGE_CATEGORIES: list[str] = ["pricing", "admission", "contact"]
GENERAL_INFO_PAGE_CATEGORIES: list[str] = [
    "about",
    "contact",
    "programs",
    "facilities",
    "admission",
    "pricing",
]
SUMMARY_SOURCE_PAGE_CATEGORIES: list[str] = ["about", "programs", "facilities"]


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


class DisplayNameOnlyExtractionOutput(BaseModel):
    """Minimal schema for a targeted display-name recovery pass."""

    display_name_i18n: dict[str, str] = Field(default_factory=dict)


def _build_openrouter_model(tier: str = "cheap") -> OpenRouterModel:
    settings = get_settings()
    model_name = (get_model(tier) or "openrouter/google/gemini-2.5-flash-lite").replace("openrouter/", "")
    provider = OpenRouterProvider(api_key=settings.openrouter_api_key)
    return OpenRouterModel(model_name=model_name, provider=provider)


def _csv_items(raw: str) -> list[str]:
    return [item.strip() for item in (raw or "").split(",") if item.strip()]


def _normalize_openrouter_model_name(raw: str) -> str:
    value = (raw or "").strip()
    return value.replace("openrouter/", "", 1) if value.startswith("openrouter/") else value


def _build_openrouter_model_settings(tier: str = "cheap") -> dict[str, Any]:
    settings = get_settings()
    model_settings: dict[str, Any] = {
        "temperature": float(settings.extraction_temperature),
        "openrouter_usage": {"include": True},
    }

    primary_model = _normalize_openrouter_model_name(
        get_model(tier) or "openrouter/google/gemini-2.5-flash-lite"
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
    from app.ai.client import extract_provider_cost_usd

    return extract_provider_cost_usd(result)


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
    existing_tags = list(primary_location.location_tags or [])
    coord_tags = [tag for tag in existing_tags if str(tag).startswith("coords_source=")]
    tags = [tag for tag in existing_tags if not str(tag).startswith("coords_source=")]
    same_address = _normalize_address_for_compare(current_bg) == _normalize_address_for_compare(website_address)

    async def apply_website_coordinates() -> bool:
        if coord_lat is None or coord_lng is None:
            return False
        applied = await apply_geocode_result_to_location(
            db,
            primary_location,
            GeocodingResult(
                success=True,
                lat=float(coord_lat),
                lng=float(coord_lng),
                provider="website",
                formatted_address=website_address,
                method="website_map_link",
                precision="exact",
            ),
            school=school,
        )
        return applied.success

    if same_address and coord_lat is not None and coord_lng is not None:
        coordinates_accepted = await apply_website_coordinates()
        if coordinates_accepted and "coords_source=website_map_link" not in tags:
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
        coordinates_accepted = await apply_website_coordinates()
        if coordinates_accepted and "coords_source=website_map_link" not in tags:
            tags.append("coords_source=website_map_link")
    elif current_bg and _normalize_address_for_compare(current_bg) != _normalize_address_for_compare(website_address):
        # Keep prior coordinates until we have a better replacement source.
        # Otherwise a later contact-page extract can make the school disappear
        # from map/search results even when the previous point was valid.
        tags.extend(tag for tag in coord_tags if tag not in tags)
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
    preferred_tier: str = "cheap",
    school_id: int | None = None,
) -> tuple[Any | None, int, int, float]:
    settings = get_settings()

    async def _run_for_tier(tier: str) -> tuple[Any | None, int, int, float]:
        retries_state = {"count": 0}
        model = _build_openrouter_model(tier)
        agent = Agent(
            model=model,
            system_prompt=system_prompt,
            output_type=result_type,
            retries=1,
            output_retries=max(0, int(settings.extraction_output_retries)),
            model_settings=_build_openrouter_model_settings(tier),
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
                        data.summary_source.has_useful_info,
                    ]
                )
                if data.has_useful_info and not has_payload:
                    retries_state["count"] += 1
                    raise ModelRetry("has_useful_info=true requires at least one populated field")
            return data

        llm_stats.total_calls += 1
        result = await asyncio.wait_for(
            execute_billable_request(
                lambda: agent.run(user_prompt),
                model=get_model(tier),
                school_id=school_id,
                stage="extract",
            ),
            timeout=timeout_seconds,
        )
        llm_stats.model_retries += retries_state["count"]
        parsed = _parse_agent_output(result, result_type)
        input_tokens, output_tokens = helpers._get_usage(result)
        token_cost_usd = _extract_openrouter_cost_usd(result)
        return parsed, input_tokens, output_tokens, token_cost_usd

    def _record_failure(exc: Exception, *, final: bool) -> None:
        if helpers._is_output_validation_error(exc):
            llm_stats.typed_validation_failures += 1
        if helpers._is_model_or_provider_error(exc):
            llm_stats.provider_failures += 1
        if final:
            llm_stats.hard_failures += 1
            llm_stats.add_failure(exc, stage=result_type.__name__)
            logger.warning("Extraction call failed (%s): %s", result_type.__name__, exc)

    try:
        return await _run_for_tier(preferred_tier)
    except Exception as exc:
        should_try_capable_fallback = preferred_tier == "cheap" and result_type is GeneralInfoExtractionOutput
        _record_failure(exc, final=not should_try_capable_fallback)
        if should_try_capable_fallback:
            llm_stats.capable_fallback_attempts += 1
            try:
                out = await _run_for_tier("capable")
            except Exception as capable_exc:
                _record_failure(capable_exc, final=True)
                return None, 0, 0, 0.0
            llm_stats.capable_fallback_successes += 1
            return out
        _record_failure(exc, final=True)
        return None, 0, 0, 0.0


def _has_display_name_signal(text: str) -> bool:
    snippet = text[:2500]
    patterns = (
        r"!\[([^\]]{3,160})\]\(",
        r"\[([^\]]{3,160})\]\(https?://[^)]+\)",
        r"(?m)^#{1,3}\s+(.{3,160})$",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, snippet, flags=re.IGNORECASE):
            candidate = helpers._refine_display_name_label(match.group(1))
            if not candidate or helpers._is_low_quality_display_name(candidate):
                continue
            lowered = candidate.casefold()
            if lowered in {
                "about us",
                "за нас",
                "preschool",
                "contact",
                "contacts",
                "контакти",
                "admission",
                "pricing",
                "documents",
                "request a meeting",
            }:
                continue
            if any(noise in lowered for noise in ("cookie", "consent", "blog", "reference school")):
                continue
            if helpers._display_name_tokens(candidate):
                return True
    return False


async def _extract_display_name_capable_fallback(
    *,
    school_name: str,
    text: str,
    country_code: str,
    timeout_seconds: float,
    llm_stats: ExtractionLLMStats,
    school_id: int | None = None,
) -> tuple[dict[str, str] | None, int, int, float]:
    system_prompt = (
        "Extract only the public-facing school or brand name shown on the website into display_name_i18n. "
        "Do not return the registry/legal name unless the website itself uses it as the public-facing name."
    )
    user_prompt = (
        f"Registry name: {school_name}\n\n"
        "Identify the website-facing school name from the header/navigation content below. "
        "If the same Latin-script brand is used site-wide, you may repeat the exact same text in both locales.\n\n"
        f"Website content:\n{text[:2500]}"
    )
    parsed, input_tokens, output_tokens, token_cost_usd = await _run_typed_agent(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        result_type=DisplayNameOnlyExtractionOutput,
        timeout_seconds=timeout_seconds,
        llm_stats=llm_stats,
        preferred_tier="capable",
        school_id=school_id,
    )
    if parsed is None:
        return None, input_tokens, output_tokens, token_cost_usd
    normalized = helpers._normalize_display_name_i18n(parsed.display_name_i18n, country_code)
    return normalized, input_tokens, output_tokens, token_cost_usd


def _promote_repeated_display_name_candidate(
    parsed: GeneralInfoExtractionOutput,
    *,
    school: School,
    pages: list[SourcePage],
) -> tuple[GeneralInfoExtractionOutput, str | None]:
    from app.scrapers.display_name_audit import audit_school_display_name

    current_display = helpers._normalize_display_name_i18n(parsed.display_name_i18n, school.country_code) or {}
    finding = audit_school_display_name(
        {
            "id": school.id,
            "name_i18n": school.name_i18n or {},
            "attributes": {"display_name_i18n": current_display},
            "website_url": school.website_url,
        },
        [
            {
                "source_url": page.source_url,
                "page_category": page.page_category,
                "raw_markdown": page.raw_markdown,
            }
            for page in pages
        ],
    )
    if finding is None:
        return parsed, None

    bucket = helpers._text_lang_bucket(finding.candidate_name)
    candidate_i18n = helpers._normalize_display_name_i18n(
        {"en": finding.candidate_name} if bucket == "en" else {"bg": finding.candidate_name},
        school.country_code,
    )
    if not candidate_i18n:
        return parsed, None
    candidate_evidence = _build_display_name_evidence(
        candidate_i18n,
        school=school,
        pages=pages,
    )
    has_strong_repetition = (
        finding.repeated_pages >= 2
        and finding.core_pages >= 1
        and finding.score >= 12
    )
    if not has_strong_repetition and candidate_evidence is None:
        return parsed, None
    # If we already have a display name, require the promoted candidate to be
    # clearly better. When the current display is empty, strong repeated page
    # evidence is allowed to seed it.
    if current_display and not helpers._should_prefer_alias_display_name(current_display, candidate_i18n):
        return parsed, None
    if current_display == candidate_i18n:
        return parsed, None

    return (
        parsed.model_copy(update={"display_name_i18n": candidate_i18n}),
        f"Promoted repeated fuller display name from page evidence: {finding.candidate_name}",
    )


def _labels_match(left: str | None, right: str | None) -> bool:
    generic_labels = {
        "academy",
        "care",
        "center",
        "centre",
        "college",
        "kindergarten",
        "preschool",
        "school",
    }
    generic_tokens = set().union(
        *(helpers._display_name_match_tokens(label) for label in generic_labels)
    )
    left_tokens = helpers._display_name_match_tokens(left) - generic_tokens
    right_tokens = helpers._display_name_match_tokens(right) - generic_tokens
    return bool(left_tokens and right_tokens and left_tokens & right_tokens)


def _refine_display_identity_label(value: str | None) -> str | None:
    label = helpers._sanitize_label(str(value or ""), max_len=200)
    if not label:
        return None
    label = re.sub(r"^(?:лого|logo)\s+", "", label, flags=re.IGNORECASE).strip()
    lowered = label.lower()
    if lowered.startswith(helpers._DISPLAY_NAME_ROLE_PREFIXES):
        return None

    stripped = helpers._DISPLAY_NAME_BG_STRIP_PREFIX.sub("", label).strip(" -,\"'“”„")
    stripped = re.sub(
        (
            r"^(?:с\s+ранно\s+чуждоезиково\s+обучение|с\s+немски\s+език|немска\s+гимназия|"
            r"английска\s+гимназия|френска\s+гимназия)\s+"
        ),
        "",
        stripped,
        flags=re.IGNORECASE,
    ).strip(" -,\"'“”„")
    stripped = re.sub(r"\s+софия\s+\d+$", "", stripped, flags=re.IGNORECASE).strip()
    if stripped and stripped != label and helpers._display_name_tokens(stripped):
        label = helpers._normalize_display_name_case(stripped) or stripped

    stripped = helpers._DISPLAY_NAME_EN_STRIP_PREFIX.sub("", label).strip(" -,\"'“”„")
    if stripped and stripped != label and helpers._display_name_tokens(stripped):
        label = helpers._normalize_display_name_case(stripped) or stripped

    return label


def _display_identity_key(value: str | None) -> str | None:
    label = _refine_display_identity_label(value)
    if not label:
        return None
    label = label.replace("’", "'").replace("“", '"').replace("”", '"')
    label = re.sub(r"\s*&\s*", " and ", label)
    label = helpers.transliterate_bulgarian(label) if re.search(r"[А-Яа-я]", label) else label
    label = re.sub(r"[^a-z0-9]+", " ", label.casefold())
    label = re.sub(r"\s+", " ", label).strip()
    return label or None


def _display_name_has_domain_alias_match(
    display_name_i18n: dict[str, str],
    *,
    registry_name: str | None,
    website_url: str | None,
) -> bool:
    host = urlparse(website_url or "").netloc.casefold().split(":", 1)[0]
    if host.startswith("www."):
        host = host[4:]
    host_parts = _HOST_SUFFIX_EXTRACTOR(host)
    owned_host_url = f"https://{host_parts.domain}" if host_parts.domain else None

    aliases = helpers._extract_host_seed_aliases(owned_host_url)
    host_aligned = helpers._extract_host_aligned_display_name(
        registry_name,
        owned_host_url,
    )
    if host_aligned:
        aliases.extend(host_aligned.values())

    if any(
        _labels_match(display_label, alias)
        for display_label in display_name_i18n.values()
        for alias in aliases
    ):
        return True

    generic_host_labels = {
        "academy",
        "centre",
        "center",
        "college",
        "education",
        "international",
        "kindergarten",
        "nursery",
        "preschool",
        "school",
        "schools",
    }
    # Public suffixes plus locale/technical subdomains, which never carry a brand.
    non_brand_host_labels = {
        "bg",
        "cm",
        "co",
        "com",
        "edu",
        "eu",
        "info",
        "io",
        "net",
        "org",
        "page",
        "sites",
        "space",
        "www",
    }
    # The brand can sit in any host label, not just the leftmost one: it is the
    # registrable domain on ``school.fusion.bg`` and the subdomain on
    # platform-hosted sites like ``ou-doganovo.idwebbg.com``. Checking only
    # ``host.split(".")[0]`` misses the first case entirely.
    # Only the registrable (or private-suffix-owned) label is school-owned.
    # Nested subdomains such as ``portal`` in ``school.portal.fusion.bg`` are
    # technical routing labels and cannot independently corroborate a name.
    raw_host_labels = [host_parts.domain] if host_parts.domain else []

    host_compacts: list[str] = []
    for raw_label in raw_host_labels:
        host_compact = re.sub(r"[^a-z0-9]+", "", raw_label)
        if host_compact in non_brand_host_labels:
            continue
        if host_compact.endswith("bg") and len(host_compact) > 6:
            host_compact = host_compact[:-2]
        if len(host_compact) < 3 or host_compact in generic_host_labels:
            continue
        host_compacts.append(host_compact)
    if not host_compacts:
        return False

    def compact(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", value.casefold())

    def acronym(value: str) -> str:
        ignored = {"and", "of", "the"}
        words = re.findall(r"[a-z0-9]+", value.casefold())
        return "".join(word[0] for word in words if word not in ignored)

    generic_suffixes = ("school", "centre", "center", "carecentre", "carecenter")
    institution_words = {
        "academy",
        "care",
        "center",
        "centre",
        "college",
        "education",
        "gymnasium",
        "institute",
        "kindergarten",
        "lyceum",
        "nursery",
        "preschool",
        "school",
        "university",
    }
    for display_label in display_name_i18n.values():
        label_compact = compact(display_label)
        for host_compact in host_compacts:
            if label_compact == host_compact:
                return True
            if acronym(display_label) == host_compact:
                label_words = set(re.findall(r"[a-z0-9]+", display_label.casefold()))
                if label_words & institution_words or _labels_match(display_label, registry_name):
                    return True
            if label_compact.startswith(host_compact):
                remainder = label_compact[len(host_compact) :]
                if remainder in generic_suffixes:
                    return True
    return False


def _display_name_has_exact_official_page_identity(
    display_name_i18n: dict[str, str],
    *,
    website_url: str | None,
    pages: list[SourcePage],
) -> bool:
    """Match a candidate literally in identity-rich cached official-page text."""
    from app.scrapers.display_name_audit import _page_bonus

    def normalized(value: str | None) -> str:
        text = re.sub(r"https?://\S+", " ", str(value or ""), flags=re.IGNORECASE)
        return " " + re.sub(r"[^\w]+", " ", text.casefold()).strip() + " "

    candidate_values = [
        value
        for value in display_name_i18n.values()
        if not re.search(r"\s+[|–—-]\s+|\|", value)
    ]
    labels = [
        key
        for value in candidate_values
        if len((key := normalized(value)).strip()) >= 4
    ]
    flexible_digit_patterns = [
        re.compile(r"(?<!\w)" + r"\s*".join(map(re.escape, re.findall(r"\w+", value.casefold()))) + r"(?!\w)")
        for value in candidate_values
        if re.search(r"\d", value)
    ]
    if not labels:
        return False

    configured_site = urlparse(website_url or "")
    configured_host = configured_site.netloc.casefold().removeprefix("www.")
    configured_path = "/" + (configured_site.path or "").strip("/")

    for page in pages:
        parsed = urlparse(page.source_url or "")
        page_host = parsed.netloc.casefold().removeprefix("www.")
        page_path = "/" + (parsed.path or "").strip("/")
        is_configured_landing = (
            bool(configured_host)
            and page_host == configured_host
            and page_path == configured_path
        )
        is_locale_root = re.fullmatch(
            r"/[a-z]{2}(?:[-_][a-z]{2})?",
            page_path,
            flags=re.IGNORECASE,
        ) is not None
        is_homepage = page_path == "/" or is_configured_landing or is_locale_root
        page_payload = {
            "source_url": page.source_url,
            "page_category": page.page_category,
        }
        if not is_homepage and _page_bonus(page_payload) <= 0:
            continue
        raw_page_text = re.sub(
            r"https?://\S+",
            " ",
            (page.raw_markdown or "")[:15000],
            flags=re.IGNORECASE,
        )
        for raw_line in raw_page_text.splitlines():
            line = normalized(raw_line)
            if any(label in line for label in labels) or any(
                pattern.search(raw_line.casefold()) for pattern in flexible_digit_patterns
            ):
                return True
    return False


def _display_name_has_repeated_page_identity(
    display_name_i18n: dict[str, str],
    *,
    school: School,
    pages: list[SourcePage],
) -> bool:
    from app.scrapers.display_name_audit import _extract_candidates_from_page, _page_bonus

    display_keys = {
        key
        for display_label in display_name_i18n.values()
        if (key := _display_identity_key(display_label))
    }
    if not display_keys:
        return False

    matching_page_urls_by_key: dict[str, set[str]] = {}
    matching_core_urls_by_key: dict[str, set[str]] = {}
    for page in pages:
        page_payload = {
            "source_url": page.source_url,
            "page_category": page.page_category,
            "raw_markdown": page.raw_markdown,
        }
        candidates = _extract_candidates_from_page(page_payload)
        if not candidates:
            continue

        page_key = page.source_url or f"page:{page.id or len(matching_page_urls_by_key)}"
        is_core_page = _page_bonus(page_payload) > 0
        for candidate, _source_kind in candidates:
            candidate_key = _display_identity_key(candidate)
            if candidate_key not in display_keys:
                continue
            matching_page_urls_by_key.setdefault(candidate_key, set()).add(page_key)
            if is_core_page:
                matching_core_urls_by_key.setdefault(candidate_key, set()).add(page_key)

    return any(
        len(page_urls) >= 2 and len(matching_core_urls_by_key.get(candidate_key, set())) >= 1
        for candidate_key, page_urls in matching_page_urls_by_key.items()
    )


def _build_display_name_evidence(
    display_name_i18n: dict[str, str] | None,
    *,
    school: School,
    pages: list[SourcePage],
) -> dict[str, Any] | None:
    if not display_name_i18n:
        return None

    # Evidence is stored for the localized map as a whole. When an English
    # candidate exists, require both signals to corroborate that exact English
    # value; a valid Bulgarian brand must not accidentally publish an invented
    # English translation alongside it.
    evidence_display_name = (
        {"en": display_name_i18n["en"]}
        if display_name_i18n.get("en")
        else display_name_i18n
    )

    registry_name = (school.name_i18n or {}).get("bg") or (school.name_i18n or {}).get("en")
    signals: list[str] = []
    if _display_name_has_domain_alias_match(
        evidence_display_name,
        registry_name=registry_name,
        website_url=school.website_url,
    ):
        signals.append("website_domain_alias_match")
    has_repeated_identity = _display_name_has_repeated_page_identity(
        evidence_display_name,
        school=school,
        pages=pages,
    )
    if has_repeated_identity:
        signals.append("repeated_on_page_identity")
    elif _display_name_has_exact_official_page_identity(
        evidence_display_name,
        website_url=school.website_url,
        pages=pages,
    ):
        signals.append("exact_official_page_identity")

    if len(set(signals)) < 2:
        return None

    return {
        "signals": signals,
        "status": "corroborated",
    }


def _supported_price_rows(
    prices: list[ExtractedPrice], selected_text: str
) -> list[ExtractedPrice]:
    """Evidence-filter then de-duplicate extracted price rows.

    Shared by the price-extraction path (LLM and deterministic branches) and the
    golden-corpus harness so the filter/dedupe sequence can never drift between them.
    """
    if not prices:
        return []
    return helpers._dedupe_price_rows(helpers._filter_supported_prices(prices, selected_text))


def _normalized_price_fields(extracted: ExtractedPrice) -> dict[str, Any] | None:
    """Coerce one ``ExtractedPrice`` into normalized persisted-row field values.

    Returns ``None`` when the row cannot be persisted (unknown category/period or
    no amount). Shared by ``_extract_prices`` row assembly and the golden-corpus
    harness so coercion stays identical between production and the regression suite.
    """
    category = helpers._coerce_price_category(extracted.category)
    period = helpers._coerce_price_period(extracted.period)
    amount = helpers._to_optional_float(extracted.amount)
    amount_min = helpers._to_optional_float(extracted.amount_min)
    amount_max = helpers._to_optional_float(extracted.amount_max)
    if category is None or period is None:
        return None
    if amount is None and amount_min is None and amount_max is None:
        return None
    return {
        "category": category,
        "period": period,
        "amount": amount,
        "amount_min": amount_min,
        "amount_max": amount_max,
        "currency": (extracted.currency or "BGN")[:3].upper(),
        "plan_name": helpers._normalize_scalar_text(extracted.plan_name, max_len=100),
        "academic_year": helpers._normalize_scalar_text(extracted.academic_year, max_len=20),
        "age_group": helpers._normalize_scalar_text(extracted.age_group, max_len=50),
    }


def _build_extracted_attributes(
    normalized: GeneralInfoExtractionOutput,
    contact_info: dict[str, Any] | None,
) -> dict[str, Any]:
    """Assemble the ``attributes.extracted`` payload from normalized general info.

    Shared by ``_extract_general_info`` and the golden-corpus harness so the
    display projection built from extraction output stays identical between them.
    """
    extracted: dict[str, Any] = {
        "_schema_version": 1,
        "languages": [entry.model_dump() for entry in normalized.languages],
        "facilities": normalized.facilities,
        "programs": normalized.programs,
        "extracurricular": normalized.extracurricular,
        "class_size": normalized.class_size,
        "founded_year": normalized.founded_year,
        "accreditations": normalized.accreditations,
        "admission": normalized.admission.model_dump(),
        "operations": normalized.operations.model_dump(),
        "services": normalized.services.model_dump(),
        "pricing_terms": normalized.pricing_terms.model_dump(),
        "summary_source": normalized.summary_source.model_dump(),
    }
    if contact_info:
        extracted["contact"] = contact_info
    return extracted


def _retention_year_key(value: str | None) -> tuple[bool, str]:
    """Bucket a stored academic year for replacement.

    Canonical years match across spellings, so a stored "2026-2027" is superseded by an
    incoming "2026/2027". A blank year forms the undated bucket. A non-blank year that
    does not normalize keeps its own bucket rather than collapsing into the undated one,
    so an unrecognised value is never deleted as collateral.
    """
    raw = str(value or "").strip()
    if not raw:
        return (False, "")
    canonical = helpers._normalize_academic_year(raw)
    return (True, canonical) if canonical else (False, raw)


async def _extract_prices(
    db: AsyncSession,
    school: School,
    pages: list[SourcePage],
    timeout_seconds: float,
    llm_stats: ExtractionLLMStats,
) -> dict[str, Any]:
    selected_text, _source_urls = helpers._select_pages(
        school=school,
        pages=pages,
        preferred_categories=PRICING_PAGE_CATEGORIES,
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
        "Extract school pricing into structured output.\n"
        "Categories: tuition, food, transport, registration, materials, extended_day, uniforms, extracurricular, camp.\n"
        "Periods: monthly, yearly, one_time, quarter, term, semester.\n"
        "\n"
        "Emit ONE row per distinct fee. Apply these rules:\n"
        "- Dual currencies: when the same fee is quoted in both EUR and BGN (e.g. '€8,100 / 15 842,22 лв'), emit only ONE row in the page's primary currency. Never emit a BGN row for a fee already emitted in EUR (or vice versa).\n"
        "- Payment schedules: when one fee has multiple payment options (full pay / 2 installments / 10 monthly), emit ONE row with the full-payment amount as `amount` and list the other options as strings in `installments` (e.g. '€8,100 – 2 installments'). Do NOT emit separate rows for the installment amounts.\n"
        "- Distinct tiers: when multiple tiers exist (e.g. 'Bulgarian students' vs 'International students', different grade bands, different meal plans like breakfast vs full-day), emit SEPARATE rows and set `plan_name` to the tier label from the page. `plan_name` must be populated whenever multiple rows share the same category/period/age_group on one page.\n"
        "- Set `age_group` when the page specifies it (grade range, preschool, nursery, etc.).\n"
        "- Set `academic_year` when the page specifies it (e.g. '2025/2026').\n"
        "\n"
        "If no concrete pricing exists, return has_pricing_info=false and prices=[]."
    )
    user_prompt = f"School: {school_name}\n\nContent:\n{selected_text}"
    deterministic_pricing = helpers._extract_prices_deterministic(selected_text)
    used_deterministic_pricing = False

    parsed, input_tokens, output_tokens, token_cost_usd = await _run_typed_agent(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        result_type=PriceExtractionOutput,
        timeout_seconds=timeout_seconds,
        llm_stats=llm_stats,
        school_id=school.id,
    )

    if parsed is None:
        if deterministic_pricing.has_pricing_info:
            parsed = deterministic_pricing
            used_deterministic_pricing = True
        else:
            return {
                "success": False,
                "count": 0,
                "detail": "Price extraction LLM call failed",
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "token_cost_usd": token_cost_usd,
            }

    if not parsed.prices and deterministic_pricing.has_pricing_info:
        parsed = deterministic_pricing
        used_deterministic_pricing = True

    supported_prices = _supported_price_rows(parsed.prices, selected_text)
    if supported_prices:
        parsed = parsed.model_copy(update={"prices": supported_prices, "has_pricing_info": True})
    elif deterministic_pricing.has_pricing_info:
        deterministic_supported_prices = _supported_price_rows(deterministic_pricing.prices, selected_text)
        if deterministic_supported_prices:
            parsed = deterministic_pricing.model_copy(
                update={"prices": deterministic_supported_prices, "has_pricing_info": True}
            )
            used_deterministic_pricing = True
        else:
            parsed = parsed.model_copy(update={"prices": [], "has_pricing_info": False})
    else:
        parsed = parsed.model_copy(update={"prices": [], "has_pricing_info": False})

    if not parsed.has_pricing_info:
        # A completed model call plus deterministic evidence pass is an
        # authoritative refresh result. Keeping prior website rows here would
        # silently republish fees that the current source no longer supports.
        # Provider failures return above and deliberately preserve old rows.
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

    pricing_rows: list[Pricing] = []
    source_rows: list[FieldSource] = []

    for extracted in parsed.prices:
        fields = _normalized_price_fields(extracted)
        if fields is None:
            continue
        category = fields["category"]
        period = fields["period"]
        amount = fields["amount"]
        amount_min = fields["amount_min"]
        amount_max = fields["amount_max"]

        normalized_discounts = helpers._normalize_text_list(extracted.discounts)
        normalized_installments = helpers._normalize_text_list(extracted.installments)
        normalized_includes = helpers._normalize_text_list(extracted.includes)
        normalized_excludes = helpers._normalize_text_list(extracted.excludes)
        row_source_url = helpers._find_supporting_price_source_url(school, pages, extracted)
        if not row_source_url:
            continue
        supporting_page = next(
            (page for page in pages if page.source_url == row_source_url),
            None,
        )
        if (
            not fields["academic_year"]
            and supporting_page is not None
            and helpers._yearless_pricing_text_is_stale(supporting_page.raw_markdown or "")
        ):
            continue

        pricing_rows.append(
            Pricing(
                school_id=school.id,
                category=category,
                amount=amount,
                amount_min=amount_min,
                amount_max=amount_max,
                currency=fields["currency"],
                period=period,
                plan_name=fields["plan_name"],
                academic_year=fields["academic_year"],
                age_group=fields["age_group"],
                source=PriceSource.SCRAPED_WEBSITE,
                source_url=row_source_url,
                source_page_id=supporting_page.id if supporting_page else None,
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
                source_url=row_source_url,
                scraped_at=helpers._utcnow_naive(),
                confidence=SourceConfidence.HIGH if (extracted.confidence or 0) >= 0.8 else SourceConfidence.MEDIUM,
                confidence_score=extracted.confidence,
            )
        )

    if not pricing_rows:
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
            "detail": "Pricing info detected but no valid rows after normalization (cleared existing scraped pricing)",
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "token_cost_usd": token_cost_usd,
        }

    # Supersede only the academic years this run actually produced, so earlier years
    # survive as fee history. Rows the school still publishes undated are replaced by
    # the undated rows of this run.
    written_years = {_retention_year_key(row.academic_year) for row in pricing_rows}
    existing_result = await db.execute(
        select(Pricing).where(
            Pricing.school_id == school.id,
            Pricing.source == PriceSource.SCRAPED_WEBSITE,
        )
    )
    superseded_ids = [
        existing.id
        for existing in existing_result.scalars().all()
        if _retention_year_key(existing.academic_year) in written_years
    ]
    if superseded_ids:
        await db.execute(delete(Pricing).where(Pricing.id.in_(superseded_ids)))
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
        "detail": (
            f"Extracted {len(pricing_rows)} pricing rows"
            + (" (deterministic fallback)" if used_deterministic_pricing else "")
        ),
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
        preferred_categories=GENERAL_INFO_PAGE_CATEGORIES,
        use_case="general_info",
        include_tokens=GENERAL_INFO_HINT_TOKENS,
    )
    narrative_text, _ = helpers._select_pages(
        school=school,
        pages=pages,
        preferred_categories=SUMMARY_SOURCE_PAGE_CATEGORIES,
        use_case="general_summary_source",
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
    known_aliases = [str(value) for value in dict(school.attributes or {}).get("name_aliases", []) if str(value or "").strip()]

    user_prompt = f"Registry name: {school_name}\n\nWebsite content:\n{selected_text}"

    parsed, input_tokens, output_tokens, token_cost_usd = await _run_typed_agent(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        result_type=GeneralInfoExtractionOutput,
        timeout_seconds=timeout_seconds,
        llm_stats=llm_stats,
        school_id=school.id,
    )
    detail_note: str | None = None
    if parsed is None:
        parsed = _build_deterministic_general_info_output(
            all_page_text,
            narrative_text=narrative_text,
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
            narrative_text=narrative_text,
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
                "class size, founded year, accreditations, admission, operations, services, pricing terms, and summary_source. "
                "Do not invent values. "
                "Only populate display_name_i18n when the website explicitly shows a public-facing name. "
                "For languages, class_size, and founded_year: only include them when explicitly stated; otherwise "
                "leave them empty/null. "
                "For summary_source, prefer concise evidence-backed phrases over polished prose."
            )
            max_chars = max(2000, int(settings.extraction_max_content_chars))
            recovery_user_prompt = f"School: {school_name}\n\nContent:\n{all_page_text[:max_chars]}"
            recovered, in2, out2, cost2 = await _run_typed_agent(
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
                recovered_augmented = _augment_general_info_with_deterministic(
                    recovered,
                    all_page_text,
                    narrative_text=narrative_text,
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

        if not parsed.display_name_i18n and _has_display_name_signal(selected_text):
            recovered_display_name, in3, out3, cost3 = await _extract_display_name_capable_fallback(
                school_name=school_name,
                text=selected_text,
                country_code=school.country_code,
                timeout_seconds=timeout_seconds,
                llm_stats=llm_stats,
                school_id=school.id,
            )
            input_tokens += in3
            output_tokens += out3
            token_cost_usd += cost3
            if recovered_display_name:
                parsed.display_name_i18n = recovered_display_name
                parsed = _augment_general_info_with_deterministic(
                    parsed,
                    all_page_text,
                    narrative_text=narrative_text,
                    registry_name=school_name,
                    country_code=school.country_code,
                    website_url=school.website_url,
                    known_aliases=known_aliases,
                )
                detail_note = (
                    f"{detail_note}; recovered display name with capable fallback"
                    if detail_note
                    else "Recovered display name with capable fallback"
                )

        parsed, promoted_display_name_note = _promote_repeated_display_name_candidate(
            parsed,
            school=school,
            pages=pages,
        )
        if promoted_display_name_note:
            detail_note = (
                f"{detail_note}; {promoted_display_name_note}"
                if detail_note
                else promoted_display_name_note
            )

    normalized, extracted_i18n, display_name_i18n = helpers._normalize_general_info_output(parsed, school.country_code)
    display_name_evidence = _build_display_name_evidence(
        display_name_i18n,
        school=school,
        pages=pages,
    )
    contact_info = helpers._extract_contact_info_deterministic(all_page_text)
    location_address_update = await _sync_primary_location_from_contact_address(db, school, contact_info)

    attrs = prepare_validation_rollover(school.attributes)
    admission_info = dict(school.admission_info) if isinstance(school.admission_info, dict) else {}

    admission_payload = normalized.admission.model_dump()
    operations_payload = normalized.operations.model_dump()
    services_payload = normalized.services.model_dump()
    pricing_terms_payload = normalized.pricing_terms.model_dump()
    summary_source_payload = normalized.summary_source.model_dump()

    extracted = _build_extracted_attributes(normalized, contact_info)

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


def _merge_summary_source_info(
    llm: SummarySourceExtractionOutput,
    deterministic: SummarySourceExtractionOutput,
) -> SummarySourceExtractionOutput:
    merged = SummarySourceExtractionOutput(
        positioning=llm.positioning or deterministic.positioning,
        teaching_approach=helpers._merge_text_values(
            llm.teaching_approach, deterministic.teaching_approach
        ),
        student_experience=helpers._merge_text_values(
            llm.student_experience, deterministic.student_experience
        ),
        community_signals=helpers._merge_text_values(
            llm.community_signals, deterministic.community_signals
        ),
        differentiators=helpers._merge_text_values(
            llm.differentiators, deterministic.differentiators
        ),
        canonical_tags=helpers._merge_text_values(
            llm.canonical_tags, deterministic.canonical_tags
        ),
        has_useful_info=False,
    )
    merged.has_useful_info = any(
        (
            merged.positioning,
            merged.teaching_approach,
            merged.student_experience,
            merged.community_signals,
            merged.differentiators,
            merged.canonical_tags,
        )
    )
    return merged


def _augment_general_info_with_deterministic(
    llm_output: GeneralInfoExtractionOutput,
    all_page_text: str,
    narrative_text: str,
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
    deterministic_summary_source = helpers._extract_summary_source_deterministic(
        narrative_text or all_page_text
    )

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
        summary_source=_merge_summary_source_info(
            llm_output.summary_source, deterministic_summary_source
        ),
        has_useful_info=False,
    )
    merged.has_useful_info = helpers._score_general_info_output(merged) > 0
    return merged


def _build_deterministic_general_info_output(
    all_page_text: str,
    narrative_text: str,
    registry_name: str | None,
    country_code: str,
    website_url: str | None,
    known_aliases: list[str] | None,
) -> GeneralInfoExtractionOutput:
    return _augment_general_info_with_deterministic(
        GeneralInfoExtractionOutput(),
        all_page_text,
        narrative_text,
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
        "validation_status": None,
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
        clear_summary_state(school)
        school.scrape_status = "extracted"
        stats["status"] = "extracted"
    else:
        school.scrape_status = "extraction_failed"

    school.updated_at = datetime.datetime.now(datetime.timezone.utc)
    db.add(school)

    if stats["status"] == "extracted":
        await db.flush()
        validation_result = await validate_school_data(
            db=db,
            school_id=school_id,
            country_code=country_code,
            run_spot_check=False,
        )
        validation_status = validation_result.get("status")
        stats["validation_status"] = validation_status
        stats["validation_issue_counts"] = validation_result.get("issue_counts")
        stats["validation_auto_fixes"] = validation_result.get("auto_fixes")
        if validation_status == "validation_failed":
            await db.rollback()
            stats["status"] = "extraction_failed"
            stats["error"] = f"Validation failed during extraction: {validation_result.get('error') or 'unknown error'}"
            stats["details"].append("Validation failed before commit; extracted data was not published")
            stats["token_cost_usd"] = round(float(stats["token_cost_usd"] or 0.0), 6)
            stats["llm_stats"] = llm_stats.as_dict()
            return stats
        if stats["general_info_success"]:
            # General extraction replaces (or explicitly clears) every website-derived
            # attribute branch. A pricing-only success cannot safely reopen preserved
            # attributes from a URL that was previously invalidated.
            attrs = dict(school.attributes or {})
            attrs.pop(WEBSITE_DATA_WITHHELD_KEY, None)
            school.attributes = attrs
        elif (school.attributes or {}).get(WEBSITE_DATA_WITHHELD_KEY):
            stats["details"].append(
                "Website data remains withheld until general information is refreshed"
            )
        stats["details"].append(f"Validation completed before commit ({validation_status})")

    await db.commit()

    stats["token_cost_usd"] = round(float(stats["token_cost_usd"] or 0.0), 6)
    stats["llm_stats"] = llm_stats.as_dict()
    return stats
