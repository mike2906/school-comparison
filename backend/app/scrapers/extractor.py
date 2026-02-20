"""Stage 5 extraction utilities.

Extracts structured pricing and general school info from navigated website pages.
"""

from __future__ import annotations

import asyncio
import ast
import datetime
import json
import logging
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse
from typing import Any, Optional

from pydantic import ValidationError
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError, UnexpectedModelBehavior

from app.ai.client import create_agent, get_model
from app.config import get_settings
from app.models.field_source import FieldSource, SourceConfidence, SourceType
from app.models.pricing import PriceCategory, PricePeriod, PriceSource, Pricing
from app.models.school import School
from app.models.scrape_log import ScrapeType
from app.models.source_page import SourcePage
from app.schemas.extraction import (
    ExtractedLanguageFocus,
    FacilitiesExtractionOutput,
    GeneralInfoExtractionOutput,
    LanguagesExtractionOutput,
    MetadataExtractionOutput,
    PriceExtractionOutput,
    ProgramsExtractionOutput,
)

logger = logging.getLogger(__name__)
_REQUEST_THROTTLE_LOCK = asyncio.Lock()
_LAST_LLM_REQUEST_AT = 0.0


_CATEGORY_ALIASES: dict[str, PriceCategory] = {
    "tuition": PriceCategory.TUITION,
    "обучение": PriceCategory.TUITION,
    "таксаобучение": PriceCategory.TUITION,
    "schoolfee": PriceCategory.TUITION,
    "food": PriceCategory.FOOD,
    "храна": PriceCategory.FOOD,
    "transport": PriceCategory.TRANSPORT,
    "транспорт": PriceCategory.TRANSPORT,
    "activities": PriceCategory.ACTIVITIES,
    "activity": PriceCategory.ACTIVITIES,
    "дейности": PriceCategory.ACTIVITIES,
    "registration": PriceCategory.REGISTRATION,
    "enrollment": PriceCategory.REGISTRATION,
    "admission": PriceCategory.REGISTRATION,
    "materials": PriceCategory.MATERIALS,
    "extendedday": PriceCategory.EXTENDED_DAY,
    "extended": PriceCategory.EXTENDED_DAY,
    "uniforms": PriceCategory.UNIFORMS,
    "extracurricular": PriceCategory.EXTRACURRICULAR,
    "camp": PriceCategory.CAMP,
}

_PERIOD_ALIASES: dict[str, PricePeriod] = {
    "monthly": PricePeriod.MONTHLY,
    "month": PricePeriod.MONTHLY,
    "месечно": PricePeriod.MONTHLY,
    "месец": PricePeriod.MONTHLY,
    "yearly": PricePeriod.YEARLY,
    "annual": PricePeriod.YEARLY,
    "годишно": PricePeriod.YEARLY,
    "year": PricePeriod.YEARLY,
    "one_time": PricePeriod.ONE_TIME,
    "onetime": PricePeriod.ONE_TIME,
    "single": PricePeriod.ONE_TIME,
    "еднократно": PricePeriod.ONE_TIME,
    "quarter": PricePeriod.QUARTER,
    "quarterly": PricePeriod.QUARTER,
    "term": PricePeriod.TERM,
    "semester": PricePeriod.SEMESTER,
    "семестър": PricePeriod.SEMESTER,
}

_PROVIDER_ERROR_MARKERS = (
    "not a valid model id",
    "openrouter",
    "provider",
    "authentication",
    "failed to authenticate",
    "chat completions endpoint",
    "status_code",
)

_OUTPUT_VALIDATION_ERROR_MARKERS = (
    "output validation",
    "result validation",
    "structured parse returned no data",
    "validation error",
    "validation errors",
    "invalid json",
    "json decode",
    "json parse",
    "failed to validate",
    "failed validation",
    "exceeded maximum retries",
    "retry attempts exhausted",
)

_GENERAL_INFO_LIST_MAX_ITEMS = 20
_GENERAL_INFO_ITEM_MAX_LEN = 100
_GENERAL_INFO_CLASS_SIZE_MAX_LEN = 48
_GENERAL_INFO_JUNK_VALUES = {
    "n/a",
    "na",
    "none",
    "null",
    "unknown",
    "неизвестно",
    "-",
    "--",
    ".",
}

_GENERAL_INFO_TEXT_KEYS = (
    "name",
    "title",
    "label",
    "language",
    "program",
    "program_name",
    "facility",
    "activity",
    "accreditation",
    "value",
    "text",
)

_GENERAL_INFO_SECTION_CONFIG: dict[str, dict[str, tuple[str, ...] | list[str]]] = {
    "languages": {
        "preferred_categories": ["about", "contact"],
        "include_tokens": (
            "language",
            "languages",
            "език",
            "езици",
            "чужд",
            "двуезич",
            "bilingual",
            "english",
            "английски",
            "немски",
            "deutsch",
            "french",
            "français",
        ),
    },
    "facilities": {
        "preferred_categories": ["facilities", "about", "contact"],
        "include_tokens": (
            "facility",
            "facilities",
            "campus",
            "base",
            "classroom",
            "laboratory",
            "library",
            "sport",
            "pool",
            "material",
            "база",
            "сграда",
            "класна",
            "лаборатория",
            "библиотека",
            "двор",
            "физкултур",
            "плувен",
        ),
    },
    "programs": {
        "preferred_categories": ["programs", "about", "admission", "contact"],
        "include_tokens": (
            "program",
            "curriculum",
            "method",
            "montessori",
            "waldorf",
            "ib",
            "cambridge",
            "stem",
            "robotics",
            "club",
            "programme",
            "програма",
            "обучение",
            "клуб",
            "извънклас",
            "занимания",
            "роботика",
        ),
    },
    "metadata": {
        "preferred_categories": ["about", "contact"],
        "include_tokens": (
            "founded",
            "established",
            "since",
            "year",
            "class",
            "students",
            "accreditation",
            "accredited",
            "founding",
            "основан",
            "създаден",
            "учреден",
            "година",
            "клас",
            "ученици",
            "акредитац",
            "лиценз",
        ),
    },
}

_DETERMINISTIC_LANGUAGE_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Bulgarian", ("български", "bulgarian")),
    ("English", ("английски", "english")),
    ("German", ("немски", "германски", "deutsch", "german")),
    ("French", ("френски", "français", "french")),
    ("Spanish", ("испански", "spanish", "español")),
    ("Italian", ("италиански", "italian", "italiano")),
    ("Russian", ("руски", "russian")),
    ("Turkish", ("турски", "turkish")),
    ("Greek", ("гръцки", "greek")),
    ("Chinese", ("китайски", "chinese", "mandarin")),
    ("Japanese", ("японски", "japanese")),
    ("Hebrew", ("иврит", "hebrew")),
    ("Arabic", ("арабски", "arabic")),
)

