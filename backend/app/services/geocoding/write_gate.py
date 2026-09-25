"""Write-time geocoding quality gates."""
import logging
import re
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import School, SchoolLocation
from app.services.geocoding.base import GeocodingResult
from app.services.geocoding.bounds import SOFIA_MUNICIPALITY_BOUNDS, point_in_bounds

logger = logging.getLogger(__name__)

SOFIA_WRITE_BOUNDS = SOFIA_MUNICIPALITY_BOUNDS
# Official building points (scripts/import_sofia_municipal_points.py); geocoders and website
# map links never replace them.
OFFICIAL_COORDS_TAG = "coords_source=sofia_municipal"


def _normalize_address(value: Optional[str]) -> str:
    if not value:
        return ""
    normalized = value.casefold()
    normalized = re.sub(r"[\W_]+", " ", normalized, flags=re.UNICODE)
    return re.sub(r"\s+", " ", normalized).strip()


def _location_address(location: SchoolLocation) -> str:
    address_i18n = location.address_i18n or {}
    return address_i18n.get("bg") or address_i18n.get("en") or ""


def _result_meta(result: GeocodingResult, *, status: str, reason: Optional[str] = None) -> dict:
    meta = {
        "status": status,
        "provider": result.provider,
        "method": result.method,
        "precision": result.precision,
        "formatted_address": result.formatted_address,
    }
    if result.lat is not None and result.lng is not None:
        meta["candidate"] = {"lat": result.lat, "lng": result.lng}
    if reason:
        meta["rejection_reason"] = reason
    return {key: value for key, value in meta.items() if value is not None}


def _point_in_sofia_write_bounds(lat: float, lng: float) -> bool:
    return point_in_bounds(lat, lng, SOFIA_WRITE_BOUNDS)


async def apply_geocode_result_to_location(
    db: AsyncSession,
    location: SchoolLocation,
    result: GeocodingResult,
    *,
    school: Optional[School] = None,
) -> GeocodingResult:
    """Apply a geocode result to a location only if it passes write-time gates."""
    if not result.success or result.lat is None or result.lng is None:
        location.geocode_meta = _result_meta(result, status="failed", reason=result.error)
        return result

    if (
        school
        and (school.city or "").casefold() == "sofia"
        and not _point_in_sofia_write_bounds(float(result.lat), float(result.lng))
    ):
        reason = "outside_sofia_write_bounds"
        logger.warning(
            "Rejected out-of-bounds Sofia geocode for location %s: %s, %s",
            location.id,
            result.lat,
            result.lng,
        )
        location.lat = None
        location.lng = None
        location.geocode_meta = _result_meta(result, status="rejected", reason=reason)
        return GeocodingResult(
            success=False,
            error=reason,
            provider=result.provider,
            formatted_address=result.formatted_address,
            method=result.method,
            precision=result.precision,
        )

    if result.precision == "approximate":
        current_address = _normalize_address(_location_address(location))
        duplicate_result = await db.execute(
            select(SchoolLocation)
            .where(
                SchoolLocation.id != location.id,
                SchoolLocation.lat == result.lat,
                SchoolLocation.lng == result.lng,
            )
            .limit(20)
        )
        for duplicate in duplicate_result.scalars():
            if _normalize_address(_location_address(duplicate)) != current_address:
                reason = (
                    "duplicate_geojson_name_match_different_address"
                    if result.method == "geojson_name_match"
                    else "duplicate_approximate_match_different_address"
                )
                logger.warning(
                    "Rejected approximate geocode duplicate for location %s; "
                    "candidate point is already held by location %s with a different address",
                    location.id,
                    duplicate.id,
                )
                location.lat = None
                location.lng = None
                location.geocode_meta = _result_meta(result, status="rejected", reason=reason)
                return GeocodingResult(
                    success=False,
                    error=reason,
                    provider=result.provider,
                    formatted_address=result.formatted_address,
                    method=result.method,
                    precision=result.precision,
                )

    location.lat = result.lat
    location.lng = result.lng
    location.geocode_meta = _result_meta(result, status="accepted")
    return result
