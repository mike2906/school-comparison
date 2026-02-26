"""Compare scraper pipeline v1 vs v2 on real schools.

Runs stage 4+5 for both pipelines with controlled per-school resets and reports:
- quality proxies (useful field coverage, malformed value rate)
- stage success/failure rates
- token and estimated cost deltas
- guardrail verdicts for near-parity acceptance

Usage:
  uv run python scripts/compare_pipeline_v1_v2.py --city sofia --limit 30 --output reports/pipeline_v1_v2.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select

from app.ai.client import MODEL_COSTS
from app.config import get_settings
from app.database import async_session_maker
from app.models import FieldSource, Pricing, School, SourcePage, SourceType
from app.models.pricing import PriceSource
from app.models.scrape_log import ScrapeType
from app.models.source_page import SourcePage as SourcePageModel
from app.scrapers.extractor import extract_school
from app.scrapers.navigator import navigate_school
from app.scrapers.v2.extractor import extract_school_v2
from app.scrapers.v2.navigator import navigate_school_v2

LITE_MODEL = "openrouter/google/gemini-2.5-flash-lite"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare scraper v1 vs v2.")
    parser.add_argument("--country", default="bg")
    parser.add_argument("--city", default="sofia")
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--school-ids", default="")
    parser.add_argument("--output", default="")
    parser.add_argument("--cheap-model", default=LITE_MODEL)
    parser.add_argument("--medium-model", default=LITE_MODEL)
    parser.add_argument("--capable-model", default=LITE_MODEL)
    parser.add_argument("--per-school-timeout", type=float, default=240.0)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--spot-check-count", type=int, default=10)
    parser.add_argument("--openrouter-models", default="")
    parser.add_argument("--openrouter-provider-order", default="")
    parser.add_argument("--openrouter-provider-sort", default="")
    parser.add_argument("--openrouter-provider-allow-fallbacks", choices=["true", "false"], default="true")
    return parser.parse_args()


def _configure_models(
    cheap_model: str,
    medium_model: str,
    capable_model: str,
    *,
    openrouter_models: str,
    openrouter_provider_order: str,
    openrouter_provider_sort: str,
    openrouter_provider_allow_fallbacks: str,
) -> dict[str, str]:
    os.environ["MODEL_TIER_CHEAP"] = cheap_model
    os.environ["MODEL_TIER_MEDIUM"] = medium_model
    os.environ["MODEL_TIER_CAPABLE"] = capable_model
    os.environ["EXTRACTION_PRIMARY_TIER"] = "cheap"
    os.environ["EXTRACTION_ENABLE_CAPABLE_FALLBACK"] = "false"
    os.environ["EXTRACTION_OPENROUTER_MODELS"] = openrouter_models
    os.environ["EXTRACTION_OPENROUTER_PROVIDER_ORDER"] = openrouter_provider_order
    os.environ["EXTRACTION_OPENROUTER_PROVIDER_SORT"] = openrouter_provider_sort
    os.environ["EXTRACTION_OPENROUTER_PROVIDER_ALLOW_FALLBACKS"] = openrouter_provider_allow_fallbacks
    get_settings.cache_clear()
    return {
        "model_tier_cheap": cheap_model,
        "model_tier_medium": medium_model,
        "model_tier_capable": capable_model,
        "extraction_openrouter_models": openrouter_models,
        "extraction_openrouter_provider_order": openrouter_provider_order,
        "extraction_openrouter_provider_sort": openrouter_provider_sort,
        "extraction_openrouter_provider_allow_fallbacks": openrouter_provider_allow_fallbacks,
    }


def _is_malformed_value(value: Any) -> bool:
    if isinstance(value, dict):
        return False
    if isinstance(value, list):
        return False

    text = str(value).strip()
    if not text:
        return False
    if (text.startswith("{") and text.endswith("}")) or (text.startswith("[") and text.endswith("]")):
        return True
    if len(text) > 120:
        return True
    return False


def _compute_metrics(attributes: dict[str, Any]) -> dict[str, Any]:
    extracted = attributes.get("extracted") if isinstance(attributes, dict) else {}
    if not isinstance(extracted, dict):
        extracted = {}

    list_fields = {
        "languages": extracted.get("languages", []),
        "facilities": extracted.get("facilities", []),
        "programs": extracted.get("programs", []),
        "extracurricular": extracted.get("extracurricular", []),
        "accreditations": extracted.get("accreditations", []),
    }
    scalar_fields = {
        "class_size": extracted.get("class_size"),
        "founded_year": extracted.get("founded_year"),
    }

    useful_fields = 0
    malformed_total = 0
    item_total = 0

    for values in list_fields.values():
        if isinstance(values, list) and values:
            useful_fields += 1
        if not isinstance(values, list):
            continue
        for value in values:
            item_total += 1
            if _is_malformed_value(value):
                malformed_total += 1

    for value in scalar_fields.values():
        if str(value or "").strip():
            useful_fields += 1
            item_total += 1
            if _is_malformed_value(value):
                malformed_total += 1

    contact = extracted.get("contact") or {}
    if isinstance(contact, dict) and (contact.get("phones") or contact.get("emails")):
        useful_fields += 1

    malformed_rate = (malformed_total / item_total) if item_total else 0.0
    return {
        "useful_fields": useful_fields,
        "malformed_rate": round(malformed_rate, 4),
        "item_total": item_total,
        "malformed_total": malformed_total,
    }


def _estimate_cost(input_tokens: int, output_tokens: int) -> float:
    in_cost, out_cost = MODEL_COSTS["cheap"]
    return (input_tokens / 1_000_000 * in_cost) + (output_tokens / 1_000_000 * out_cost)


def _resolve_row_cost(
    *,
    ext_result: dict[str, Any],
    input_tokens: int,
    output_tokens: int,
) -> tuple[float, str]:
    raw_cost = ext_result.get("token_cost_usd")
    if raw_cost is not None:
        try:
            return round(float(raw_cost), 6), "extractor_reported"
        except (TypeError, ValueError):
            pass
    return round(_estimate_cost(input_tokens, output_tokens), 6), "estimated_cheap"


def _to_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    enum_value = getattr(value, "value", None)
    if isinstance(enum_value, (str, int, float, bool)):
        return enum_value
    return str(value)


def _build_field_snapshot(attributes: dict[str, Any], admission_info: dict[str, Any]) -> dict[str, Any]:
    extracted = attributes.get("extracted") if isinstance(attributes, dict) else {}
    if not isinstance(extracted, dict):
        extracted = {}

    def _list(value: Any) -> list[Any]:
        return value if isinstance(value, list) else []

    languages = _list(extracted.get("languages"))
    facilities = _list(extracted.get("facilities"))
    programs = _list(extracted.get("programs"))
    extracurricular = _list(extracted.get("extracurricular"))
    accreditations = _list(extracted.get("accreditations"))
    class_size = extracted.get("class_size")
    founded_year = extracted.get("founded_year")
    contact = extracted.get("contact") if isinstance(extracted.get("contact"), dict) else {}
    admission = extracted.get("admission") if isinstance(extracted.get("admission"), dict) else {}
    operations = extracted.get("operations") if isinstance(extracted.get("operations"), dict) else {}
    services = extracted.get("services") if isinstance(extracted.get("services"), dict) else {}
    pricing_terms = extracted.get("pricing_terms") if isinstance(extracted.get("pricing_terms"), dict) else {}
    website_extracted = (
        admission_info.get("website_extracted")
        if isinstance(admission_info, dict) and isinstance(admission_info.get("website_extracted"), dict)
        else {}
    )

    field_presence = {
        "languages": bool(languages),
        "facilities": bool(facilities),
        "programs": bool(programs),
        "extracurricular": bool(extracurricular),
        "accreditations": bool(accreditations),
        "class_size": bool(str(class_size or "").strip()),
        "founded_year": bool(str(founded_year or "").strip()),
        "contact": bool(contact),
        "admission": bool(admission),
        "operations": bool(operations),
        "services": bool(services),
        "pricing_terms": bool(pricing_terms),
        "admission_website_extracted": bool(website_extracted),
    }
    field_counts = {
        "languages": len(languages),
        "facilities": len(facilities),
        "programs": len(programs),
        "extracurricular": len(extracurricular),
        "accreditations": len(accreditations),
    }

    return {
        "presence": field_presence,
        "counts": field_counts,
        "values": _to_jsonable(
            {
                "languages": languages,
                "facilities": facilities,
                "programs": programs,
                "extracurricular": extracurricular,
                "accreditations": accreditations,
                "class_size": class_size,
                "founded_year": founded_year,
                "contact": contact,
                "admission": admission,
                "operations": operations,
                "services": services,
                "pricing_terms": pricing_terms,
                "admission_website_extracted": website_extracted,
            }
        ),
    }


async def _build_pricing_snapshot(db, school_id: int) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            select(Pricing).where(
                Pricing.school_id == school_id,
                Pricing.source == PriceSource.SCRAPED_WEBSITE,
            )
        )
    ).scalars().all()

    normalized_rows: list[dict[str, Any]] = []
    for row in rows:
        normalized_rows.append(
            {
                "category": row.category.value if row.category else None,
                "period": row.period.value if row.period else None,
                "amount": float(row.amount) if row.amount is not None else None,
                "amount_min": float(row.amount_min) if row.amount_min is not None else None,
                "amount_max": float(row.amount_max) if row.amount_max is not None else None,
                "currency": row.currency,
                "academic_year": row.academic_year,
                "age_group": row.age_group,
                "plan_name": row.plan_name,
                "source_url": row.source_url,
                "pricing_context": _to_jsonable(row.pricing_context),
            }
        )
    normalized_rows.sort(
        key=lambda item: (
            str(item.get("category") or ""),
            str(item.get("period") or ""),
            str(item.get("academic_year") or ""),
            str(item.get("age_group") or ""),
            float(item.get("amount") or 0.0),
        )
    )
    return normalized_rows


def _llm_stat(stats: dict[str, Any], key: str) -> int:
    value = stats.get(key, 0)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _build_llm_row_fields(llm_stats: dict[str, Any]) -> dict[str, int]:
    return {
        "llm_total_calls": _llm_stat(llm_stats, "total_calls"),
        "llm_hard_failures": _llm_stat(llm_stats, "hard_failures"),
        "llm_provider_failures": _llm_stat(llm_stats, "provider_failures"),
        "llm_model_retries": _llm_stat(llm_stats, "model_retries"),
        "llm_typed_validation_failures": _llm_stat(llm_stats, "typed_validation_failures"),
        "llm_relaxed_parse_attempts": _llm_stat(llm_stats, "relaxed_parse_attempts"),
        "llm_relaxed_parse_successes": _llm_stat(llm_stats, "relaxed_parse_successes"),
        "llm_capable_fallback_attempts": _llm_stat(llm_stats, "capable_fallback_attempts"),
        "llm_capable_fallback_successes": _llm_stat(llm_stats, "capable_fallback_successes"),
    }


def _compute_guardrail_verdict(v1: dict[str, Any], v2: dict[str, Any], code_reduction_ratio: float) -> dict[str, Any]:
    quality_ratio = (v2["avg_useful_fields"] / v1["avg_useful_fields"]) if v1["avg_useful_fields"] > 0 else 1.0
    failure_rate_diff = v2["failure_rate"] - v1["failure_rate"]
    token_ratio = (v2["total_tokens"] / v1["total_tokens"]) if v1["total_tokens"] > 0 else 1.0

    passes = (
        quality_ratio >= 0.90
        and failure_rate_diff <= 0.05
        and token_ratio <= 1.35
    )

    clean_win = passes and code_reduction_ratio >= 0.40

    return {
        "passes_guardrails": passes,
        "clean_win": clean_win,
        "quality_ratio": round(quality_ratio, 4),
        "failure_rate_diff": round(failure_rate_diff, 4),
        "token_ratio": round(token_ratio, 4),
        "code_reduction_ratio": round(code_reduction_ratio, 4),
        "guardrails": {
            "min_quality_ratio": 0.90,
            "max_failure_rate_diff": 0.05,
            "max_token_ratio": 1.35,
            "min_code_reduction_for_clean_win": 0.40,
        },
    }


async def _select_school_ids(country: str, city: str, limit: int, school_ids_csv: str) -> list[int]:
    if school_ids_csv.strip():
        return [int(part.strip()) for part in school_ids_csv.split(",") if part.strip()]

    async with async_session_maker() as db:
        stmt = (
            select(School.id)
            .where(
                School.country_code == country,
                School.city == city,
                School.website_url.isnot(None),
            )
            .order_by(School.school_type.asc(), School.id.asc())
            .limit(limit)
        )
        result = await db.execute(stmt)
        return [row[0] for row in result.all()]


async def _reset_school_stage_state(db, school_id: int) -> None:
    school = (await db.execute(select(School).where(School.id == school_id))).scalar_one()

    await db.execute(
        delete(SourcePageModel).where(
            SourcePageModel.school_id == school_id,
            SourcePageModel.scrape_type == ScrapeType.WEBSITE,
        )
    )
    await db.execute(
        delete(Pricing).where(
            Pricing.school_id == school_id,
            Pricing.source == PriceSource.SCRAPED_WEBSITE,
        )
    )
    await db.execute(
        delete(FieldSource).where(
            FieldSource.school_id == school_id,
            FieldSource.source_type == SourceType.SCRAPED_WEBSITE,
        )
    )

    attrs = dict(school.attributes) if isinstance(school.attributes, dict) else {}
    attrs.pop("_extraction_hashes", None)
    attrs.pop("extracted", None)
    attrs.pop("extracted_i18n", None)
    school.attributes = attrs

    admission_info = dict(school.admission_info) if isinstance(school.admission_info, dict) else {}
    admission_info.pop("website_extracted", None)
    school.admission_info = admission_info

    school.scrape_status = "validated"
    await db.commit()


async def _run_variant_once(
    variant: str,
    school_id: int,
    country: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    async with async_session_maker() as db:
        await _reset_school_stage_state(db, school_id)

        start = time.perf_counter()
        if variant == "v1":
            nav_coro = navigate_school(db=db, school_id=school_id, country_code=country)
        else:
            nav_coro = navigate_school_v2(db=db, school_id=school_id, country_code=country)

        try:
            nav = await asyncio.wait_for(nav_coro, timeout=timeout_seconds)
        except asyncio.TimeoutError:
            return {
                "school_id": school_id,
                "variant": variant,
                "status": "timeout",
                "navigate_success": False,
                "extract_status": "timeout",
                "runtime_seconds": round(time.perf_counter() - start, 3),
                "input_tokens": 0,
                "output_tokens": 0,
                "token_cost_usd": 0.0,
                "token_cost_source": "none",
                "pricing_success": False,
                "general_info_success": False,
                "useful_fields": 0,
                "malformed_rate": 0.0,
                **_build_llm_row_fields({}),
            }

        if not nav.get("success"):
            return {
                "school_id": school_id,
                "variant": variant,
                "status": "navigate_failed",
                "navigate_success": False,
                "extract_status": "not_run",
                "runtime_seconds": round(time.perf_counter() - start, 3),
                "input_tokens": 0,
                "output_tokens": 0,
                "token_cost_usd": 0.0,
                "token_cost_source": "none",
                "pricing_success": False,
                "general_info_success": False,
                "useful_fields": 0,
                "malformed_rate": 0.0,
                "reason": nav.get("reason"),
                **_build_llm_row_fields({}),
            }

        if variant == "v1":
            ext_coro = extract_school(db=db, school_id=school_id, country_code=country)
        else:
            ext_coro = extract_school_v2(db=db, school_id=school_id, country_code=country)

        try:
            ext = await asyncio.wait_for(ext_coro, timeout=timeout_seconds)
        except asyncio.TimeoutError:
            return {
                "school_id": school_id,
                "variant": variant,
                "status": "timeout",
                "navigate_success": True,
                "extract_status": "timeout",
                "runtime_seconds": round(time.perf_counter() - start, 3),
                "input_tokens": 0,
                "output_tokens": 0,
                "token_cost_usd": 0.0,
                "token_cost_source": "none",
                "pricing_success": False,
                "general_info_success": False,
                "useful_fields": 0,
                "malformed_rate": 0.0,
                **_build_llm_row_fields({}),
            }

        school = (await db.execute(select(School).where(School.id == school_id))).scalar_one()
        school_attributes = school.attributes if isinstance(school.attributes, dict) else {}
        school_admission_info = school.admission_info if isinstance(school.admission_info, dict) else {}
        metrics = _compute_metrics(school_attributes)
        input_tokens = int(ext.get("input_tokens", 0) or 0)
        output_tokens = int(ext.get("output_tokens", 0) or 0)
        llm_stats = ext.get("llm_stats") if isinstance(ext.get("llm_stats"), dict) else {}
        field_snapshot = _build_field_snapshot(school_attributes, school_admission_info)
        pricing_snapshot = await _build_pricing_snapshot(db, school_id)

        token_cost_usd, token_cost_source = _resolve_row_cost(
            ext_result=ext,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

        return {
            "school_id": school_id,
            "variant": variant,
            "status": ext.get("status"),
            "navigate_success": bool(nav.get("success")),
            "extract_status": ext.get("status"),
            "runtime_seconds": round(time.perf_counter() - start, 3),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "token_cost_usd": token_cost_usd,
            "token_cost_source": token_cost_source,
            "pricing_success": bool(ext.get("pricing_success")),
            "general_info_success": bool(ext.get("general_info_success")),
            "useful_fields": metrics["useful_fields"],
            "malformed_rate": metrics["malformed_rate"],
            "item_total": metrics["item_total"],
            "malformed_total": metrics["malformed_total"],
            "details": ext.get("details", []),
            "field_snapshot": field_snapshot,
            "pricing_snapshot": pricing_snapshot,
            **_build_llm_row_fields(llm_stats),
        }


async def _run_variant_batch(
    *,
    variant: str,
    school_ids: list[int],
    country: str,
    timeout_seconds: float,
    concurrency: int,
) -> list[dict[str, Any]]:
    if not school_ids:
        return []

    capped_concurrency = max(1, int(concurrency))
    if capped_concurrency == 1:
        rows: list[dict[str, Any]] = []
        for school_id in school_ids:
            print(f"- School {school_id}: running {variant}")
            rows.append(await _run_variant_once(variant, school_id, country, timeout_seconds))
        return rows

    semaphore = asyncio.Semaphore(capped_concurrency)
    ordered_rows: list[dict[str, Any] | None] = [None] * len(school_ids)

    async def _run_indexed(index: int, school_id: int) -> tuple[int, dict[str, Any]]:
        async with semaphore:
            print(f"- School {school_id}: running {variant}")
            row = await _run_variant_once(variant, school_id, country, timeout_seconds)
            return index, row

    tasks = [asyncio.create_task(_run_indexed(i, school_id)) for i, school_id in enumerate(school_ids)]
    for completed in asyncio.as_completed(tasks):
        idx, row = await completed
        ordered_rows[idx] = row

    return [row for row in ordered_rows if row is not None]


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    extracted_count = sum(1 for row in rows if row.get("extract_status") == "extracted")
    failure_count = total - extracted_count
    pricing_success_rate = (
        sum(1 for row in rows if row.get("pricing_success")) / total if total else 0.0
    )
    general_info_success_rate = (
        sum(1 for row in rows if row.get("general_info_success")) / total if total else 0.0
    )

    total_input_tokens = sum(int(row.get("input_tokens", 0) or 0) for row in rows)
    total_output_tokens = sum(int(row.get("output_tokens", 0) or 0) for row in rows)
    total_tokens = total_input_tokens + total_output_tokens
    total_llm_calls = sum(int(row.get("llm_total_calls", 0) or 0) for row in rows)
    total_llm_hard_failures = sum(int(row.get("llm_hard_failures", 0) or 0) for row in rows)
    total_llm_provider_failures = sum(int(row.get("llm_provider_failures", 0) or 0) for row in rows)
    total_llm_model_retries = sum(int(row.get("llm_model_retries", 0) or 0) for row in rows)
    total_typed_validation_failures = sum(
        int(row.get("llm_typed_validation_failures", 0) or 0) for row in rows
    )
    total_relaxed_attempts = sum(int(row.get("llm_relaxed_parse_attempts", 0) or 0) for row in rows)
    total_relaxed_successes = sum(int(row.get("llm_relaxed_parse_successes", 0) or 0) for row in rows)
    total_capable_fallback_attempts = sum(
        int(row.get("llm_capable_fallback_attempts", 0) or 0) for row in rows
    )
    total_capable_fallback_successes = sum(
        int(row.get("llm_capable_fallback_successes", 0) or 0) for row in rows
    )
    llm_hard_failure_rate = (
        float(total_llm_hard_failures) / float(total_llm_calls) if total_llm_calls else 0.0
    )
    relaxed_parse_success_rate = (
        float(total_relaxed_successes) / float(total_relaxed_attempts) if total_relaxed_attempts else 0.0
    )

    return {
        "schools": total,
        "extracted_count": extracted_count,
        "failure_count": failure_count,
        "failure_rate": round((failure_count / total) if total else 0.0, 4),
        "avg_runtime_seconds": round(
            (sum(float(row.get("runtime_seconds", 0.0) or 0.0) for row in rows) / total) if total else 0.0,
            3,
        ),
        "avg_useful_fields": round(
            (sum(float(row.get("useful_fields", 0.0) or 0.0) for row in rows) / total) if total else 0.0,
            3,
        ),
        "avg_malformed_rate": round(
            (sum(float(row.get("malformed_rate", 0.0) or 0.0) for row in rows) / total) if total else 0.0,
            4,
        ),
        "pricing_success_rate": round(pricing_success_rate, 4),
        "general_info_success_rate": round(general_info_success_rate, 4),
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
        "total_tokens": total_tokens,
        "total_token_cost_usd": round(sum(float(row.get("token_cost_usd", 0.0) or 0.0) for row in rows), 6),
        "llm": {
            "total_calls": total_llm_calls,
            "hard_failures": total_llm_hard_failures,
            "hard_failure_rate": round(llm_hard_failure_rate, 4),
            "provider_failures": total_llm_provider_failures,
            "model_retries": total_llm_model_retries,
            "typed_validation_failures": total_typed_validation_failures,
            "relaxed_parse_attempts": total_relaxed_attempts,
            "relaxed_parse_successes": total_relaxed_successes,
            "relaxed_parse_success_rate": round(relaxed_parse_success_rate, 4),
            "capable_fallback_attempts": total_capable_fallback_attempts,
            "capable_fallback_successes": total_capable_fallback_successes,
        },
    }


def _select_spot_check_candidates(
    v1_rows: list[dict[str, Any]],
    v2_rows: list[dict[str, Any]],
    top_n: int,
) -> list[dict[str, Any]]:
    by_school_v1 = {row["school_id"]: row for row in v1_rows}
    by_school_v2 = {row["school_id"]: row for row in v2_rows}

    candidates: list[dict[str, Any]] = []
    for school_id in sorted(set(by_school_v1) & set(by_school_v2)):
        row_v1 = by_school_v1[school_id]
        row_v2 = by_school_v2[school_id]
        useful_delta = abs(float(row_v1.get("useful_fields", 0) or 0) - float(row_v2.get("useful_fields", 0) or 0))
        malformed_delta = abs(float(row_v1.get("malformed_rate", 0.0) or 0.0) - float(row_v2.get("malformed_rate", 0.0) or 0.0))
        token_delta = abs(int(row_v1.get("input_tokens", 0) or 0) - int(row_v2.get("input_tokens", 0) or 0))
        divergence_score = (useful_delta * 10.0) + (malformed_delta * 100.0) + (token_delta / 1000.0)

        candidates.append(
            {
                "school_id": school_id,
                "divergence_score": round(divergence_score, 4),
                "v1_status": row_v1.get("extract_status"),
                "v2_status": row_v2.get("extract_status"),
                "v1_useful_fields": row_v1.get("useful_fields"),
                "v2_useful_fields": row_v2.get("useful_fields"),
                "v1_malformed_rate": row_v1.get("malformed_rate"),
                "v2_malformed_rate": row_v2.get("malformed_rate"),
            }
        )

    candidates.sort(key=lambda item: item["divergence_score"], reverse=True)
    return candidates[: max(1, int(top_n))]


def _count_loc(path: Path) -> int:
    if not path.exists():
        print(f"[WARN] LOC file not found: {path}")
        return 0
    with path.open("r", encoding="utf-8") as handle:
        return sum(1 for _ in handle)


async def main() -> int:
    args = _parse_args()
    model_profile = _configure_models(
        cheap_model=args.cheap_model,
        medium_model=args.medium_model,
        capable_model=args.capable_model,
        openrouter_models=args.openrouter_models,
        openrouter_provider_order=args.openrouter_provider_order,
        openrouter_provider_sort=args.openrouter_provider_sort,
        openrouter_provider_allow_fallbacks=args.openrouter_provider_allow_fallbacks,
    )

    school_ids = await _select_school_ids(args.country, args.city, args.limit, args.school_ids)
    if not school_ids:
        print("No candidate schools found.")
        return 1

    print(f"Comparing v1 vs v2 for {len(school_ids)} schools: {school_ids}")

    timeout = max(30.0, float(args.per_school_timeout))
    requested_concurrency = max(1, int(args.concurrency))
    max_concurrency = 8
    concurrency = min(requested_concurrency, max_concurrency)
    print(f"Variant batch concurrency: {concurrency}")
    if requested_concurrency > max_concurrency:
        print(f"Requested concurrency {requested_concurrency} capped to {max_concurrency}")

    # Keep variants isolated to avoid shared stage-state races.
    v1_rows = await _run_variant_batch(
        variant="v1",
        school_ids=school_ids,
        country=args.country,
        timeout_seconds=timeout,
        concurrency=concurrency,
    )
    v2_rows = await _run_variant_batch(
        variant="v2",
        school_ids=school_ids,
        country=args.country,
        timeout_seconds=timeout,
        concurrency=concurrency,
    )

    v1_summary = _summarize(v1_rows)
    v2_summary = _summarize(v2_rows)

    repo_root = Path(__file__).resolve().parents[1]
    v1_loc = _count_loc(repo_root / "app" / "scrapers" / "navigator.py") + _count_loc(repo_root / "app" / "scrapers" / "extractor.py")
    v2_loc = _count_loc(repo_root / "app" / "scrapers" / "v2" / "navigator.py") + _count_loc(repo_root / "app" / "scrapers" / "v2" / "extractor.py")
    code_reduction_ratio = (1.0 - (v2_loc / v1_loc)) if v1_loc > 0 else 0.0

    verdict = _compute_guardrail_verdict(v1_summary, v2_summary, code_reduction_ratio)
    spot_checks = _select_spot_check_candidates(v1_rows, v2_rows, args.spot_check_count)

    report = {
        "config": {
            "country": args.country,
            "city": args.city,
            "school_ids": school_ids,
            "concurrency": concurrency,
            "model_profile": model_profile,
            "guardrails": verdict["guardrails"],
        },
        "summary": {
            "v1": v1_summary,
            "v2": v2_summary,
            "code_footprint": {
                "v1_loc": v1_loc,
                "v2_loc": v2_loc,
                "code_reduction_ratio": round(code_reduction_ratio, 4),
            },
            "verdict": verdict,
            "spot_check_candidates": spot_checks,
        },
        "details": {
            "v1": v1_rows,
            "v2": v2_rows,
        },
    }

    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Wrote report: {output_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