_LANGUAGE_CONTEXT_MARKERS = (
    "език",
    "езици",
    "чужд",
    "двуезич",
    "language",
    "languages",
    "bilingual",
    "teaching",
    "instruction",
    "обучение",
)

_ACCREDITATION_CONTEXT_MARKERS = (
    "accredit",
    "authorized",
    "licensed",
    "certified",
    "affiliated",
    "акредитац",
    "акредит",
    "оторизиран",
    "лиценз",
    "сертифи",
    "удостоверен",
)

_ACCREDITATION_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("International Baccalaureate (IB)", ("international baccalaureate", "ib diploma", "ib programme", "ib program")),
    ("Cambridge International", ("cambridge international", "cambridge assessment", "cambridge english")),
    ("Council of International Schools (CIS)", ("council of international schools", "cis accreditation", "cis accredited")),
    ("COBIS", ("cobis",)),
    ("Pearson Edexcel", ("pearson", "edexcel")),
    ("МОН лиценз", ("мон", "министерство на образованието", "министерството на образованието")),
)

# Bulgarian phone: 0XXXXXXXXX (mobile), 02-XXXXXXX (Sofia landline), 0800... (toll-free)
# Also handles +359XXXXXXXXX, 00359XXXXXXXXX international formats
_PHONE_PATTERN = re.compile(
    r"(?:(?:\+359|00359|0)(?:\s*[-./]?\s*))"  # prefix: +359, 00359, or 0
    r"(?:8[7-9]\d|2|[3-9]\d)"                  # area/mobile prefix
    r"(?:\s*[-./]?\s*\d){5,8}",               # remaining digits with optional separators
    re.IGNORECASE,
)
_EMAIL_PATTERN = re.compile(
    r"[a-z0-9._%+\-]+\s*(?:\[\s*a\s*t\s*\]|@)\s*[a-z0-9.\-]+\.[a-z]{2,}",
    re.IGNORECASE,
)
_CONTACT_NOISE_TOKENS = frozenset({
    "example", "yourname", "email@", "@domain", "placeholder", "username",
    "noreply", "no-reply", "donotreply", "info@info", "test@test",
})


@dataclass
class ExtractionLLMStats:
    """Per-school LLM extraction telemetry for observability."""

    total_calls: int = 0
    typed_validation_failures: int = 0
    relaxed_parse_attempts: int = 0
    relaxed_parse_successes: int = 0
    hard_failures: int = 0
    provider_failures: int = 0
    capable_fallback_attempts: int = 0
    capable_fallback_successes: int = 0
    languages_coercions: int = 0
    typed_failure_details: list[dict[str, Any]] = field(default_factory=list)
    hard_failure_details: list[dict[str, Any]] = field(default_factory=list)

    def add_typed_failure_detail(self, detail: dict[str, Any]) -> None:
        if len(self.typed_failure_details) >= 50:
            return
        self.typed_failure_details.append(detail)

    def add_hard_failure_detail(self, detail: dict[str, Any]) -> None:
        if len(self.hard_failure_details) >= 50:
            return
        self.hard_failure_details.append(detail)

    def add_languages_coercions(self, count: int) -> None:
        if count <= 0:
            return
        self.languages_coercions += int(count)

    def as_dict(self) -> dict[str, Any]:
        typed_failure_rate = (
            float(self.typed_validation_failures) / float(self.total_calls)
            if self.total_calls
            else 0.0
        )
        hard_failure_rate = (
            float(self.hard_failures) / float(self.total_calls)
            if self.total_calls
            else 0.0
        )
        relaxed_success_rate = (
            float(self.relaxed_parse_successes) / float(self.relaxed_parse_attempts)
            if self.relaxed_parse_attempts
            else 0.0
        )
        return {
            "total_calls": self.total_calls,
            "typed_validation_failures": self.typed_validation_failures,
            "typed_validation_failure_rate": round(typed_failure_rate, 4),
            "relaxed_parse_attempts": self.relaxed_parse_attempts,
            "relaxed_parse_successes": self.relaxed_parse_successes,
            "relaxed_parse_success_rate": round(relaxed_success_rate, 4),
            "hard_failures": self.hard_failures,
            "hard_failure_rate": round(hard_failure_rate, 4),
            "provider_failures": self.provider_failures,
            "capable_fallback_attempts": self.capable_fallback_attempts,
            "capable_fallback_successes": self.capable_fallback_successes,
            "languages_coercions": self.languages_coercions,
            "typed_failure_details": self.typed_failure_details,
            "hard_failure_details": self.hard_failure_details,
        }


async def extract_school(
    db: AsyncSession,
    school_id: int,
    country_code: str,
    run_id: Optional[str] = None,
    allow_navigation_refresh_on_empty: bool = True,
) -> dict:
    """Extract pricing + general info for one school from navigated pages."""
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

    if not pages:
        school.scrape_status = "extraction_failed"
        await db.commit()
        return {
            "school_id": school_id,
            "status": "extraction_failed",
            "error": "No navigated source pages found",
            "llm_stats": ExtractionLLMStats().as_dict(),
        }

    content_pages = [page for page in pages if (page.raw_markdown or "").strip()]
    refresh_attempted = False
    refresh_succeeded = False

    # Some navigations end up storing empty raw_markdown for all pages.
    # Retry navigation once before failing extraction with zero LLM calls.
    if not content_pages and allow_navigation_refresh_on_empty:
        refresh_attempted = True
        try:
            from app.scrapers.navigator import navigate_school

            refresh_result = await navigate_school(
                db=db,
                school_id=school_id,
                country_code=country_code,
            )
            refresh_succeeded = bool(refresh_result.get("success"))
        except Exception as exc:
            logger.warning("Navigation refresh before extraction failed for school %s: %s", school_id, exc)

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
            "content_refresh_attempted": refresh_attempted,
            "content_refresh_succeeded": refresh_succeeded,
            "llm_stats": ExtractionLLMStats().as_dict(),
        }

    if _check_hashes_unchanged(school, content_pages):
        return {
            "school_id": school_id,
            "status": school.scrape_status,
            "skipped": True,
            "content_refresh_attempted": refresh_attempted,
            "content_refresh_succeeded": refresh_succeeded,
            "llm_stats": ExtractionLLMStats().as_dict(),
        }

    llm_stats = ExtractionLLMStats()

    stats = {
        "school_id": school_id,
        "status": "extraction_failed",
        "pricing_count": 0,
        "pricing_success": False,
        "general_info_success": False,
        "details": [],
        "input_tokens": 0,
        "output_tokens": 0,
        "skipped": False,
        "content_refresh_attempted": refresh_attempted,
        "content_refresh_succeeded": refresh_succeeded,
        "llm_stats": llm_stats.as_dict(),
    }

    should_extract_prices = school.school_type in {"private", "international"}
    primary_tier = _normalize_primary_tier(settings.extraction_primary_tier)
    if should_extract_prices:
        price_result = await _extract_prices(
            db=db,
            school=school,
            pages=content_pages,
            timeout_seconds=settings.extraction_llm_timeout_seconds,
            primary_tier=primary_tier,
            allow_capable_fallback=settings.extraction_enable_capable_fallback,
            llm_stats=llm_stats,
        )
        stats["pricing_count"] = price_result["count"]
        stats["pricing_success"] = price_result["success"]
        stats["input_tokens"] += price_result["input_tokens"]
        stats["output_tokens"] += price_result["output_tokens"]
        if price_result["detail"]:
            stats["details"].append(price_result["detail"])

    general_result = await _extract_general_info(
        db=db,
        school=school,
        pages=content_pages,
        timeout_seconds=settings.extraction_llm_timeout_seconds,
        primary_tier=primary_tier,
        quality_gate_enabled=settings.extraction_quality_gate_enabled,
        min_quality_score=max(0, int(settings.extraction_general_info_min_quality_score)),
        allow_capable_fallback=settings.extraction_enable_capable_fallback,
        llm_stats=llm_stats,
    )
    stats["general_info_success"] = general_result["success"]
    stats["input_tokens"] += general_result["input_tokens"]
    stats["output_tokens"] += general_result["output_tokens"]
    if general_result["detail"]:
        stats["details"].append(general_result["detail"])

    if stats["pricing_success"] or stats["general_info_success"]:
        school.scrape_status = "extracted"
        stats["status"] = "extracted"
        _store_extraction_hashes(school, content_pages)
    else:
        school.scrape_status = "extraction_failed"

    school.updated_at = datetime.datetime.now(datetime.timezone.utc)
    db.add(school)
    await db.commit()
    stats["llm_stats"] = llm_stats.as_dict()
    return stats


