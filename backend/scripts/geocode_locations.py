#!/usr/bin/env python3
"""
Geocode school locations using OpenStreetMap Nominatim.

This script geocodes all school locations that don't have coordinates yet.
It respects Nominatim's usage policy (max 1 request/second).

Usage:
    # Geocode all missing locations
    uv run python scripts/geocode_locations.py

    # Geocode with limit (for testing)
    uv run python scripts/geocode_locations.py --limit 10

    # Force re-geocode Sofia, Bulgaria locations (even with coordinates)
    uv run python scripts/geocode_locations.py --force

    # Deliberately remove the default Bulgaria/Sofia scope
    uv run python scripts/geocode_locations.py --force --all-locations

    # Geocode specific school
    uv run python scripts/geocode_locations.py --school-id 123

    # Read-only: list a school's locations that share a point at different addresses
    uv run python scripts/geocode_locations.py --check-shared-points
"""
import argparse
import asyncio
import sys
from pathlib import Path

# Add parent directory to path to allow imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database import async_session_maker
from app.services.geocoding.service import GeocodingService, same_school_shared_points


async def main():
    """Main geocoding script."""
    parser = argparse.ArgumentParser(description="Geocode school locations")
    parser.add_argument(
        "--limit",
        type=int,
        help="Maximum number of locations to geocode (for testing/rate limiting)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-geocode even if coordinates exist",
    )
    parser.add_argument(
        "--school-id",
        type=int,
        help="Geocode only locations for this school ID",
    )
    parser.add_argument(
        "--country",
        default="bg",
        help="School country code to process (default: bg)",
    )
    parser.add_argument(
        "--city",
        default="sofia",
        help="School city to process (default: sofia)",
    )
    parser.add_argument(
        "--all-locations",
        action="store_true",
        help="Process all countries and cities instead of the default bg/sofia scope",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Confirm a force run non-interactively",
    )

    parser.add_argument(
        "--check-shared-points",
        action="store_true",
        help="Read-only: report one school's locations on one point at different addresses",
    )

    args = parser.parse_args()

    if args.check_shared_points:
        async with async_session_maker() as db:
            pairs = await same_school_shared_points(
                db,
                country_code=None if args.all_locations else args.country,
                city=None if args.all_locations else args.city,
            )
        for first, second, distance in pairs:
            print(
                f"school {first.school_id}: location {first.id} {first.address_i18n} and "
                f"{second.id} {second.address_i18n} are {distance:.0f} m apart"
            )
        print(f"{len(pairs)} same-school pairs share a point at different addresses")
        return

    print("=" * 60)
    print("Sofia School Comparison - Geocoding Script")
    print("=" * 60)
    print()

    async with async_session_maker() as db:
        service = GeocodingService(db=db)

        print(f"Using provider: {service.provider.provider_name}")

        # Show rate limit if provider has one
        rate_limit = getattr(service.provider, "MIN_REQUEST_INTERVAL", None)
        if rate_limit:
            print(f"Rate limit: {rate_limit}s per request")
        print()

        if args.school_id:
            # Geocode specific school
            print(f"Geocoding locations for school ID {args.school_id}...")
            results = await service.geocode_school_locations(
                school_id=args.school_id,
                force=args.force,
            )

            print()
            print(f"Results: {len(results)} locations processed")
            for location_id, result in results.items():
                status = "✓" if result.success else "✗"
                if result.success:
                    print(f"  {status} Location {location_id}: ({result.lat}, {result.lng})")
                else:
                    print(f"  {status} Location {location_id}: {result.error}")

        else:
            country_code = None if args.all_locations else args.country
            city = None if args.all_locations else args.city

            # Geocode all missing locations
            if args.force:
                scope = (
                    "all locations"
                    if args.all_locations
                    else f"{args.country}/{args.city} locations"
                )
                print(f"WARNING: --force flag will re-geocode {scope}!")
                print("This may take a long time and make many API requests.")
                if not args.yes:
                    response = input("Continue? (yes/no): ")
                    if response.lower() != "yes":
                        print("Aborted.")
                        return

            if args.force:
                print("Re-geocoding selected locations...")
            else:
                print("Geocoding selected locations without coordinates...")
            if args.all_locations:
                print("Scope: all countries and cities")
            else:
                print(f"Scope: country={args.country}, city={args.city}")
            if args.limit:
                print(f"Limit: {args.limit} locations")
            print()

            summary = await service.geocode_all_locations(
                force=args.force,
                limit=args.limit,
                country_code=country_code,
                city=city,
            )

            print()
            print("=" * 60)
            print("Geocoding Complete")
            print("=" * 60)
            print(f"Total locations processed: {summary['total']}")
            print(f"Successfully geocoded: {summary['success']}")
            print(f"Failed: {summary['failed']}")

            if summary["failed"] > 0:
                failure_rate = summary["failed"] / summary["total"] * 100
                print(f"Failure rate: {failure_rate:.1f}%")

            print()


if __name__ == "__main__":
    asyncio.run(main())
