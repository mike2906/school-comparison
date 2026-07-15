#!/usr/bin/env python3
"""Relabel Sofia-province schools without deleting or moving any related data.

The script always writes the complete classification report before applying any
changes. Classification uses MoE address/municipality metadata confirmed by the
BG education GeoJSON dataset; stored coordinates only select the audit cohort.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database import async_session_maker
from app.models import School
from app.services.geocoding.bg import GeoJSONProvider, city_storage_value
from app.services.geocoding.bounds import SOFIA_MUNICIPALITY_BOUNDS, point_in_bounds


DEFAULT_REPORT_ROOT = Path(__file__).parent.parent / "reports" / "scope-repair"


@dataclass
class Classification:
    school: School
    population: str
    name: str
    address: str
    current_city: str
    municipality: Optional[str]
    action: str
    evidence: str
    target_city: Optional[str] = None


def _school_population(school: School) -> Optional[str]:
    coordinate_pairs = [
        (location.lat, location.lng)
        for location in school.locations
        if location.lat is not None and location.lng is not None
    ]
    if any(
        not point_in_bounds(float(lat), float(lng), SOFIA_MUNICIPALITY_BOUNDS)
        for lat, lng in coordinate_pairs
    ):
        return "out_of_bounds"
    if not coordinate_pairs:
        return "no_coordinates"
    return None


def _school_addresses(school: School) -> list[str]:
    addresses: list[str] = []
    for location in school.locations:
        values = location.address_i18n or {}
        address = values.get("bg") or values.get("en") or ""
        if address.strip() and address.strip() not in addresses:
            addresses.append(address.strip())
    return addresses


async def classify_candidates(
    db: AsyncSession,
    provider: GeoJSONProvider,
) -> list[Classification]:
    result = await db.execute(
        select(School)
        .options(selectinload(School.locations))
        .where(
            func.lower(School.country_code) == "bg",
            func.lower(School.city) == "sofia",
        )
        .order_by(School.id)
    )

    classifications: list[Classification] = []
    for school in result.scalars().unique():
        population = _school_population(school)
        if population is None:
            continue

        attrs = school.attributes if isinstance(school.attributes, dict) else {}
        addresses = _school_addresses(school)
        name = (school.name_i18n or {}).get("bg") or (school.name_i18n or {}).get("en") or ""
        resolution = provider.resolve_admin_municipality(
            school_name=name,
            addresses=addresses,
            municipality_hint=attrs.get("moe_municipality_name"),
        )

        municipality = resolution.municipality
        target_city: Optional[str] = None
        if municipality and city_storage_value(municipality) == "sofia":
            action = "keep sofia"
        elif municipality:
            target_city = city_storage_value(attrs.get("moe_town_name") or municipality)
            if target_city and target_city != "sofia":
                action = f"relabel to {target_city}"
            else:
                target_city = None
                action = "ambiguous - no change"
        else:
            action = "ambiguous - no change"

        classifications.append(
            Classification(
                school=school,
                population=population,
                name=name,
                address=" || ".join(addresses),
                current_city=school.city or "",
                municipality=municipality,
                action=action,
                evidence=resolution.evidence,
                target_city=target_city,
            )
        )
    return classifications


def _md(value: object) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", "<br>")


def write_classification_report(
    classifications: list[Classification],
    *,
    report_root: Path,
    timestamp: str,
) -> Path:
    report_dir = report_root / timestamp
    report_dir.mkdir(parents=True, exist_ok=False)
    report_path = report_dir / "classification.md"

    population_counts = {
        population: sum(row.population == population for row in classifications)
        for population in ("out_of_bounds", "no_coordinates")
    }
    action_counts = {
        "relabel": sum(row.target_city is not None for row in classifications),
        "keep": sum(row.action == "keep sofia" for row in classifications),
        "ambiguous": sum(row.action == "ambiguous - no change" for row in classifications),
    }
    lines = [
        "# Sofia city-scope repair classification",
        "",
        f"Generated: `{timestamp}`",
        "",
        f"Populations: out-of-bounds={population_counts['out_of_bounds']}, "
        f"no-coordinates={population_counts['no_coordinates']}",
        "",
        f"Actions: relabel={action_counts['relabel']}, keep={action_counts['keep']}, "
        f"ambiguous={action_counts['ambiguous']}",
        "",
        "| School id | Name | Address | Current city | Derived municipality | Action | Population | Evidence |",
        "|---:|---|---|---|---|---|---|---|",
    ]
    lines.extend(
        "| " + " | ".join(
            [
                str(row.school.id),
                _md(row.name),
                _md(row.address),
                _md(row.current_city),
                _md(row.municipality),
                _md(row.action),
                _md(row.population),
                _md(row.evidence),
            ]
        ) + " |"
        for row in classifications
    )

    with report_path.open("x", encoding="utf-8") as report:
        report.write("\n".join(lines) + "\n")
        report.flush()
        os.fsync(report.fileno())
    return report_path


async def apply_relabels(db: AsyncSession, classifications: list[Classification]) -> int:
    relabeled = 0
    for row in classifications:
        if row.target_city is None:
            continue
        row.school.city = row.target_city
        relabeled += 1
    await db.commit()
    return relabeled


async def run_repair(
    db: AsyncSession,
    *,
    provider: GeoJSONProvider,
    report_root: Path,
    timestamp: str,
    apply: bool,
    expected_out_of_bounds: int,
    expected_no_coordinates: int,
) -> dict:
    classifications = await classify_candidates(db, provider)
    counts = {
        "out_of_bounds": sum(row.population == "out_of_bounds" for row in classifications),
        "no_coordinates": sum(row.population == "no_coordinates" for row in classifications),
    }
    expected = {
        "out_of_bounds": expected_out_of_bounds,
        "no_coordinates": expected_no_coordinates,
    }
    if counts != expected:
        raise RuntimeError(f"candidate population drift: expected {expected}, found {counts}")

    # This durable write deliberately precedes the first ORM mutation.
    report_path = write_classification_report(
        classifications,
        report_root=report_root,
        timestamp=timestamp,
    )
    relabeled = await apply_relabels(db, classifications) if apply else 0
    return {
        "report": str(report_path),
        "applied": apply,
        "out_of_bounds": counts["out_of_bounds"],
        "no_coordinates": counts["no_coordinates"],
        "classified": len(classifications),
        "relabeled": relabeled,
        "keep": sum(row.action == "keep sofia" for row in classifications),
        "ambiguous": sum(row.action == "ambiguous - no change" for row in classifications),
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description="Classify and relabel Sofia-province schools")
    parser.add_argument("--apply", action="store_true", help="Apply relabel actions after writing the report")
    parser.add_argument("--timestamp", help="Evidence directory name (default: current UTC timestamp)")
    parser.add_argument("--report-root", type=Path, default=DEFAULT_REPORT_ROOT)
    parser.add_argument("--expected-out-of-bounds", type=int, default=65)
    parser.add_argument("--expected-no-coordinates", type=int, default=76)
    args = parser.parse_args()

    timestamp = args.timestamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    async with async_session_maker() as db:
        summary = await run_repair(
            db,
            provider=GeoJSONProvider(),
            report_root=args.report_root,
            timestamp=timestamp,
            apply=args.apply,
            expected_out_of_bounds=args.expected_out_of_bounds,
            expected_no_coordinates=args.expected_no_coordinates,
        )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