def _check_hashes_unchanged(school: School, pages: list[SourcePage]) -> bool:
    """Return True when current page hash snapshot matches last extraction snapshot."""
    attrs = school.attributes if isinstance(school.attributes, dict) else {}
    stored_hashes = attrs.get("_extraction_hashes")
    if not isinstance(stored_hashes, dict):
        return False

    current_hashes = {p.source_url: p.content_hash for p in pages if p.source_url and p.content_hash}
    if len(stored_hashes) != len(current_hashes):
        return False

    return all(stored_hashes.get(url) == page_hash for url, page_hash in current_hashes.items())


def _store_extraction_hashes(school: School, pages: list[SourcePage]) -> None:
    """Persist extraction hash snapshot into school.attributes."""
    attrs = dict(school.attributes) if isinstance(school.attributes, dict) else {}
    attrs["_extraction_hashes"] = {
        p.source_url: p.content_hash for p in pages if p.source_url and p.content_hash
    }
    school.attributes = attrs


async def _extract_prices(
    db: AsyncSession,
    school: School,
    pages: list[SourcePage],
    timeout_seconds: float,
    primary_tier: str,
    allow_capable_fallback: bool,
    llm_stats: ExtractionLLMStats | None = None,
) -> dict[str, Any]:
    """Extract pricing rows and pricing field sources. Returns extraction stats."""
    settings = get_settings()
    selected_text, source_urls = _select_pages(
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
        "Extract school pricing into structured JSON. "
        "Return category, period, amount/amount_min/amount_max, currency, plan_name, academic_year, age_group, notes, confidence. "
        "Use category names like tuition/food/transport/activities/registration/materials/extended_day/uniforms/extracurricular/camp. "
        "Use period names like monthly/yearly/one_time/quarter/term/semester. "
        "If no concrete pricing is present, set has_pricing_info=false and prices=[]. "
        "Output must be valid JSON only (no markdown fences). "
        'Example: {"prices":[{"category":"tuition","amount":500,"currency":"BGN","period":"monthly"}],"has_pricing_info":true}.'
    )
    user_prompt = (
        f"School: {school_name}\n"
        f"Content:\n{selected_text}\n\n"
        "Output JSON only."
    )

    parsed, input_tokens, output_tokens = await _run_agent_with_fallback(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        result_type=PriceExtractionOutput,
        timeout_seconds=timeout_seconds,
        primary_tier=primary_tier,
        allow_capable_fallback=allow_capable_fallback,
        llm_stats=llm_stats,
    )

    if parsed is None:
        return {
            "success": False,
            "count": 0,
            "detail": "Price extraction LLM call failed",
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        }
    if not isinstance(parsed, PriceExtractionOutput):
        logger.warning("Unexpected price extraction output type: %s", type(parsed))
        return {
            "success": False,
            "count": 0,
            "detail": "Price extraction output type mismatch",
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
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
            }
        return {
            "success": True,
            "count": 0,
            "detail": "No pricing info detected",
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        }
    if not parsed.prices:
        logger.warning(
            "Pricing info flagged for school %s but no price rows returned",
            school.id,
        )
        return {
            "success": True,
            "count": 0,
            "detail": "Pricing info detected but no rows returned",
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        }

    source_url = source_urls[0] if source_urls else None
    pricing_rows: list[Pricing] = []
    source_rows: list[FieldSource] = []
    for extracted in parsed.prices:
        category = _coerce_price_category(extracted.category)
        period = _coerce_price_period(extracted.period)
        amount = _to_optional_float(extracted.amount)
        amount_min = _to_optional_float(extracted.amount_min)
        amount_max = _to_optional_float(extracted.amount_max)

        if category is None or period is None:
            continue
        if amount is None and amount_min is None and amount_max is None:
            continue

        pricing_rows.append(
            Pricing(
                school_id=school.id,
                category=category,
                amount=amount,
                amount_min=amount_min,
                amount_max=amount_max,
                currency=(extracted.currency or "BGN")[:3].upper(),
                period=period,
                plan_name=extracted.plan_name,
                academic_year=extracted.academic_year,
                age_group=extracted.age_group,
                source=PriceSource.SCRAPED_WEBSITE,
                source_url=source_url,
                pricing_context={
                    "notes": extracted.notes,
                    "confidence": extracted.confidence,
                },
            )
        )
        source_rows.append(
            FieldSource(
                school_id=school.id,
                category="pricing",
                field_key=f"pricing.{category.value}.{period.value}",
                value_text=_format_price_value_text(amount, amount_min, amount_max, extracted.currency),
                source_type=SourceType.SCRAPED_WEBSITE,
                source_url=source_url,
                scraped_at=_utcnow_naive(),
                confidence=SourceConfidence.HIGH if (extracted.confidence or 0) >= 0.8 else SourceConfidence.MEDIUM,
                confidence_score=extracted.confidence,
            )
        )

    if not pricing_rows:
        logger.warning(
            "Pricing info detected for school %s but no valid rows after normalization",
            school.id,
        )
        return {
            "success": True,
            "count": 0,
            "detail": "Pricing info detected but no valid rows after normalization",
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
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
    }


