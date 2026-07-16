#!/usr/bin/env python3
"""Run the deterministic, targeted P2.12 API and database acceptance checks."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Iterable

from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database import async_session_maker
from app.main import app
from app.models import Pricing, School, SchoolLocation
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


async def api_audit(cohort_ids: list[int]) -> dict[str, Any]:
    async with async_session_maker() as db:
        raw_attributes = {
            school_id: attributes if isinstance(attributes, dict) else {}
            for school_id, attributes in (
                await db.execute(select(School.id, School.attributes))
            ).all()
        }
        publishable_pricing_ids = {
            row.id
            for row in (await db.execute(select(Pricing))).scalars()
            if pricing_row_is_publishable(row)
        }

    requests: list[tuple[str, str]] = [("list", "/schools")]
    requests.extend(
        (f"detail-{school_id}", f"/schools/{school_id}")
        for school_id in cohort_ids
    )
    requests.append(
        (f"detail-{REGRESSION_SCHOOL_ID}", f"/schools/{REGRESSION_SCHOOL_ID}")
    )
    compare_ids = [*cohort_ids, REGRESSION_SCHOOL_ID]
    for offset in range(0, len(compare_ids), 3):
        batch = compare_ids[offset : offset + 3]
        requests.append(
            (
                f"compare-{'-'.join(map(str, batch))}",
                f"/compare?ids={','.join(map(str, batch))}",
            )
        )

    hits: list[dict[str, str]] = []
    statuses: dict[str, int] = {}
    school_367_payloads = 0
    school_367_address_values: list[str] = []
    dynamic_field_hits: list[dict[str, Any]] = []
    withheld_pricing_hits: list[dict[str, Any]] = []
    published_pricing_ids: set[int] = set()
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
                hits.extend(display_markdown_hits(payload, endpoint=request_key))
                rows = payload if isinstance(payload, list) else [payload]
                for school in rows:
                    if not isinstance(school, dict):
                        continue
                    school_id = school.get("id")
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
                        if isinstance(localized, dict) and localized.get("daily_schedule"):
                            dynamic_field_hits.append(
                                {
                                    "endpoint": request_key,
                                    "school_id": school_id,
                                    "field": "daily_schedule",
                                }
                            )
                    for pricing in school.get("pricing") or []:
                        pricing_id = pricing.get("id") if isinstance(pricing, dict) else None
                        if pricing_id is None:
                            continue
                        published_pricing_ids.add(pricing_id)
                        if pricing_id not in publishable_pricing_ids:
                            withheld_pricing_hits.append(
                                {
                                    "endpoint": request_key,
                                    "school_id": school_id,
                                    "pricing_id": pricing_id,
                                }
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
    return {
        "requests": len(statuses),
        "non_200": non_200,
        "markdown_hits": hits,
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

    return {
        "duplicate_groups": duplicate_groups,
        "unexplained_duplicate_groups": unexplained,
        "terminal_locations": terminal,
        "school_161_withheld": school_161_withheld,
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cohort-file",
        type=Path,
        help="Optional cohort file; defaults to the tracked P2.9 run-2 school IDs",
    )
    args = parser.parse_args()
    cohort_ids = read_cohort(args.cohort_file)
    api = await api_audit(cohort_ids)
    database = await database_audit()
    result = {
        "cohort_ids": cohort_ids,
        "api": api,
        "database": database,
        "llm_calls": 0,
    }
    result["passed"] = bool(
        not api["non_200"]
        and not api["markdown_hits"]
        and api["school_367_payloads"] > 0
        and not api["school_367_tainted_address_hits"]
        and not api["dynamic_field_hits"]
        and not api["withheld_pricing_hits"]
        and not database["unexplained_duplicate_groups"]
        and all(database["terminal_locations"].values())
        and database["school_161_withheld"]
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
