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

    # Force re-geocode all locations (even if they have coordinates)
    uv run python scripts/geocode_locations.py --force

    # Geocode specific school
    uv run python scripts/geocode_locations.py --school-id 123
"""
import asyncio
import sys
import argparse
from pathlib import Path

# Add parent directory to path to allow imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database import async_session_maker
from app.services.geocoding.service import GeocodingService


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

    args = parser.parse_args()

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
            # Geocode all missing locations
            if args.force:
                print("WARNING: --force flag will re-geocode ALL locations!")
                print("This may take a long time and make many API requests.")
                response = input("Continue? (yes/no): ")
                if response.lower() != "yes":
                    print("Aborted.")
                    return

            print("Geocoding all locations without coordinates...")
            if args.limit:
                print(f"Limit: {args.limit} locations")
            print()

            summary = await service.geocode_all_missing(limit=args.limit)

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