async def _extract_general_info(
    db: AsyncSession,
    school: School,
    pages: list[SourcePage],
    timeout_seconds: float,
    primary_tier: str,
    quality_gate_enabled: bool,
    min_quality_score: int,
    allow_capable_fallback: bool,
    llm_stats: ExtractionLLMStats | None = None,
) -> dict[str, Any]:
    """Extract general info with field-focused prompts and normalization."""
    school_name = (school.name_i18n or {}).get("bg") or (school.name_i18n or {}).get("en") or ""
    fallback_content, source_urls = _select_pages(
        school=school,
        pages=pages,
        preferred_categories=["about", "contact"],
        use_case="general_info",
    )
    if not fallback_content:
        return {
            "success": False,
            "detail": "No general-info content found",
            "input_tokens": 0,
            "output_tokens": 0,
        }

    combined, input_tokens, output_tokens = await _extract_general_info_sections(
        school=school,
        pages=pages,
        school_name=school_name,
        fallback_content=fallback_content,
        timeout_seconds=timeout_seconds,
        primary_tier=primary_tier,
        allow_capable_fallback=allow_capable_fallback,
        llm_stats=llm_stats,
    )

    quality_note: str | None = None
    if quality_gate_enabled and primary_tier == "cheap":
        cheap_normalized, _ = _normalize_general_info_output(combined, school.country_code)
        cheap_quality = _score_general_info_output(cheap_normalized)
        if cheap_quality < min_quality_score:
            upgraded, extra_input, extra_output = await _extract_general_info_sections(
                school=school,
                pages=pages,
                school_name=school_name,
                fallback_content=fallback_content,
                timeout_seconds=timeout_seconds,
                primary_tier="medium",
                allow_capable_fallback=allow_capable_fallback,
                llm_stats=llm_stats,
            )
            input_tokens += extra_input
            output_tokens += extra_output
            upgraded_normalized, _ = _normalize_general_info_output(upgraded, school.country_code)
            medium_quality = _score_general_info_output(upgraded_normalized)
            if medium_quality >= cheap_quality:
                combined = upgraded
                quality_note = f"quality gate upgraded cheap({cheap_quality})->medium({medium_quality})"
    normalized, extracted_i18n = _normalize_general_info_output(combined, school.country_code)

    # Extract contact info deterministically (no LLM needed)
    all_page_text = "\n".join((p.raw_markdown or "") for p in pages)
    contact_info = _extract_contact_info_deterministic(all_page_text)

    attrs = dict(school.attributes) if isinstance(school.attributes, dict) else {}
    extracted: dict[str, Any] = {
        "languages": [entry.model_dump() for entry in normalized.languages],
        "facilities": normalized.facilities,
        "programs": normalized.programs,
        "extracurricular": normalized.extracurricular,
        "class_size": normalized.class_size,
        "founded_year": normalized.founded_year,
        "accreditations": normalized.accreditations,
    }
    if contact_info:
        extracted["contact"] = contact_info
    attrs["extracted"] = extracted
    # TODO: Confirm frontend consumption of extracted_i18n; drop if unused to avoid schema drift.
    if extracted_i18n:
        attrs["extracted_i18n"] = extracted_i18n
    else:
        attrs.pop("extracted_i18n", None)
    school.attributes = attrs

    await db.execute(
        delete(FieldSource).where(
            FieldSource.school_id == school.id,
            FieldSource.source_type == SourceType.SCRAPED_WEBSITE,
            FieldSource.category == "general_info",
        )
    )

    source_url = source_urls[0] if source_urls else None

    def add_source(field_key: str, value_json: Any = None, value_text: Optional[str] = None) -> None:
        if value_json is None and not value_text:
            return
        if isinstance(value_json, list) and not value_json:
            return
        db.add(
            FieldSource(
                school_id=school.id,
                category="general_info",
                field_key=f"attributes.{field_key}",
                value_json=value_json,
                value_text=value_text,
                source_type=SourceType.SCRAPED_WEBSITE,
                source_url=source_url,
                scraped_at=_utcnow_naive(),
                confidence=SourceConfidence.MEDIUM,
                confidence_score=0.7,
            )
        )

    add_source("languages", value_json=[entry.model_dump() for entry in normalized.languages])
    add_source("facilities", value_json=normalized.facilities)
    add_source("programs", value_json=normalized.programs)
    add_source("extracurricular", value_json=normalized.extracurricular)
    add_source("accreditations", value_json=normalized.accreditations)
    add_source("class_size", value_text=normalized.class_size)
    add_source("founded_year", value_text=normalized.founded_year)
    if contact_info:
        add_source("contact", value_json=contact_info)

    has_any_info = any(
        [
            normalized.languages,
            normalized.facilities,
            normalized.programs,
            normalized.extracurricular,
            normalized.class_size,
            normalized.founded_year,
            normalized.accreditations,
            contact_info,
        ]
    )
    detail = "General info extracted" if has_any_info else "No useful general info detected"
    if quality_note and has_any_info:
        detail = f"{detail} ({quality_note})"
    return {
        "success": True,
        "detail": detail,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }


