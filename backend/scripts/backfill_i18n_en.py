#!/usr/bin/env python3
"""Backfill missing EN i18n values using deterministic transliteration."""

import argparse
import asyncio

from sqlalchemy import select

from app.database import async_session_maker
from app.models import School, SchoolLocation
from app.utils.transliteration import transliterate_address, transliterate_bulgarian


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill name_i18n/address_i18n EN values")
    parser.add_argument("--country", default="bg", help="Country code filter (default: bg)")
    parser.add_argument("--city", default=None, help="Optional city filter (e.g. sofia)")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing EN values (default: fill missing only)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without committing",
    )
    return parser.parse_args()


async def backfill(country: str, city: str | None, force: bool, dry_run: bool) -> None:
    async with async_session_maker() as db:
        school_query = select(School).where(School.country_code == country)
        if city:
            school_query = school_query.where(School.city == city)
        schools = (await db.execute(school_query)).scalars().all()

        school_updates = 0
        school_skipped = 0
        for school in schools:
            name_i18n = dict(school.name_i18n or {})
            bg_name = name_i18n.get("bg")
            en_name = name_i18n.get("en")
            if not bg_name:
                school_skipped += 1
                continue
            if en_name and not force:
                school_skipped += 1
                continue

            name_i18n["en"] = transliterate_bulgarian(bg_name)
            school.name_i18n = name_i18n
            school_updates += 1

        location_query = (
            select(SchoolLocation)
            .join(School, School.id == SchoolLocation.school_id)
            .where(School.country_code == country)
        )
        if city:
            location_query = location_query.where(School.city == city)
        locations = (await db.execute(location_query)).scalars().all()

        location_updates = 0
        location_skipped = 0
        for location in locations:
            address_i18n = dict(location.address_i18n or {})
            bg_address = address_i18n.get("bg")
            en_address = address_i18n.get("en")
            if not bg_address:
                location_skipped += 1
                continue
            if en_address and not force:
                location_skipped += 1
                continue

            address_i18n["en"] = transliterate_address(bg_address)
            location.address_i18n = address_i18n
            location_updates += 1

        if dry_run:
            await db.rollback()
        else:
            await db.commit()

        print("============================================================")
        print("i18n EN Backfill")
        print("============================================================")
        print(f"Country: {country}")
        print(f"City: {city or 'all'}")
        print(f"Mode: {'dry-run' if dry_run else 'commit'}")
        print("")
        print(f"Schools updated: {school_updates}")
        print(f"Schools skipped: {school_skipped}")
        print(f"Locations updated: {location_updates}")
        print(f"Locations skipped: {location_skipped}")


async def main() -> None:
    args = parse_args()
    await backfill(
        country=args.country,
        city=args.city,
        force=args.force,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    asyncio.run(main())
