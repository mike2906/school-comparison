"""Shared geographic bounds used for city-scoped map and geocoding guards."""

from __future__ import annotations

from typing import Optional


SOFIA_MUNICIPALITY_BOUNDS = {
    "south": 42.50,
    "west": 23.10,
    "north": 42.86,
    "east": 23.60,
}


def get_city_bounds(country_code: Optional[str], city: Optional[str]) -> dict[str, float] | None:
    if (country_code or "").casefold() == "bg" and (city or "").casefold() == "sofia":
        return SOFIA_MUNICIPALITY_BOUNDS
    return None


def point_in_bounds(lat: float, lng: float, bounds: dict[str, float]) -> bool:
    return (
        bounds["south"] <= lat <= bounds["north"]
        and bounds["west"] <= lng <= bounds["east"]
    )