async def _extract_general_info_sections(
    school: School,
    pages: list[SourcePage],
    school_name: str,
    fallback_content: str,
    timeout_seconds: float,
    primary_tier: str,
    allow_capable_fallback: bool,
    llm_stats: ExtractionLLMStats | None = None,
) -> tuple[GeneralInfoExtractionOutput, int, int]:
    """Run field-focused extraction passes and combine with deterministic signals."""
    # TODO: Consider collapsing section-specific prompts into a single pass if cost/latency becomes an issue.
    input_tokens = 0
    output_tokens = 0

    languages_content = _select_general_info_section_content(
        school=school,
        pages=pages,
        section_key="languages",
        fallback_content=fallback_content,
    )
    facilities_content = _select_general_info_section_content(
        school=school,
        pages=pages,
        section_key="facilities",
        fallback_content=fallback_content,
    )
    programs_content = _select_general_info_section_content(
        school=school,
        pages=pages,
        section_key="programs",
        fallback_content=fallback_content,
    )
    metadata_content = _select_general_info_section_content(
        school=school,
        pages=pages,
        section_key="metadata",
        fallback_content=fallback_content,
    )

    languages_prompt = f"School: {school_name}\nContent:\n{languages_content}\n\nOutput JSON only."
    facilities_prompt = f"School: {school_name}\nContent:\n{facilities_content}\n\nOutput JSON only."
    programs_prompt = f"School: {school_name}\nContent:\n{programs_content}\n\nOutput JSON only."
    metadata_prompt = f"School: {school_name}\nContent:\n{metadata_content}\n\nOutput JSON only."

    languages_out, in_tok, out_tok = await _run_agent_with_fallback(
        system_prompt=(
            "Extract only teaching/learning languages.\n"
            "Return exactly one JSON object with this shape:\n"
            '{"languages":[{"language":"English","level":null}]}\n'
            "If no language is found, return {\"languages\":[]}.\n"
            "No markdown, no prose, no extra keys."
        ),
        user_prompt=languages_prompt,
        result_type=LanguagesExtractionOutput,
        timeout_seconds=timeout_seconds,
        primary_tier=primary_tier,
        allow_capable_fallback=allow_capable_fallback,
        llm_stats=llm_stats,
    )
    input_tokens += in_tok
    output_tokens += out_tok

    facilities_out, in_tok, out_tok = await _run_agent_with_fallback(
        system_prompt=(
            "Extract only facilities/infrastructure.\n"
            "Return exactly one JSON object with this shape:\n"
            '{"facilities":["library","STEM lab"]}\n'
            "If none are found, return {\"facilities\":[]}.\n"
            "No markdown, no prose, no extra keys."
        ),
        user_prompt=facilities_prompt,
        result_type=FacilitiesExtractionOutput,
        timeout_seconds=timeout_seconds,
        primary_tier=primary_tier,
        allow_capable_fallback=allow_capable_fallback,
        llm_stats=llm_stats,
    )
    input_tokens += in_tok
    output_tokens += out_tok

    programs_out, in_tok, out_tok = await _run_agent_with_fallback(
        system_prompt=(
            "Extract only programs and extracurricular activities.\n"
            "Return exactly one JSON object with this shape:\n"
            '{"programs":["Montessori"],"extracurricular":["Robotics"]}\n'
            "If none are found, return empty lists for both keys.\n"
            "No markdown, no prose, no extra keys."
        ),
        user_prompt=programs_prompt,
        result_type=ProgramsExtractionOutput,
        timeout_seconds=timeout_seconds,
        primary_tier=primary_tier,
        allow_capable_fallback=allow_capable_fallback,
        llm_stats=llm_stats,
    )
    input_tokens += in_tok
    output_tokens += out_tok

    metadata_out, in_tok, out_tok = await _run_agent_with_fallback(
        system_prompt=(
            "Extract only metadata: class_size, founded_year, accreditations.\n"
            "Return exactly one JSON object with this shape:\n"
            '{"class_size":null,"founded_year":null,"accreditations":[]}\n'
            "founded_year should be a 4-digit year string only when the text explicitly says founded/established.\n"
            "class_size should be average/per-class group size (children or students per class/group), not total capacity.\n"
            "accreditations should include only formal accreditation/license/authorization entities.\n"
            "No markdown, no prose, no extra keys."
        ),
        user_prompt=metadata_prompt,
        result_type=MetadataExtractionOutput,
        timeout_seconds=timeout_seconds,
        primary_tier=primary_tier,
        allow_capable_fallback=allow_capable_fallback,
        llm_stats=llm_stats,
    )
    input_tokens += in_tok
    output_tokens += out_tok

    deterministic_languages = _extract_languages_deterministic(
        "\n".join([languages_content, fallback_content]).strip()
    )
    deterministic_founded_year = _extract_founded_year_deterministic(
        "\n".join([metadata_content, fallback_content]).strip()
    )
    deterministic_class_size = _extract_class_size_deterministic(
        "\n".join([metadata_content, fallback_content]).strip()
    )
    deterministic_accreditations = _extract_accreditations_deterministic(
        "\n".join([metadata_content, fallback_content]).strip()
    )

    llm_languages = languages_out.languages if isinstance(languages_out, LanguagesExtractionOutput) else []
    merged_languages = _merge_language_candidates(llm_languages, deterministic_languages)

    llm_founded_year = metadata_out.founded_year if isinstance(metadata_out, MetadataExtractionOutput) else None
    merged_founded_year = _merge_founded_year(llm_founded_year, deterministic_founded_year)
    llm_class_size = metadata_out.class_size if isinstance(metadata_out, MetadataExtractionOutput) else None
    merged_class_size = _merge_class_size(llm_class_size, deterministic_class_size)
    llm_accreditations = metadata_out.accreditations if isinstance(metadata_out, MetadataExtractionOutput) else []
    merged_accreditations = _merge_text_values(llm_accreditations, deterministic_accreditations)

    combined = GeneralInfoExtractionOutput(
        languages=merged_languages,
        facilities=facilities_out.facilities if isinstance(facilities_out, FacilitiesExtractionOutput) else [],
        programs=programs_out.programs if isinstance(programs_out, ProgramsExtractionOutput) else [],
        extracurricular=programs_out.extracurricular if isinstance(programs_out, ProgramsExtractionOutput) else [],
        class_size=merged_class_size,
        founded_year=merged_founded_year,
        accreditations=merged_accreditations,
        has_useful_info=False,
    )
    combined.has_useful_info = _score_general_info_output(combined) > 0

    return combined, input_tokens, output_tokens


def _select_general_info_section_content(
    school: School,
    pages: list[SourcePage],
    section_key: str,
    fallback_content: str,
) -> str:
    section_config = _GENERAL_INFO_SECTION_CONFIG.get(section_key, {})
    preferred_categories = section_config.get("preferred_categories") or ["about", "contact"]
    include_tokens = tuple(section_config.get("include_tokens") or ())
    section_content, _ = _select_pages(
        school=school,
        pages=pages,
        preferred_categories=list(preferred_categories),
        use_case=f"general_{section_key}",
        include_tokens=include_tokens,
    )
    if section_content:
        return section_content
    return fallback_content


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
        has_context = any(marker in lowered_fragment for marker in _LANGUAGE_CONTEXT_MARKERS)
        for label, variants in _DETERMINISTIC_LANGUAGE_PATTERNS:
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
        has_context = any(marker in fragment for marker in _ACCREDITATION_CONTEXT_MARKERS)
        if not has_context:
            continue
        for label, keywords in _ACCREDITATION_KEYWORDS:
            if label in seen:
                continue
            if any(keyword in fragment for keyword in keywords):
                seen.add(label)
                found.append(label)
    return found


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
    for match in _PHONE_PATTERN.finditer(text):
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
    for match in _EMAIL_PATTERN.finditer(text):
        raw = match.group(0).strip()
        # Normalize [at] / [ a t ] → @, collapse spaces around @
        normalized = re.sub(r"\s*\[\s*a\s*t\s*\]\s*", "@", raw, flags=re.IGNORECASE)
        normalized = re.sub(r"\s+", "", normalized).lower()
        # Filter obvious placeholder/noise emails
        if any(noise in normalized for noise in _CONTACT_NOISE_TOKENS):
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


