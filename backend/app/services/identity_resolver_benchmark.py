"""Read-only benchmark for the public English-identity resolver."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import School, SourcePage
from app.models.scrape_log import ScrapeType
from app.scrapers import extractor
from app.scrapers import extractor_helpers as helpers

DEFAULT_BENCHMARK_PATH = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "fixtures"
    / "identity_resolver_benchmark.json"
)


def load_identity_resolver_benchmark(path: Path = DEFAULT_BENCHMARK_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _identity_key(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def _normalized_candidate(value: Any, country_code: str) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return helpers._normalize_display_name_i18n(value, country_code) or {}


def _english_candidate(candidate: dict[str, str]) -> str | None:
    value = candidate.get("en")
    if not value and candidate.get("bg") and not re.search(r"[А-Яа-я]", candidate["bg"]):
        value = candidate["bg"]
    if not value or re.search(r"[А-Яа-я]", value):
        return None
    return value


def _signal_state(
    candidate: dict[str, str],
    *,
    school: School,
    pages: list[SourcePage],
) -> dict[str, Any]:
    evidence = extractor._build_display_name_evidence(candidate, school=school, pages=pages)
    registry_name = (school.name_i18n or {}).get("bg") or (school.name_i18n or {}).get("en")
    return {
        "accepted": evidence is not None,
        "signals": (evidence or {}).get("signals", []),
        "domain": extractor._display_name_has_domain_alias_match(
            candidate,
            registry_name=registry_name,
            website_url=school.website_url,
        ),
        "exact_page": extractor._display_name_has_exact_official_page_identity(
            candidate,
            website_url=school.website_url,
            pages=pages,
        ),
        "repeated_page": extractor._display_name_has_repeated_page_identity(
            candidate,
            school=school,
            pages=pages,
        ),
    }


async def evaluate_identity_resolver_benchmark(
    db: AsyncSession,
    *,
    benchmark: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate fixed good/bad labels and recorded deferred candidates without writes."""
    benchmark = benchmark or load_identity_resolver_benchmark()
    known_good = list(benchmark.get("known_good") or [])
    known_bad = list(benchmark.get("known_bad") or [])
    deferred_ids = [int(value) for value in benchmark.get("deferred_ids") or []]
    school_ids = sorted(
        {int(row["id"]) for row in known_good + known_bad} | set(deferred_ids)
    )

    schools = (
        await db.execute(select(School).where(School.id.in_(school_ids)))
    ).scalars().all()
    pages = (
        await db.execute(
            select(SourcePage).where(
                SourcePage.school_id.in_(school_ids),
                SourcePage.scrape_type == ScrapeType.WEBSITE,
                SourcePage.is_valid.is_(True),
                SourcePage.raw_markdown.isnot(None),
            )
        )
    ).scalars().all()
    school_by_id = {int(school.id): school for school in schools}
    pages_by_school: dict[int, list[SourcePage]] = defaultdict(list)
    for page in pages:
        if page.school_id is not None:
            pages_by_school[int(page.school_id)].append(page)

    missing_ids = sorted(set(school_ids) - set(school_by_id))
    if missing_ids:
        raise ValueError(f"Benchmark schools missing from database: {missing_ids}")

    good_rows = []
    for row in known_good:
        school_id = int(row["id"])
        school = school_by_id[school_id]
        candidate = _normalized_candidate({"en": row["english_name"]}, school.country_code)
        state = _signal_state(candidate, school=school, pages=pages_by_school[school_id])
        accepted_name = _english_candidate(candidate) if state["accepted"] else None
        good_rows.append(
            {
                "id": school_id,
                "english_name": row["english_name"],
                "correctly_accepted": bool(
                    accepted_name
                    and _identity_key(accepted_name) == _identity_key(row["english_name"])
                ),
                **state,
            }
        )

    bad_rows = []
    for row in known_bad:
        school_id = int(row["id"])
        school = school_by_id[school_id]
        candidate = _normalized_candidate(row["candidate"], school.country_code)
        state = _signal_state(candidate, school=school, pages=pages_by_school[school_id])
        bad_rows.append(
            {
                "id": school_id,
                "candidate": row["candidate"],
                "reason": row["reason"],
                "leaked": bool(state["accepted"]),
                **state,
            }
        )

    deferred_rows = []
    for school_id in deferred_ids:
        school = school_by_id[school_id]
        attributes = dict(school.attributes or {})
        curation = attributes.get("display_name_curation")
        recorded = curation.get("candidate") if isinstance(curation, dict) else None
        candidate = _normalized_candidate(recorded, school.country_code)
        english_name = _english_candidate(candidate)
        state = (
            _signal_state(candidate, school=school, pages=pages_by_school[school_id])
            if english_name
            else {"accepted": False, "signals": [], "domain": False, "exact_page": False, "repeated_page": False}
        )
        deferred_rows.append(
            {
                "id": school_id,
                "has_recorded_candidate": bool(candidate),
                "english_candidate": english_name,
                **state,
            }
        )

    return {
        "schema_version": 1,
        "mode": "read_only_cached_pages_no_llm",
        "schools": len(school_ids),
        "cached_pages": len(pages),
        "known_good": {
            "correctly_accepted": sum(row["correctly_accepted"] for row in good_rows),
            "total": len(good_rows),
            "missed": [row for row in good_rows if not row["correctly_accepted"]],
        },
        "known_bad": {
            "leaked": sum(row["leaked"] for row in bad_rows),
            "total": len(bad_rows),
            "leaks": [row for row in bad_rows if row["leaked"]],
        },
        "deferred": {
            "total": len(deferred_rows),
            "with_recorded_candidate": sum(row["has_recorded_candidate"] for row in deferred_rows),
            "with_english_candidate": sum(bool(row["english_candidate"]) for row in deferred_rows),
            "english_candidate_accepted": sum(
                bool(row["english_candidate"] and row["accepted"]) for row in deferred_rows
            ),
            "accepted_rows": [
                row for row in deferred_rows if row["english_candidate"] and row["accepted"]
            ],
        },
    }


__all__ = [
    "DEFAULT_BENCHMARK_PATH",
    "evaluate_identity_resolver_benchmark",
    "load_identity_resolver_benchmark",
]
