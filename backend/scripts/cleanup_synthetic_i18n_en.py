#!/usr/bin/env python3
"""Remove deterministic transliteration from persisted EN i18n fields.

This keeps source-backed EN values intact and clears only values that exactly
match the project's legacy transliteration fallback.
"""

import argparse
import asyncio

from sqlalchemy import select

from app.database import async_session_maker
from app.models import School, SchoolLocation
from app.utils.transliteration import transliterate_address, transliterate_bulgarian


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Clear synthetic EN i18n values")
    parser.add_argument("--country", default="bg", help="Country code filter (default: bg)")
    parser.add_argument("--city", default=None, help="Optional city filter (e.g. sofia)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without committing",
    )
    return parser.parse_args()


def _is_synthetic_name_en(bg_value: str | None, en_value: str | None) -> bool:
    if not bg_value or not en_value:
        return False
    return en_value.strip() == transliterate_bulgarian(bg_value).strip()


def _is_synthetic_address_en(bg_value: str | None, en_value: str | None) -> bool:
    if not bg_value or not en_value:
        return False
    return en_value.strip() == transliterate_address(bg_value).strip()


async def cleanup(country: str, city: str | None, dry_run: bool) -> None:
    async with async_session_maker() as db:
        school_query = select(School).where(School.country_code == country)
        if city:
            school_query = school_query.where(School.city == city)
        schools = (await db.execute(school_query)).scalars().all()

        school_cleared = 0
        school_kept = 0
        for school in schools:
            name_i18n = dict(school.name_i18n or {})
            bg_name = name_i18n.get("bg")
            en_name = name_i18n.get("en")
            if not _is_synthetic_name_en(bg_name, en_name):
                school_kept += 1
                continue

            name_i18n.pop("en", None)
            school.name_i18n = name_i18n
            school_cleared += 1

        location_query = (
            select(SchoolLocation)
            .join(School, School.id == SchoolLocation.school_id)
            .where(School.country_code == country)
        )
        if city:
            location_query = location_query.where(School.city == city)
        locations = (await db.execute(location_query)).scalars().all()

        location_cleared = 0
        location_kept = 0
        for location in locations:
            address_i18n = dict(location.address_i18n or {})
            bg_address = address_i18n.get("bg")
            en_address = address_i18n.get("en")
            if not _is_synthetic_address_en(bg_address, en_address):
                location_kept += 1
                continue

            address_i18n.pop("en", None)
            location.address_i18n = address_i18n
            location_cleared += 1

        if dry_run:
            await db.rollback()
        else:
            await db.commit()

        print("============================================================")
        print("Synthetic EN i18n cleanup")
        print("============================================================")
        print(f"Country: {country}")
        print(f"City: {city or 'all'}")
        print(f"Mode: {'dry-run' if dry_run else 'commit'}")
        print("")
        print(f"Schools cleared: {school_cleared}")
        print(f"Schools kept: {school_kept}")
        print(f"Locations cleared: {location_cleared}")
        print(f"Locations kept: {location_kept}")


async def main() -> None:
    args = parse_args()
    await cleanup(
        country=args.country,
        city=args.city,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    asyncio.run(main())