async def _run_agent_with_fallback(
    system_prompt: str,
    user_prompt: str,
    result_type: type,
    timeout_seconds: float,
    primary_tier: str,
    allow_capable_fallback: bool,
    llm_stats: ExtractionLLMStats | None = None,
) -> tuple[Any | None, int, int]:
    """Run configured primary tier first; optionally fallback once to capable tier."""
    settings = get_settings()
    output_retries = max(0, int(settings.extraction_output_retries))
    temperature = float(settings.extraction_temperature)
    min_interval_seconds = max(0.0, float(settings.extraction_request_min_interval_seconds))
    effective_system_prompt = _with_structured_output_contract(system_prompt, result_type)

    async def _run_tier(tier: str, retries: int) -> tuple[Any | None, int, int]:
        if llm_stats is not None:
            llm_stats.total_calls += 1
        try:
            agent = create_agent(
                tier=tier,  # type: ignore[arg-type]
                system_prompt=effective_system_prompt,
                result_type=result_type,
                retries=retries,
                output_retries=output_retries,
                model_settings={"temperature": temperature},
            )
            await _respect_llm_request_interval(min_interval_seconds)
            result = await asyncio.wait_for(agent.run(user_prompt), timeout=timeout_seconds)
            parsed = _parse_llm_output(result, result_type)
            input_tokens, output_tokens = _get_usage(result)
            if parsed is None:
                raise ValueError("Structured parse returned no data")
            return parsed, input_tokens, output_tokens
        except Exception as exc:
            # If strict structured output keeps failing, try one relaxed pass and
            # validate/coerce locally to salvage useful responses.
            if _is_output_validation_error(exc):
                if llm_stats is not None:
                    llm_stats.typed_validation_failures += 1
                    llm_stats.relaxed_parse_attempts += 1
                failure_detail = _build_typed_failure_detail(exc, tier)
                logger.warning("Typed output validation failed on %s tier; attempting relaxed parse", tier)
                # TODO: Investigate why typed outputs fail in benchmarks to reduce relaxed parsing fallback.
                repaired, input_tokens, output_tokens, relaxed_diag = await _run_tier_with_relaxed_output(
                    tier=tier,
                    system_prompt=effective_system_prompt,
                    user_prompt=user_prompt,
                    result_type=result_type,
                    timeout_seconds=timeout_seconds,
                    temperature=temperature,
                )
                if failure_detail:
                    if relaxed_diag:
                        failure_detail.update(relaxed_diag)
                        if llm_stats is not None:
                            llm_stats.add_languages_coercions(
                                int(relaxed_diag.get("languages_coercions_applied", 0) or 0)
                            )
                    if llm_stats is not None:
                        llm_stats.add_typed_failure_detail(failure_detail)
                if repaired is not None:
                    if llm_stats is not None:
                        llm_stats.relaxed_parse_successes += 1
                    return repaired, input_tokens, output_tokens
            if llm_stats is not None and _is_model_or_provider_error(exc):
                llm_stats.provider_failures += 1
            raise

    selected_primary_tier = _normalize_primary_tier(primary_tier)

    try:
        return await _run_tier(selected_primary_tier, retries=1)
    except Exception as primary_error:
        logger.warning("%s extraction failed: %s", selected_primary_tier.capitalize(), primary_error)
        if selected_primary_tier != "medium":
            if _should_try_medium_fallback(selected_primary_tier):
                try:
                    return await _run_tier("medium", retries=1)
                except Exception as medium_error:
                    logger.warning("Medium extraction failed after %s tier: %s", selected_primary_tier, medium_error)
                    if not allow_capable_fallback or not _is_model_or_provider_error(medium_error):
                        if llm_stats is not None:
                            llm_stats.hard_failures += 1
                            llm_stats.add_hard_failure_detail(
                                _build_hard_failure_detail(
                                    medium_error,
                                    stage="primary_to_medium_failed",
                                    tier="medium",
                                    context={
                                        "selected_primary_tier": selected_primary_tier,
                                        "allow_capable_fallback": bool(allow_capable_fallback),
                                    },
                                )
                            )
                        return None, 0, 0
            else:
                logger.debug(
                    "Skipping medium fallback because cheap/medium resolve to same model for primary tier %s",
                    selected_primary_tier,
                )
                if not allow_capable_fallback or not _is_model_or_provider_error(primary_error):
                    if llm_stats is not None:
                        llm_stats.hard_failures += 1
                        llm_stats.add_hard_failure_detail(
                            _build_hard_failure_detail(
                                primary_error,
                                stage="primary_failed_medium_skipped",
                                tier=selected_primary_tier,
                                context={
                                    "selected_primary_tier": selected_primary_tier,
                                    "allow_capable_fallback": bool(allow_capable_fallback),
                                    "reason": "cheap_medium_same_model",
                                },
                            )
                        )
                    return None, 0, 0
        else:
            if not allow_capable_fallback or not _is_model_or_provider_error(primary_error):
                if llm_stats is not None:
                    llm_stats.hard_failures += 1
                    llm_stats.add_hard_failure_detail(
                        _build_hard_failure_detail(
                            primary_error,
                            stage="primary_failed_no_capable_fallback",
                            tier=selected_primary_tier,
                            context={"allow_capable_fallback": bool(allow_capable_fallback)},
                        )
                    )
                return None, 0, 0

    try:
        if llm_stats is not None:
            llm_stats.capable_fallback_attempts += 1
        parsed, input_tokens, output_tokens = await _run_tier("capable", retries=1)
        if llm_stats is not None and parsed is not None:
            llm_stats.capable_fallback_successes += 1
        return parsed, input_tokens, output_tokens
    except Exception as capable_error:
        logger.error("Capable extraction fallback failed: %s", capable_error)
        if llm_stats is not None:
            llm_stats.hard_failures += 1
            llm_stats.add_hard_failure_detail(
                _build_hard_failure_detail(
                    capable_error,
                    stage="capable_fallback_failed",
                    tier="capable",
                    context={"selected_primary_tier": selected_primary_tier},
                )
            )
        return None, 0, 0


async def _run_tier_with_relaxed_output(
    tier: str,
    system_prompt: str,
    user_prompt: str,
    result_type: type,
    timeout_seconds: float,
    temperature: float,
) -> tuple[Any | None, int, int, dict[str, Any]]:
    """Run one untyped call and coerce result to requested schema."""
    settings = get_settings()
    min_interval_seconds = max(0.0, float(settings.extraction_request_min_interval_seconds))
    agent = create_agent(
        tier=tier,  # type: ignore[arg-type]
        system_prompt=system_prompt,
        result_type=str,
        retries=0,
        model_settings={"temperature": temperature},
    )
    await _respect_llm_request_interval(min_interval_seconds)
    result = await asyncio.wait_for(agent.run(user_prompt), timeout=timeout_seconds)
    input_tokens, output_tokens = _get_usage(result)
    raw = _extract_raw_result_payload(result)
    parsed, local_error, coerce_diagnostics = _coerce_and_validate_output_verbose(raw, result_type)
    diagnostics = {
        "relaxed_raw_preview": _safe_preview(raw),
        "relaxed_local_validation_error": local_error,
    }
    diagnostics.update(coerce_diagnostics)
    return parsed, input_tokens, output_tokens, diagnostics


