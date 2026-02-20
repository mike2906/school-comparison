"""A/B compare navigator fetch/content extractor combinations on real schools.

This script reruns navigation + extraction for each selected school across:
- NAV_FETCH_ENGINE modes: httpx, crawl4ai
- NAV_CONTENT_EXTRACTOR modes: bs4, trafilatura, crawl4ai

It reports:
- non-empty useful field count
- malformed value rate (stringified dict/list style values)
- LLM token usage

Usage:
  uv run python scripts/compare_content_extractors.py --city sofia --limit 20 --modes bs4,trafilatura
  uv run python scripts/compare_content_extractors.py --city sofia --limit 20 --fetch-engines httpx,crawl4ai --modes bs4,crawl4ai
  uv run python scripts/compare_content_extractors.py --school-ids 12,34,56 --output reports/extractor_ab.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import select

from app.config import get_settings
from app.database import async_session_maker
from app.models import School
from app.scrapers.extractor import extract_school
from app.scrapers.navigator import navigate_school


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare navigation/extraction modes.")
    parser.add_argument("--country", default="bg")
    parser.add_argument("--city", default="sofia")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--school-ids", default="")
    parser.add_argument("--output", default="")
    parser.add_argument(
        "--modes",
        default="bs4,trafilatura",
        help="Comma-separated extractor modes, e.g. bs4,trafilatura,crawl4ai",
    )
    parser.add_argument(
        "--fetch-engines",
        default="httpx",
        help="Comma-separated fetch engines, e.g. httpx,crawl4ai,crawl4ai_deep",
    )
    parser.add_argument(
        "--school-timeout-seconds",
        type=float,
        default=180.0,
        help="Per-school hard timeout for navigation+extraction. Use 0 to disable.",
    )
    parser.add_argument(
        "--checkpoint-every",
        type=int,
        default=1,
        help="Write checkpoint output every N schools (requires --output).",
    )
    parser.add_argument(
        "--disable-refresh-on-empty-content",
        action="store_true",
        help="Do not run extraction-side navigation refresh when all cached page content is empty.",
    )
    parser.add_argument(
        "--skip-navigation",
        action="store_true",
        help="Skip navigate_school and only run extraction against already cached pages.",
    )
    return parser.parse_args()


def _is_malformed_value(value: Any) -> bool:
    # Structured language objects are expected and should not count as malformed.
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
    if re.fullmatch(r"[\W_]+", text):
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

    # Contact info (deterministic extraction - phones or emails)
    contact = extracted.get("contact") or {}
    if isinstance(contact, dict) and (contact.get("phones") or contact.get("emails")):
        useful_fields += 1

    malformed_rate = (malformed_total / item_total) if item_total else 0.0
    return {
        "useful_fields": useful_fields,
        "malformed_rate": round(malformed_rate, 4),
        "item_total": item_total,
        "malformed_total": malformed_total,
        "extracted": extracted,
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
                School.scrape_status.in_(["validated", "navigated", "extracted", "extraction_failed"]),
            )
            .limit(limit)
        )
        result = await db.execute(stmt)
        return [row[0] for row in result.all()]


def _summarize_mode_result(fetch_engine: str, mode: str, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "fetch_engine": fetch_engine,
        "mode": mode,
        "avg_useful_fields": result["avg_useful_fields"],
        "avg_malformed_rate": result["avg_malformed_rate"],
        "total_input_tokens": result["total_input_tokens"],
        "total_output_tokens": result["total_output_tokens"],
        "llm_total_calls": result["llm_total_calls"],
        "llm_typed_validation_failures": result["llm_typed_validation_failures"],
        "llm_typed_validation_failure_rate": result["llm_typed_validation_failure_rate"],
        "llm_relaxed_parse_success_rate": result["llm_relaxed_parse_success_rate"],
        "llm_hard_failures": result["llm_hard_failures"],
        "llm_hard_failure_rate": result["llm_hard_failure_rate"],
    }


def _write_output_file(
    output_path: Path | None,
    report: dict[str, Any],
    summary: dict[str, Any],
    in_progress: dict[str, Any] | None = None,
) -> None:
    if not output_path:
        return
    partial_path = output_path.with_suffix(output_path.suffix + ".partial")
    partial_path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {"summary": summary, "report": report}
    if in_progress:
        payload["_in_progress"] = in_progress
    temp_path = partial_path.with_suffix(partial_path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(partial_path)


async def _run_school_once(
    school_id: int,
    country: str,
    allow_navigation_refresh_on_empty: bool,
    skip_navigation: bool,
) -> dict[str, Any]:
    async with async_session_maker() as db:
        if skip_navigation:
            nav_result = {"success": True, "skipped": True}
        else:
            nav_result = await navigate_school(db=db, school_id=school_id, country_code=country)
        ext_result = await extract_school(
            db=db,
            school_id=school_id,
            country_code=country,
            allow_navigation_refresh_on_empty=allow_navigation_refresh_on_empty,
        )

        school = (await db.execute(select(School).where(School.id == school_id))).scalar_one_or_none()
        # Keep benchmark scoring fair: failed/skipped extraction runs should not inherit
        # previously extracted values persisted from earlier mode runs.
        if ext_result.get("status") == "extracted":
            metrics_source = school.attributes if school and isinstance(school.attributes, dict) else {}
        else:
            metrics_source = {}
        metrics = _compute_metrics(metrics_source)

        input_tokens = int(ext_result.get("input_tokens", 0) or 0)
        output_tokens = int(ext_result.get("output_tokens", 0) or 0)
        llm_stats = ext_result.get("llm_stats") if isinstance(ext_result, dict) else None
        if not isinstance(llm_stats, dict):
            llm_stats = {}

        return {
            "school_id": school_id,
            "navigate_success": bool(nav_result.get("success")),
            "extract_status": ext_result.get("status"),
            "extract_skipped": bool(ext_result.get("skipped")),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "llm_stats": llm_stats,
            **metrics,
        }


def _build_mode_result(
    mode: str,
    fetch_engine: str,
    per_school: list[dict[str, Any]],
) -> dict[str, Any]:
    total_input_tokens = sum(int(item.get("input_tokens", 0) or 0) for item in per_school)
    total_output_tokens = sum(int(item.get("output_tokens", 0) or 0) for item in per_school)
    llm_total_calls = sum(int((item.get("llm_stats") or {}).get("total_calls", 0) or 0) for item in per_school)
    llm_typed_validation_failures = sum(
        int((item.get("llm_stats") or {}).get("typed_validation_failures", 0) or 0) for item in per_school
    )
    llm_relaxed_parse_attempts = sum(
        int((item.get("llm_stats") or {}).get("relaxed_parse_attempts", 0) or 0) for item in per_school
    )
    llm_relaxed_parse_successes = sum(
        int((item.get("llm_stats") or {}).get("relaxed_parse_successes", 0) or 0) for item in per_school
    )
    llm_hard_failures = sum(int((item.get("llm_stats") or {}).get("hard_failures", 0) or 0) for item in per_school)
    avg_useful = sum(float(item.get("useful_fields", 0) or 0) for item in per_school) / len(per_school) if per_school else 0.0
    avg_malformed_rate = (
        sum(float(item.get("malformed_rate", 0) or 0) for item in per_school) / len(per_school) if per_school else 0.0
    )
    return {
        "mode": mode,
        "fetch_engine": fetch_engine,
        "schools": len(per_school),
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
        "llm_total_calls": llm_total_calls,
        "llm_typed_validation_failures": llm_typed_validation_failures,
        "llm_typed_validation_failure_rate": round(
            (llm_typed_validation_failures / llm_total_calls) if llm_total_calls else 0.0,
            4,
        ),
        "llm_relaxed_parse_attempts": llm_relaxed_parse_attempts,
        "llm_relaxed_parse_successes": llm_relaxed_parse_successes,
        "llm_relaxed_parse_success_rate": round(
            (llm_relaxed_parse_successes / llm_relaxed_parse_attempts) if llm_relaxed_parse_attempts else 0.0,
            4,
        ),
        "llm_hard_failures": llm_hard_failures,
        "llm_hard_failure_rate": round((llm_hard_failures / llm_total_calls) if llm_total_calls else 0.0, 4),
        "avg_useful_fields": round(avg_useful, 3),
        "avg_malformed_rate": round(avg_malformed_rate, 4),
        "details": per_school,
    }


async def _run_mode(
    mode: str,
    fetch_engine: str,
    school_ids: list[int],
    country: str,
    school_timeout_seconds: float,
    checkpoint_every: int,
    allow_navigation_refresh_on_empty: bool,
    skip_navigation: bool,
    progress_callback: Callable[[int, dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    os.environ["NAV_CONTENT_EXTRACTOR"] = mode
    os.environ["NAV_FETCH_ENGINE"] = fetch_engine
    get_settings.cache_clear()

    per_school: list[dict[str, Any]] = []
    effective_checkpoint_every = max(1, int(checkpoint_every))

    for index, school_id in enumerate(school_ids, start=1):
        try:
            school_coro = _run_school_once(
                school_id=school_id,
                country=country,
                allow_navigation_refresh_on_empty=allow_navigation_refresh_on_empty,
                skip_navigation=skip_navigation,
            )
            if school_timeout_seconds > 0:
                row = await asyncio.wait_for(school_coro, timeout=school_timeout_seconds)
            else:
                row = await school_coro
        except asyncio.TimeoutError:
            row = {
                "school_id": school_id,
                "navigate_success": False,
                "extract_status": "benchmark_timeout",
                "extract_skipped": False,
                "input_tokens": 0,
                "output_tokens": 0,
                "llm_stats": {},
                "useful_fields": 0,
                "malformed_rate": 0.0,
                "item_total": 0,
                "malformed_total": 0,
                "extracted": {},
                "benchmark_error": f"per-school timeout after {school_timeout_seconds}s",
            }
        except Exception as exc:
            row = {
                "school_id": school_id,
                "navigate_success": False,
                "extract_status": "benchmark_error",
                "extract_skipped": False,
                "input_tokens": 0,
                "output_tokens": 0,
                "llm_stats": {},
                "useful_fields": 0,
                "malformed_rate": 0.0,
                "item_total": 0,
                "malformed_total": 0,
                "extracted": {},
                "benchmark_error": f"{type(exc).__name__}: {exc}",
            }

        per_school.append(row)

        if progress_callback and (index % effective_checkpoint_every == 0 or index == len(school_ids)):
            progress_callback(index, _build_mode_result(mode, fetch_engine, per_school))

    return _build_mode_result(mode, fetch_engine, per_school)


async def main() -> int:
    args = _parse_args()
    school_ids = await _select_school_ids(args.country, args.city, args.limit, args.school_ids)
    if not school_ids:
        print("No candidate schools found.")
        return 1

    modes = [mode.strip().lower() for mode in args.modes.split(",") if mode.strip()]
    valid_modes = {"bs4", "trafilatura", "crawl4ai"}
    invalid = [mode for mode in modes if mode not in valid_modes]
    if invalid:
        print(f"Invalid modes: {invalid}. Valid modes: {sorted(valid_modes)}")
        return 2
    if not modes:
        print("No modes selected.")
        return 2

    fetch_engines = [engine.strip().lower() for engine in args.fetch_engines.split(",") if engine.strip()]
    valid_fetch_engines = {"httpx", "crawl4ai"}
    invalid_fetch = [engine for engine in fetch_engines if engine not in valid_fetch_engines]
    if invalid_fetch:
        print(f"Invalid fetch engines: {invalid_fetch}. Valid: {sorted(valid_fetch_engines)}")
        return 2
    if not fetch_engines:
        print("No fetch engines selected.")
        return 2

    print(
        f"Comparing fetch_engines={fetch_engines} extractors={modes} "
        f"on {len(school_ids)} schools: {school_ids}"
    )
    report: dict[str, Any] = {}
    summary: dict[str, Any] = {}
    multi_fetch = len(fetch_engines) > 1
    output_path = Path(args.output) if args.output else None

    def _checkpoint_writer(key: str, total_schools: int) -> Callable[[int, dict[str, Any]], None]:
        def _write(completed_schools: int, partial_result: dict[str, Any]) -> None:
            report[key] = partial_result
            summary[key] = _summarize_mode_result(partial_result["fetch_engine"], partial_result["mode"], partial_result)
            _write_output_file(
                output_path=output_path,
                report=report,
                summary=summary,
                in_progress={
                    "mode_key": key,
                    "completed_schools": completed_schools,
                    "total_schools": total_schools,
                },
            )

        return _write

    for fetch_engine in fetch_engines:
        for mode in modes:
            key = f"{fetch_engine}:{mode}" if multi_fetch else mode
            result = await _run_mode(
                mode=mode,
                fetch_engine=fetch_engine,
                school_ids=school_ids,
                country=args.country,
                school_timeout_seconds=max(0.0, float(args.school_timeout_seconds)),
                checkpoint_every=max(1, int(args.checkpoint_every)),
                allow_navigation_refresh_on_empty=not bool(args.disable_refresh_on_empty_content),
                skip_navigation=bool(args.skip_navigation),
                progress_callback=_checkpoint_writer(key, len(school_ids)) if output_path else None,
            )
            report[key] = result
            summary[key] = _summarize_mode_result(fetch_engine, mode, result)

    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Wrote detailed report: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
