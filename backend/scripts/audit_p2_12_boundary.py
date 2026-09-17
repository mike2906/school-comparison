#!/usr/bin/env python3
"""Run deterministic P2.12/P2.13 API and database acceptance checks.

The default P2.13 scope is every Sofia school serialized by the list endpoint.  The
tracked P2.9 cohort remains available through ``--cohort-only`` for the older,
targeted P2.12 regression check.  This script never invokes an LLM or a scraper.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Iterable

from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.orm import load_only, selectinload

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database import async_session_maker
from app.main import app
from app.models import PipelineRun, Pricing, School, SchoolLocation, SourcePage
from app.services.data_quality import compute_quality_metrics
from app.services.geocoding.bounds import SOFIA_MUNICIPALITY_BOUNDS, point_in_bounds
from app.services.geocoding.service import geocode_failure_is_terminal
from app.utils.display_gating import pricing_row_is_publishable
from app.utils.school_attributes import publishable_display_text
from app.utils.website_data import WEBSITE_DATA_WITHHELD_KEY


DEFAULT_COHORT_IDS = (
    506,
    529,
    538,
    631,
    584,
    404,
    609,
    516,
    593,
    610,
    103,
    105,
    297,
    163,
    310,
    161,
    191,
    324,
)
REGRESSION_SCHOOL_ID = 367
TERMINAL_LOCATION_IDS = (1143, 1145, 1188)
KNOWN_SHARED_COORDINATE_GROUPS = {
    frozenset({1107, 1108, 1123}),
    frozenset({1141, 1142}),
    frozenset({130, 132}),
    frozenset({129, 133}),
    frozenset({1160, 1191}),
    frozenset({853, 854}),
    frozenset({1104, 1111}),
    # Dr Petar Beron school and kindergarten share the same verified campus.
    frozenset({1082, 1091}),
}
DISPLAY_KEYS = {
    "name_i18n",
    "resolved_name_i18n",
    "summary_i18n",
    "attributes",
    "attributes_i18n",
    "address_i18n",
    "resolved_address_i18n",
}
INTERNAL_KEYS = {
    "confidence_score",
    "data_validation",
    "extracted",
    "extracted_i18n",
    "field_path",
    "source_refs",
    "submitted_by",
    "value_json",
    "value_text",
    "website_extracted",
}
PROVENANCE_KEYS = {"source_type", "source_url", "last_verified", "confidence"}
WEBSITE_ADMISSION_FIELDS = {
    "entry_requirements",
    "application_deadlines",
    "available_spots",
}
URL_FIELDS = {"platform_url", "source_url", "website_url"}
CACHED_RUN_IDS = (
    "db4ba90d-6894-4164-a873-34ee79687fad",
    "396dffe5-c905-44e2-8582-1d4c1bcdbd66",
)
CACHED_RUN_EXPECTATIONS = {
    "db4ba90d-6894-4164-a873-34ee79687fad": {
        "status": "partial",
        "cohort_size": 18,
        "cost_usd": 0.029856,
    },
    "396dffe5-c905-44e2-8582-1d4c1bcdbd66": {
        "status": "partial",
        "cohort_size": 18,
        "cost_usd": 0.923238,
    },
}
CACHED_TRUTH_SCHOOL_IDS = {105, 153, 310, 529, 538, 570}


def read_cohort(path: Path | None = None) -> list[int]:
    if path is None:
        return list(DEFAULT_COHORT_IDS)
    return [
        int(line)
        for raw in path.read_text(encoding="utf-8").splitlines()
        if (line := raw.split("#", 1)[0].strip())
    ]


def iter_strings(value: Any, path: tuple[str, ...] = ()) -> Iterable[tuple[str, str]]:
    if isinstance(value, str):
        yield ".".join(path), value
    elif isinstance(value, dict):
        for key, child in value.items():
            yield from iter_strings(child, (*path, str(key)))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from iter_strings(child, (*path, str(index)))


def display_markdown_hits(payload: Any, *, endpoint: str) -> list[dict[str, str]]:
    hits: list[dict[str, str]] = []

    def walk(value: Any, path: tuple[str, ...] = ()) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                child_path = (*path, str(key))
                if key in DISPLAY_KEYS:
                    for display_path, text in iter_strings(child, child_path):
                        if publishable_display_text(text) is None:
                            hits.append(
                                {"endpoint": endpoint, "path": display_path, "value": text}
                            )
                else:
                    walk(child, child_path)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, (*path, str(index)))

    walk(payload)
    return hits


def internal_key_hits(payload: Any, *, endpoint: str) -> list[dict[str, str]]:
    hits: list[dict[str, str]] = []

    def walk(value: Any, path: tuple[str, ...] = ()) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                child_path = (*path, str(key))
                if key in INTERNAL_KEYS or key.startswith("moe_"):
                    hits.append({"endpoint": endpoint, "path": ".".join(child_path)})
                walk(child, child_path)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, (*path, str(index)))

    walk(payload)
    return hits


def all_display_text_hits(payload: Any, *, endpoint: str) -> list[dict[str, str]]:
    """Find Markdown/bare URLs in strings other than explicit URL fields."""
    hits: list[dict[str, str]] = []

    def walk(value: Any, path: tuple[str, ...] = ()) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                child_path = (*path, str(key))
                if isinstance(child, str) and key not in URL_FIELDS:
                    if publishable_display_text(child) is None:
                        hits.append(
                            {"endpoint": endpoint, "path": ".".join(child_path), "value": child}
                        )
                else:
                    walk(child, child_path)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                child_path = (*path, str(index))
                if isinstance(child, str):
                    if publishable_display_text(child) is None:
                        hits.append(
                            {"endpoint": endpoint, "path": ".".join(child_path), "value": child}
                        )
                else:
                    walk(child, child_path)

    walk(payload)
    return hits


def cached_run_matches_expectation(actual: dict[str, Any]) -> bool:
    expected = CACHED_RUN_EXPECTATIONS.get(str(actual.get("run_id")))
    return bool(
        expected
        and actual.get("found") is True
        and actual.get("status") == expected["status"]
        and actual.get("cohort_size") == expected["cohort_size"]
        and actual.get("cost_usd") == expected["cost_usd"]
    )


def partial_coordinate_location_ids(locations: Iterable[Any]) -> list[int]:
    return [
        location.id
        for location in locations
        if (location.lat is None) != (location.lng is None)
    ]


async def sofia_school_ids() -> list[int]:
    async with async_session_maker() as db:
        return list(
            (
                await db.execute(
                    select(School.id)
                    .where(
                        func.lower(School.country_code) == "bg",
                        func.lower(School.city) == "sofia",
                    )
                    .order_by(School.id)
                )
            ).scalars()
        )


def _pricing_query():
    """Pricing with the evidence link the publish gate reads.

    ``Pricing.source_page`` is ``lazy="raise_on_sql"``, so a plain ``select(Pricing)``
    raises here as soon as a row carries a link.
    """
    return select(Pricing).options(
        selectinload(Pricing.source_page).load_only(SourcePage.id, SourcePage.is_valid)
    )


async def api_audit(school_ids: list[int]) -> dict[str, Any]:
    async with async_session_maker() as db:
        raw_attributes = {
            school_id: attributes if isinstance(attributes, dict) else {}
            for school_id, attributes in (
                await db.execute(select(School.id, School.attributes))
            ).all()
        }
        publishable_pricing_ids = {
            row.id
            for row in (await db.execute(_pricing_query())).scalars()
            if pricing_row_is_publishable(row)
        }

    requests: list[tuple[str, str]] = [
        ("list", "/schools?country_code=bg&city=sofia")
    ]
    requests.extend(
        (f"detail-{school_id}", f"/schools/{school_id}")
        for school_id in school_ids
    )
    for offset in range(0, len(school_ids), 5):
        batch = school_ids[offset : offset + 5]
        requests.append(
            (
                f"compare-{'-'.join(map(str, batch))}",
                f"/compare?ids={','.join(map(str, batch))}",
            )
        )

    markdown_hits: list[dict[str, str]] = []
    internal_hits: list[dict[str, str]] = []
    statuses: dict[str, int] = {}
    list_ids_by_locale: dict[str, list[int]] = {}
    detail_ids_by_locale: dict[str, set[int]] = {"bg": set(), "en": set()}
    compare_ids_by_locale: dict[str, set[int]] = {"bg": set(), "en": set()}
    school_367_payloads = 0
    school_367_address_values: list[str] = []
    dynamic_field_hits: list[dict[str, Any]] = []
    withheld_pricing_hits: list[dict[str, Any]] = []
    published_pricing_ids: set[int] = set()
    summary_hits: list[dict[str, Any]] = []
    website_admission_hits: list[dict[str, Any]] = []
    scraped_pricing_hits: list[dict[str, Any]] = []
    provenance_shape_hits: list[dict[str, Any]] = []
    truth_set_leaks: list[dict[str, Any]] = []
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://p2-12-audit") as client:
        for locale in ("bg", "en"):
            for label, url in requests:
                response = await client.get(url, headers={"Accept-Language": locale})
                request_key = f"{locale}:{label}"
                statuses[request_key] = response.status_code
                if response.status_code != 200:
                    continue
                payload = response.json()
                markdown_hits.extend(all_display_text_hits(payload, endpoint=request_key))
                internal_hits.extend(internal_key_hits(payload, endpoint=request_key))
                if label == "list":
                    list_ids_by_locale[locale] = [
                        row.get("id") for row in payload if isinstance(row, dict)
                    ]
                rows = payload if isinstance(payload, list) else [payload]
                for school in rows:
                    if not isinstance(school, dict):
                        continue
                    school_id = school.get("id")
                    if label.startswith("detail-"):
                        detail_ids_by_locale[locale].add(school_id)
                    elif label.startswith("compare-"):
                        compare_ids_by_locale[locale].add(school_id)
                    if school.get("summary_i18n") is not None:
                        summary_hits.append({"endpoint": request_key, "school_id": school_id})
                    stored_attributes = raw_attributes.get(school_id, {})
                    public_attributes = school.get("attributes") or {}
                    for field in ("class_size", "school_hours", "established_year"):
                        if (
                            public_attributes.get(field) is not None
                            and stored_attributes.get(field) is None
                        ):
                            dynamic_field_hits.append(
                                {
                                    "endpoint": request_key,
                                    "school_id": school_id,
                                    "field": field,
                                }
                            )
                    for localized in (school.get("attributes_i18n") or {}).values():
                        if not isinstance(localized, dict):
                            continue
                        if localized.get("daily_schedule"):
                            dynamic_field_hits.append(
                                {
                                    "endpoint": request_key,
                                    "school_id": school_id,
                                    "field": "daily_schedule",
                                }
                            )
                        for field in WEBSITE_ADMISSION_FIELDS:
                            if localized.get(field):
                                website_admission_hits.append(
                                    {
                                        "endpoint": request_key,
                                        "school_id": school_id,
                                        "field": field,
                                    }
                                )
                    for pricing in school.get("pricing") or []:
                        pricing_id = pricing.get("id") if isinstance(pricing, dict) else None
                        if pricing_id is None:
                            continue
                        published_pricing_ids.add(pricing_id)
                        if pricing.get("source") == "scraped_website":
                            scraped_pricing_hits.append(
                                {
                                    "endpoint": request_key,
                                    "school_id": school_id,
                                    "pricing_id": pricing_id,
                                }
                            )
                        if pricing_id not in publishable_pricing_ids:
                            withheld_pricing_hits.append(
                                {
                                    "endpoint": request_key,
                                    "school_id": school_id,
                                    "pricing_id": pricing_id,
                                }
                            )

                    for source in school.get("field_sources") or []:
                        unexpected = sorted(set(source) - PROVENANCE_KEYS)
                        if unexpected:
                            provenance_shape_hits.append(
                                {
                                    "endpoint": request_key,
                                    "school_id": school_id,
                                    "unexpected_keys": unexpected,
                                }
                            )

                    if school_id in CACHED_TRUTH_SCHOOL_IDS and (
                        school.get("summary_i18n") is not None
                        or any(
                            (localized or {}).get(field)
                            for localized in (school.get("attributes_i18n") or {}).values()
                            for field in WEBSITE_ADMISSION_FIELDS
                        )
                        or any(
                            row.get("source") == "scraped_website"
                            for row in school.get("pricing") or []
                        )
                    ):
                        truth_set_leaks.append(
                            {"endpoint": request_key, "school_id": school_id}
                        )

                    if school_id == REGRESSION_SCHOOL_ID:
                        school_367_payloads += 1
                        for location in school.get("locations", []):
                            school_367_address_values.extend(
                                str(value)
                                for key in ("address_i18n", "resolved_address_i18n")
                                for value in (location.get(key) or {}).values()
                            )

    non_200 = {key: status for key, status in statuses.items() if status != 200}
    expected_ids = set(school_ids)
    list_id_sets = {locale: set(ids) for locale, ids in list_ids_by_locale.items()}
    return {
        "requests": len(statuses),
        "non_200": non_200,
        "expected_school_count": len(school_ids),
        "list_ids_are_scoped": {
            locale: ids <= expected_ids for locale, ids in list_id_sets.items()
        },
        "list_ids_match_locales": len(list_id_sets) == 2
        and len({frozenset(ids) for ids in list_id_sets.values()}) == 1,
        "list_school_count": {
            locale: len(ids) for locale, ids in list_id_sets.items()
        },
        "db_scoped_but_not_listed_ids": {
            locale: sorted(expected_ids - ids) for locale, ids in list_id_sets.items()
        },
        "detail_ids_match_scope": {
            locale: ids == expected_ids for locale, ids in detail_ids_by_locale.items()
        },
        "compare_ids_match_scope": {
            locale: ids == expected_ids for locale, ids in compare_ids_by_locale.items()
        },
        "markdown_hits": markdown_hits,
        "internal_key_hits": internal_hits,
        "summary_hits": summary_hits,
        "website_admission_hits": website_admission_hits,
        # Informational only: school-website pricing may now publish when it is
        # evidence-backed. `withheld_pricing_hits` enforces that every published row
        # still satisfies the publish predicate.
        "scraped_pricing_hits": scraped_pricing_hits,
        "provenance_shape_hits": provenance_shape_hits,
        "cached_truth_set_leaks": truth_set_leaks,
        "school_367_payloads": school_367_payloads,
        "school_367_tainted_address_hits": [
            value
            for value in school_367_address_values
            if publishable_display_text(value) is None
        ],
        "dynamic_field_hits": dynamic_field_hits,
        "withheld_pricing_hits": withheld_pricing_hits,
        "published_pricing_ids": sorted(published_pricing_ids),
    }


async def database_audit() -> dict[str, Any]:
    async with async_session_maker() as db:
        scoped_schools = list(
            (
                await db.execute(
                    select(School).where(
                        func.lower(School.country_code) == "bg",
                        func.lower(School.city) == "sofia",
                    )
                )
            ).scalars()
        )
        scoped_ids = [school.id for school in scoped_schools]
        locations = list(
            (
                await db.execute(
                    select(SchoolLocation).where(SchoolLocation.school_id.in_(scoped_ids))
                )
            ).scalars()
        )
        pricing_rows = list(
            (
                await db.execute(_pricing_query().where(Pricing.school_id.in_(scoped_ids)))
            ).scalars()
        )
        quality = await compute_quality_metrics(db, country="bg", city="sofia")
        out_of_bounds = [
            location.id
            for location in locations
            if location.lat is not None
            and location.lng is not None
            and not point_in_bounds(
                float(location.lat), float(location.lng), SOFIA_MUNICIPALITY_BOUNDS
            )
        ]
        precision_missing = [
            location.id
            for location in locations
            if location.lat is not None
            and location.lng is not None
            and (location.geocode_meta or {}).get("precision") not in {"exact", "approximate"}
        ]
        partial_coordinate_ids = partial_coordinate_location_ids(locations)
        terminal_failure_ids = [
            location.id
            for location in locations
            if geocode_failure_is_terminal(location.geocode_meta)
        ]
        invalid_terminal_failures = [
            location.id
            for location in locations
            if geocode_failure_is_terminal(location.geocode_meta)
            and (
                location.lat is not None
                or location.lng is not None
                or not (location.geocode_meta or {}).get("provider")
                or not (location.geocode_meta or {}).get("rejection_reason")
            )
        ]
        duplicate_points = (
            await db.execute(
                select(SchoolLocation.lat, SchoolLocation.lng)
                .join(School, School.id == SchoolLocation.school_id)
                .where(
                    func.lower(School.country_code) == "bg",
                    func.lower(School.city) == "sofia",
                    SchoolLocation.lat.is_not(None),
                    SchoolLocation.lng.is_not(None),
                )
                .group_by(SchoolLocation.lat, SchoolLocation.lng)
                .having(func.count(SchoolLocation.id) > 1)
            )
        ).all()
        duplicate_groups: list[list[int]] = []
        for lat, lng in duplicate_points:
            ids = list(
                (
                    await db.execute(
                        select(SchoolLocation.id)
                        .join(School, School.id == SchoolLocation.school_id)
                        .where(
                            func.lower(School.country_code) == "bg",
                            func.lower(School.city) == "sofia",
                            SchoolLocation.lat == lat,
                            SchoolLocation.lng == lng,
                        )
                        .order_by(SchoolLocation.id)
                    )
                ).scalars()
            )
            duplicate_groups.append(ids)

        unexplained = [
            ids
            for ids in duplicate_groups
            if frozenset(ids) not in KNOWN_SHARED_COORDINATE_GROUPS
        ]
        terminal: dict[int, bool] = {}
        for location_id in TERMINAL_LOCATION_IDS:
            location = await db.get(SchoolLocation, location_id)
            terminal[location_id] = bool(
                location
                and location.lat is None
                and location.lng is None
                and geocode_failure_is_terminal(location.geocode_meta)
            )
        school_161 = await db.get(School, 161)
        school_161_withheld = bool(
            school_161
            and school_161.website_url is None
            and school_161.scrape_status == "failed_validate"
            and isinstance(school_161.attributes, dict)
            and school_161.attributes.get(WEBSITE_DATA_WITHHELD_KEY) is True
        )

        cached_runs: dict[str, Any] = {}
        for run_id in CACHED_RUN_IDS:
            run = await db.get(PipelineRun, run_id)
            summary = (
                {
                    "run_id": run_id,
                    "found": True,
                    "status": run.status.value,
                    "cohort_size": len((run.config or {}).get("cohort_school_ids") or []),
                    "cost_usd": (run.metrics or {}).get("llm_usage", {}).get(
                        "token_cost_usd"
                    ),
                }
                if run
                else {"run_id": run_id, "found": False}
            )
            cached_runs[run_id] = {
                **summary,
                "matches_expectation": cached_run_matches_expectation(summary),
            }

        predicate_publishable_ids = {
            row.id for row in pricing_rows if pricing_row_is_publishable(row)
        }

    return {
        "schools_in_scope": len(scoped_ids),
        "locations_in_scope": len(locations),
        "out_of_bounds_location_ids": out_of_bounds,
        "precision_missing_location_ids": precision_missing,
        "partial_coordinate_location_ids": partial_coordinate_ids,
        "terminal_failure_location_ids": terminal_failure_ids,
        "invalid_terminal_failure_location_ids": invalid_terminal_failures,
        "duplicate_groups": duplicate_groups,
        "unexplained_duplicate_groups": unexplained,
        "terminal_locations": terminal,
        "school_161_withheld": school_161_withheld,
        "quality_metrics": quality,
        "predicate_publishable_pricing_ids": sorted(predicate_publishable_ids),
        "cached_runs": cached_runs,
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cohort-file",
        type=Path,
        help="Optional cohort file; defaults to the tracked P2.9 run-2 school IDs",
    )
    parser.add_argument(
        "--cohort-only",
        action="store_true",
        help="Run the legacy targeted P2.12 cohort instead of exhaustive Sofia scope",
    )
    args = parser.parse_args()
    if args.cohort_file and not args.cohort_only:
        parser.error("--cohort-file requires --cohort-only")
    school_ids = (
        read_cohort(args.cohort_file) if args.cohort_only else await sofia_school_ids()
    )
    audit_ids = (
        school_ids
        if not args.cohort_only or REGRESSION_SCHOOL_ID in school_ids
        else [*school_ids, REGRESSION_SCHOOL_ID]
    )
    api = await api_audit(audit_ids)
    database = await database_audit()
    scoreboard_pricing_ids = database["predicate_publishable_pricing_ids"]
    pricing_parity = (
        api["published_pricing_ids"]
        == scoreboard_pricing_ids
        and database["quality_metrics"]["pricing_rows_failing_gates"]["publishable"]
        == len(scoreboard_pricing_ids)
    )
    coverage = database["quality_metrics"]["website_validation_coverage"]
    result = {
        "scope": "cohort" if args.cohort_only else "all_sofia_serialized_schools",
        "school_count": len(school_ids),
        "api": api,
        "database": database,
        "acceptance": {
            "validation_report_coverage": coverage,
            "pricing_gate_scoreboard_api_parity": pricing_parity,
        },
        "llm_calls": 0,
    }
    if args.cohort_only:
        result["school_ids"] = school_ids
    result["passed"] = bool(
        not api["non_200"]
        and all(api["list_ids_are_scoped"].values())
        and api["list_ids_match_locales"]
        and all(api["detail_ids_match_scope"].values())
        and all(api["compare_ids_match_scope"].values())
        and not api["markdown_hits"]
        and not api["internal_key_hits"]
        and not api["summary_hits"]
        and not api["website_admission_hits"]
        and not api["provenance_shape_hits"]
        and not api["cached_truth_set_leaks"]
        and api["school_367_payloads"] > 0
        and not api["school_367_tainted_address_hits"]
        and not api["dynamic_field_hits"]
        and not api["withheld_pricing_hits"]
        and not database["out_of_bounds_location_ids"]
        and not database["precision_missing_location_ids"]
        and not database["partial_coordinate_location_ids"]
        and not database["invalid_terminal_failure_location_ids"]
        and not database["unexplained_duplicate_groups"]
        and all(database["terminal_locations"].values())
        and database["school_161_withheld"]
        and all(
            run["matches_expectation"] for run in database["cached_runs"].values()
        )
        and coverage["published_without_report"] == 0
        and coverage["coverage_pct"] in {None, 100.0}
        and pricing_parity
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