def _parse_llm_output(result: Any, result_type: type) -> Any | None:
    """Read structured output across pydantic-ai versions."""
    raw_output = getattr(result, "output", None)
    if isinstance(raw_output, result_type):
        return raw_output
    parsed_output = _coerce_and_validate_output(raw_output, result_type)
    if parsed_output is not None:
        return parsed_output

    raw_data = getattr(result, "data", None)
    if isinstance(raw_data, result_type):
        return raw_data
    parsed_data = _coerce_and_validate_output(raw_data, result_type)
    if parsed_data is not None:
        return parsed_data

    return None


def _with_structured_output_contract(system_prompt: str, result_type: type) -> str:
    """Append a compact JSON contract to reduce schema-shape drift."""
    contract = _build_output_contract(result_type)
    if not contract:
        return system_prompt
    return (
        f"{system_prompt}\n\n"
        "Strict output requirements:\n"
        "- Return exactly one JSON object.\n"
        "- No markdown fences, prose, explanations, or trailing text.\n"
        "- Use only keys defined in the schema contract below.\n"
        f"{contract}"
    )


def _build_output_contract(result_type: type) -> str:
    if not hasattr(result_type, "model_json_schema"):
        return ""
    try:
        schema = result_type.model_json_schema()
    except Exception:
        return ""

    props = schema.get("properties", {}) if isinstance(schema, dict) else {}
    required = schema.get("required", []) if isinstance(schema, dict) else []
    if not isinstance(props, dict):
        return ""

    lines = ["Schema contract:"]
    if required:
        lines.append(f"- required: {', '.join(str(x) for x in required)}")
    lines.append("- fields:")
    for key, meta in props.items():
        if not isinstance(meta, dict):
            lines.append(f"  - {key}")
            continue
        type_name = meta.get("type")
        if not type_name and "anyOf" in meta:
            variants = [item.get("type", "object") for item in meta.get("anyOf", []) if isinstance(item, dict)]
            type_name = "|".join(str(v) for v in variants if v)
        type_name = str(type_name or "object")
        lines.append(f"  - {key}: {type_name}")
    return "\n".join(lines)


def _extract_raw_result_payload(result: Any) -> Any:
    raw_output = getattr(result, "output", None)
    if raw_output is not None:
        return raw_output
    return getattr(result, "data", None)


async def _respect_llm_request_interval(min_interval_seconds: float) -> None:
    global _LAST_LLM_REQUEST_AT
    if min_interval_seconds <= 0:
        return
    loop = asyncio.get_running_loop()
    async with _REQUEST_THROTTLE_LOCK:
        now = loop.time()
        wait_seconds = min_interval_seconds - (now - _LAST_LLM_REQUEST_AT)
        if wait_seconds > 0:
            await asyncio.sleep(wait_seconds)
        _LAST_LLM_REQUEST_AT = loop.time()


def _coerce_and_validate_output(raw: Any, result_type: type) -> Any | None:
    """Best-effort coercion for malformed-but-salvageable model outputs."""
    parsed, _, _ = _coerce_and_validate_output_verbose(raw, result_type)
    return parsed


def _coerce_and_validate_output_verbose(
    raw: Any,
    result_type: type,
) -> tuple[Any | None, str | None, dict[str, Any]]:
    """Best-effort coercion for malformed-but-salvageable model outputs with diagnostics."""
    diagnostics: dict[str, Any] = {}
    if raw is None:
        return None, "Raw output is None", diagnostics
    try:
        candidate = _coerce_raw_output_to_python(raw)
        if candidate is None:
            return None, "Could not parse raw output into JSON/Python object", diagnostics
        if result_type is GeneralInfoExtractionOutput:
            candidate = _normalize_general_info_payload(candidate)
        elif result_type is LanguagesExtractionOutput:
            candidate, coercions = _normalize_languages_section_payload(candidate)
            diagnostics["languages_coercions_applied"] = coercions
        return result_type.model_validate(candidate), None, diagnostics
    except ValidationError as exc:
        return None, _summarize_validation_error(exc), diagnostics
    except Exception as exc:
        return None, f"{exc.__class__.__name__}: {exc}", diagnostics


def _build_typed_failure_detail(exc: Exception, tier: str) -> dict[str, Any]:
    message = _safe_preview(str(exc), max_chars=400) or _safe_preview(repr(exc), max_chars=400)
    detail: dict[str, Any] = {
        "tier": tier,
        "exception_type": exc.__class__.__name__,
        "exception_message": message,
    }
    body_preview = _extract_exception_body_preview(exc)
    if body_preview:
        detail["exception_body_preview"] = body_preview
    validation_summary = _extract_validation_error_from_exception_chain(exc)
    if validation_summary:
        detail["validation_error"] = validation_summary
    return detail


def _build_hard_failure_detail(
    exc: Exception,
    *,
    stage: str,
    tier: str | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    message = _safe_preview(str(exc), max_chars=400) or _safe_preview(repr(exc), max_chars=400)
    detail: dict[str, Any] = {
        "stage": stage,
        "exception_type": exc.__class__.__name__,
        "exception_message": message,
        "provider_error": _is_model_or_provider_error(exc),
    }
    if tier:
        detail["tier"] = tier
    if context:
        detail["context"] = context

    body_preview = _extract_exception_body_preview(exc)
    if body_preview:
        detail["exception_body_preview"] = body_preview

    validation_summary = _extract_validation_error_from_exception_chain(exc)
    if validation_summary:
        detail["validation_error"] = validation_summary
    return detail


def _extract_exception_body_preview(exc: Exception) -> str | None:
    stack = [exc]
    seen: set[int] = set()
    while stack:
        current = stack.pop()
        current_id = id(current)
        if current_id in seen:
            continue
        seen.add(current_id)

        body = getattr(current, "body", None)
        if body:
            preview = _safe_preview(body)
            if preview:
                return preview

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
    return None


def _extract_validation_error_from_exception_chain(exc: Exception) -> str | None:
    stack = [exc]
    seen: set[int] = set()
    while stack:
        current = stack.pop()
        current_id = id(current)
        if current_id in seen:
            continue
        seen.add(current_id)

        if isinstance(current, ValidationError):
            return _summarize_validation_error(current)

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
    return None


def _summarize_validation_error(exc: ValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return "ValidationError with no detailed errors"
    first = errors[0]
    loc = ".".join(str(part) for part in first.get("loc", [])) or "<root>"
    msg = first.get("msg", "invalid value")
    typ = first.get("type")
    if typ:
        return f"{loc}: {msg} ({typ})"
    return f"{loc}: {msg}"


def _safe_preview(value: Any, max_chars: int = 800) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False)
        except Exception:
            text = str(value)
    text = text.strip()
    if not text:
        return None
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}...<truncated>"


def _coerce_raw_output_to_python(raw: Any) -> Any | None:
    if isinstance(raw, (dict, list)):
        return raw
    if not isinstance(raw, str):
        return raw

    text = raw.strip()
    if not text:
        return None

    # Remove markdown code fences if present.
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    # Try direct JSON parse first.
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Fallback: parse the first JSON object block in mixed content.
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    candidate = text[start : end + 1]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass

    # Last-resort fallback for Python-literal-ish payloads.
    try:
        return ast.literal_eval(candidate)
    except (ValueError, SyntaxError):
        return None


def _normalize_general_info_payload(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return payload

    normalized = dict(payload)
    languages = normalized.get("languages")
    if isinstance(languages, str):
        normalized["languages"] = [{"language": languages, "level": None}]
    elif isinstance(languages, list):
        fixed_languages: list[dict[str, Any]] = []
        for item in languages:
            if isinstance(item, str):
                if item.strip():
                    fixed_languages.append({"language": item.strip(), "level": None})
                continue
            if isinstance(item, dict) and item.get("language"):
                fixed_languages.append(
                    {"language": str(item.get("language")).strip(), "level": item.get("level")}
                )
        normalized["languages"] = fixed_languages

    for key in ("facilities", "programs", "extracurricular", "accreditations"):
        value = normalized.get(key)
        if isinstance(value, str):
            normalized[key] = [value.strip()] if value.strip() else []
        elif isinstance(value, list):
            normalized[key] = [str(item).strip() for item in value if str(item).strip()]

    for key in ("class_size", "founded_year"):
        value = normalized.get(key)
        if value is not None and not isinstance(value, str):
            normalized[key] = str(value)

    return normalized


def _normalize_languages_section_payload(payload: Any) -> tuple[Any, int]:
    """Normalize section-level languages payload to list[{language, level|null}]."""
    if not isinstance(payload, dict):
        return payload, 0

    normalized = dict(payload)
    languages = normalized.get("languages")
    coercions = 0

    if languages is None:
        normalized["languages"] = []
        return normalized, coercions

    if isinstance(languages, str):
        text = languages.strip()
        normalized["languages"] = [{"language": text, "level": None}] if text else []
        return normalized, (1 if text else 0)

    if not isinstance(languages, list):
        text = str(languages).strip()
        normalized["languages"] = [{"language": text, "level": None}] if text else []
        return normalized, (1 if text else 0)

    fixed_languages: list[dict[str, Any]] = []
    for item in languages:
        if isinstance(item, str):
            label = item.strip()
            if label:
                fixed_languages.append({"language": label, "level": None})
                coercions += 1
            continue

        if isinstance(item, dict):
            language_value = item.get("language")
            if not language_value:
                for key in _GENERAL_INFO_TEXT_KEYS:
                    fallback = item.get(key)
                    if fallback:
                        language_value = fallback
                        coercions += 1
                        break

            if not language_value:
                continue

            level_value = item.get("level")
            if level_value is not None and not isinstance(level_value, str):
                level_value = str(level_value)
                coercions += 1

            fixed_languages.append(
                {
                    "language": str(language_value).strip(),
                    "level": (level_value.strip() if isinstance(level_value, str) else level_value) or None,
                }
            )
            continue

        # Rare fallback for numeric/enum-like outputs.
        label = str(item).strip()
        if label:
            fixed_languages.append({"language": label, "level": None})
            coercions += 1

    normalized["languages"] = fixed_languages
    return normalized, coercions


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
    class_size = _normalize_scalar_text(parsed.class_size, _GENERAL_INFO_CLASS_SIZE_MAX_LEN)
    founded_year = _normalize_founded_year(parsed.founded_year)

    primary_lang = _pick_primary_text_lang(
        country_code=country_code,
        values=(
            [entry.language for entry in language_entries]
            + facilities
            + programs
            + extracurricular
            + accreditations
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
                level_text = _sanitize_label(level_candidates[0]) if level_candidates else ""

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
            if len(cleaned) >= _GENERAL_INFO_LIST_MAX_ITEMS:
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
            if len(cleaned) >= _GENERAL_INFO_LIST_MAX_ITEMS:
                return cleaned
    return cleaned


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
        for key in _GENERAL_INFO_TEXT_KEYS:
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


def _sanitize_label(raw_text: str, max_len: int = _GENERAL_INFO_ITEM_MAX_LEN) -> str | None:
    text = str(raw_text).replace("\x00", "").strip()
    if not text:
        return None

    text = re.sub(r"^[\"'`]+|[\"'`]+$", "", text)
    text = re.sub(r"\s+", " ", text).strip(" ,;|")
    if not text:
        return None

    lowered = text.lower()
    if lowered in _GENERAL_INFO_JUNK_VALUES:
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
    return any(marker in message for marker in _PROVIDER_ERROR_MARKERS)


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
        if any(marker in message for marker in _OUTPUT_VALIDATION_ERROR_MARKERS):
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


def _normalize_primary_tier(raw_tier: str) -> str:
    return "cheap" if (raw_tier or "").strip().lower() == "cheap" else "medium"


def _should_try_medium_fallback(selected_primary_tier: str) -> bool:
    """Return whether cheap-tier failures should retry medium tier."""
    if selected_primary_tier == "medium":
        return False
    try:
        cheap_model = (get_model("cheap") or "").strip().lower()
        medium_model = (get_model("medium") or "").strip().lower()
    except Exception:
        # Be conservative if model resolution fails.
        return True
    return cheap_model != medium_model


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
        if normalized_tail in _CATEGORY_ALIASES:
            return _CATEGORY_ALIASES[normalized_tail]
    normalized = _normalize_key(raw)
    return _CATEGORY_ALIASES.get(normalized)


def _coerce_price_period(raw: Optional[str | PricePeriod]) -> Optional[PricePeriod]:
    if raw is None:
        return None
    if isinstance(raw, PricePeriod):
        return raw
    if isinstance(raw, str) and "." in raw:
        tail = raw.split(".")[-1]
        normalized_tail = _normalize_key(tail)
        if normalized_tail in _PERIOD_ALIASES:
            return _PERIOD_ALIASES[normalized_tail]
    normalized = _normalize_key(raw)
    return _PERIOD_ALIASES.get(normalized)


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
