"""Repair commands for wrong coordinates: Sofia-oblast schools and pins outside the city bounds."""

from __future__ import annotations

import asyncio
import builtins
import logging
import re
from typing import Optional

from rich.console import Console
from rich.table import Table

from app.scrapers.stages import common as stage_common

logger = logging.getLogger(__name__)
console = Console()


def _reverse_result_tokens(payload: dict | None) -> set[str]:
    address = payload.get("address") if isinstance(payload, dict) else None
    if not isinstance(address, dict):
        return set()

    keys = ("city", "town", "village", "municipality", "county", "state_district", "state", "suburb")
    return {
        value.strip().lower()
        for key in keys
        if isinstance((value := address.get(key)), str) and value.strip()
    }


def _expected_locality_tokens(*values: Optional[str]) -> set[str]:
    tokens: set[str] = set()
    for value in values:
        if isinstance(value, str) and value.strip():
            tokens.add(value.strip().lower())
    return tokens


def _is_sofia_oblast_school(school) -> bool:
    attrs = dict(school.attributes or {})
    return attrs.get("moe_region_code") == 23 or attrs.get("moe_region_name") == "София-област"


def _location_out_of_bounds(location, bounds: dict[str, float]) -> bool:
    if location.lat is None or location.lng is None:
        return False
    try:
        lat = float(location.lat)
        lng = float(location.lng)
    except (TypeError, ValueError):
        return False

    from app.services.geocoding.bounds import point_in_bounds

    return not point_in_bounds(lat, lng, bounds)


def _out_of_bounds_repair_address_candidates(school, location) -> list[str]:
    attrs = dict(school.attributes or {})
    extracted = attrs.get("extracted") if isinstance(attrs.get("extracted"), dict) else {}
    contact = extracted.get("contact") if isinstance(extracted.get("contact"), dict) else {}

    raw_candidates: list[object] = [
        contact.get("address"),
        *(contact.get("addresses") if isinstance(contact.get("addresses"), builtins.list) else []),
    ]
    address_i18n = dict(location.address_i18n or {})
    raw_candidates.extend([address_i18n.get("bg"), address_i18n.get("en")])

    candidates: list[str] = []
    seen: set[str] = set()
    for candidate in raw_candidates:
        if not isinstance(candidate, str):
            continue
        text = re.sub(r"\s+", " ", candidate).strip(" ,")
        if not text:
            continue
        variants = [
            text,
            re.sub(r",?\s*(?:п\.?\s*к\.?|ПК)\s*\d{4}\b", "", text, flags=re.IGNORECASE).strip(" ,"),
        ]
        for variant in variants:
            if not variant:
                continue
            key = variant.casefold()
            if key in seen:
                continue
            seen.add(key)
            candidates.append(variant)
    return candidates


def _sanitize_oblast_query_text(value: Optional[str]) -> str:
    if not isinstance(value, str):
        return ""

    sanitized = value
    sanitized = sanitized.replace("№", " ")
    sanitized = re.sub(r'["„“”]', " ", sanitized)
    sanitized = re.sub(r"\b[Гг][Рр]\.\s*", "", sanitized)
    sanitized = re.sub(r"\b[Сс]\.\s*", "", sanitized)
    sanitized = re.sub(r"\bобщ\.\s*", "", sanitized, flags=re.IGNORECASE)
    sanitized = re.sub(r"\bобщина\b", "", sanitized, flags=re.IGNORECASE)
    sanitized = re.sub(r"\bобл\.?\s*[^,]+", "", sanitized, flags=re.IGNORECASE)
    sanitized = re.sub(r"\bет\.\s*\d+\b", "", sanitized, flags=re.IGNORECASE)
    sanitized = re.sub(r"\bх\.\s*", "хаджи ", sanitized, flags=re.IGNORECASE)
    sanitized = re.sub(r"\bСв\.\s*Св\.\s*", "Свети Свети ", sanitized, flags=re.IGNORECASE)
    sanitized = re.sub(r"([A-Za-zА-Яа-я])([IVX]{1,4})(\d)", r"\1 \2 \3", sanitized)
    sanitized = re.sub(r"([A-Za-zА-Яа-я])(\d)", r"\1 \2", sanitized)
    sanitized = re.sub(r"(\d)([A-Za-zА-Яа-я])", r"\1 \2", sanitized)
    sanitized = re.sub(r"\s*-\s*", " ", sanitized)
    sanitized = re.sub(r"\s*,\s*", ", ", sanitized)
    sanitized = re.sub(r"\s+", " ", sanitized)
    return sanitized.strip(" ,")


def _abbreviate_school_query_name(value: Optional[str]) -> str:
    sanitized = _sanitize_oblast_query_text(value)
    replacements = [
        (r"\bГимназия с преподаване на чужди езици\b", "ГПЧЕ"),
        (r"\bПрофесионална гимназия\b", "ПГ"),
        (r"\bПрофилирана гимназия\b", "ПГ"),
        (r"\bСредно училище\b", "СУ"),
        (r"\bОсновно училище\b", "ОУ"),
        (r"\bНачално училище\b", "НУ"),
    ]
    for pattern, replacement in replacements:
        sanitized = re.sub(pattern, replacement, sanitized, flags=re.IGNORECASE)
    sanitized = re.sub(r"\s+", " ", sanitized)
    return sanitized.strip(" ,")


def _oblast_fallback_query_specs(
    *,
    address: str,
    school_name: Optional[str],
    town_name: Optional[str],
    municipality_name: Optional[str],
) -> list[tuple[str, str, set[str]]]:
    from app.services.geocoding.bg.geojson import GeoJSONProvider
    from app.services.geocoding.nominatim import NominatimProvider

    provider = NominatimProvider()
    geojson_provider = GeoJSONProvider()
    normalized_address = provider._normalize_bulgarian_address(address or "")
    sanitized_address = _sanitize_oblast_query_text(normalized_address)
    sanitized_town = _sanitize_oblast_query_text(town_name)
    sanitized_municipality = _sanitize_oblast_query_text(municipality_name)
    sanitized_school = _sanitize_oblast_query_text(school_name)
    abbreviated_school = _sanitize_oblast_query_text(
        geojson_provider._normalize_name(school_name or "").title()
    )
    acronym_school = _abbreviate_school_query_name(school_name)

    road_types = {
        "road",
        "residential",
        "tertiary",
        "secondary",
        "primary",
        "unclassified",
        "service",
        "pedestrian",
    }
    poi_types = {"school", "kindergarten", "college", "university"}
    locality_types = {"administrative", "village", "town", "hamlet", "suburb"}

    specs: list[tuple[str, str, set[str]]] = []
    seen: set[tuple[str, str]] = set()

    def add(query: str, precision: str, allowed_types: set[str]) -> None:
        cleaned = _sanitize_oblast_query_text(query)
        if not cleaned:
            return
        key = (cleaned.casefold(), precision)
        if key in seen:
            return
        seen.add(key)
        specs.append((cleaned, precision, allowed_types))

    if sanitized_school and sanitized_town:
        add(f"{sanitized_school}, {sanitized_town}", "poi", poi_types)
    if abbreviated_school and abbreviated_school != sanitized_school and sanitized_town:
        add(f"{abbreviated_school}, {sanitized_town}", "poi", poi_types)
    if acronym_school and acronym_school not in {sanitized_school, abbreviated_school} and sanitized_town:
        add(f"{acronym_school}, {sanitized_town}", "poi", poi_types)

    address_tokens = [token.strip() for token in sanitized_address.split(",") if token.strip()]
    locality_tokens = {
        token.casefold()
        for token in (sanitized_town, sanitized_municipality)
        if token
    }
    detail_tokens = [token for token in address_tokens if token.casefold() not in locality_tokens]
    detail_query = ", ".join(detail_tokens)
    detail_without_number = re.sub(r"\b\d+[A-Za-zА-Яа-я-]*\b", "", detail_query).strip(" ,")
    detail_without_number = re.sub(r"\s+", " ", detail_without_number).strip(" ,")

    if sanitized_town and detail_without_number:
        add(f"{sanitized_town} {detail_without_number}", "street", road_types)
    if sanitized_town and detail_query and detail_query != detail_without_number:
        add(f"{sanitized_town} {detail_query}", "street", road_types)

    address_is_locality_only = not re.search(r"\d", sanitized_address) and (
        not detail_query or detail_query.casefold() in locality_tokens
    )
    if sanitized_town and address_is_locality_only:
        if sanitized_municipality and sanitized_municipality.casefold() != sanitized_town.casefold():
            add(f"{sanitized_town}, {sanitized_municipality}", "locality", locality_types)
        add(sanitized_town, "locality", locality_types)

    return specs


async def _search_nominatim_query(client, *, query: str, country_code: str = "bg") -> list[dict]:
    response = await client.get(
        "https://nominatim.openstreetmap.org/search",
        params={
            "format": "jsonv2",
            "q": query,
            "limit": 3,
            "addressdetails": 1,
            "countrycodes": country_code,
        },
    )
    response.raise_for_status()
    data = response.json()
    return data if isinstance(data, builtins.list) else []


async def _load_moe_lookup_maps() -> tuple[dict[int, str], dict[int, str], dict[int, str]]:
    import httpx

    from app.scrapers.sources.bg.moe_registry import MoeRegistryAdapter

    headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
    }

    async def load_map(client: httpx.AsyncClient, url: str) -> dict[int, str]:
        try:
            response = await client.post(url, json={}, headers=headers)
            response.raise_for_status()
            return MoeRegistryAdapter._extract_code_label_map(response.json())
        except Exception as exc:
            logger.warning("Failed to load MoE lookup labels from %s: %s", url, exc)
            return {}

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        return (
            await load_map(client, MoeRegistryAdapter.REGIONS_URL),
            await load_map(client, MoeRegistryAdapter.MUNICIPALITIES_URL),
            await load_map(client, MoeRegistryAdapter.TOWNS_URL),
        )


async def _reverse_geocode_payload(client, *, lat: float, lng: float) -> dict:
    response = await client.get(
        "https://nominatim.openstreetmap.org/reverse",
        params={
            "format": "jsonv2",
            "lat": lat,
            "lon": lng,
            "zoom": 18,
            "addressdetails": 1,
        },
    )
    response.raise_for_status()
    return response.json()


async def repair_oblast_geocodes_command(
    *,
    school_name: Optional[str],
    school_id: Optional[int],
    city: Optional[str],
    country: str,
    limit: Optional[int],
    dry_run: bool,
):
    import httpx
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from app.config import get_settings
    from app.database import async_session_maker
    from app.models import School
    from app.scrapers.sources.bg.moe_registry import MoeRegistryAdapter
    from app.services.geocoding.service import GeocodingService, nominatim_user_agent
    from app.services.geocoding.write_gate import has_pinned_point

    settings = get_settings()
    user_agent = nominatim_user_agent(settings)

    async with async_session_maker() as db:
        if school_name and not school_id:
            school_id = await stage_common.find_school_by_name(db, school_name, country)
            if not school_id:
                console.print(f"[red]School not found: {school_name}[/red]")
                return

        region_map, municipality_map, town_map = await _load_moe_lookup_maps()

        query = (
            select(School)
            .options(selectinload(School.locations))
            .where(
                School.country_code == country,
                School.attributes["moe_region_code"].as_integer() == MoeRegistryAdapter.SOFIA_OBLAST_REGION,
            )
        )
        if city:
            query = query.where(School.city == city)
        if school_id:
            query = query.where(School.id == school_id)
        if limit:
            query = query.limit(limit)

        schools = (await db.execute(query)).scalars().all()
        if not schools:
            console.print("[yellow]No Sofia-oblast schools matched the repair filters.[/yellow]")
            return

        candidate_rows: list[dict[str, object]] = []
        reverse_client = httpx.AsyncClient(
            timeout=30.0,
            follow_redirects=True,
            headers={"User-Agent": user_agent},
        )

        try:
            for school in schools:
                attrs = dict(school.attributes or {})
                region_code = attrs.get("moe_region_code")
                municipality_code = attrs.get("moe_municipality_code")
                town_code = attrs.get("moe_town_code")

                region_name = (
                    region_map.get(region_code) or attrs.get("moe_region_name")
                    if isinstance(region_code, int)
                    else attrs.get("moe_region_name")
                )
                municipality_name = (
                    municipality_map.get(municipality_code) or attrs.get("moe_municipality_name")
                    if isinstance(municipality_code, int)
                    else attrs.get("moe_municipality_name")
                )
                town_name = (
                    town_map.get(town_code) or attrs.get("moe_town_name")
                    if isinstance(town_code, int)
                    else attrs.get("moe_town_name")
                )

                attrs["moe_region_name"] = region_name
                attrs["moe_municipality_name"] = municipality_name
                attrs["moe_town_name"] = town_name
                school.attributes = attrs

                for location in builtins.list(school.locations or []):
                    address_i18n = dict(location.address_i18n or {})
                    address_bg = address_i18n.get("bg") or address_i18n.get("en") or ""
                    expected_city = MoeRegistryAdapter._preferred_geocoding_city(school.city, attrs)
                    normalized_address = MoeRegistryAdapter._address_with_locality_hint(address_bg, expected_city)

                    reasons: list[str] = []
                    reverse_display = ""
                    reverse_tokens: set[str] = set()
                    expected_tokens = _expected_locality_tokens(town_name, municipality_name)

                    if normalized_address and normalized_address != address_bg:
                        address_i18n["bg"] = normalized_address
                        address_i18n.pop("en", None)
                        location.address_i18n = address_i18n
                        reasons.append("address-prefixed-with-town")

                    if location.lat is None or location.lng is None:
                        reasons.append("missing-coords")
                    else:
                        payload = await _reverse_geocode_payload(client=reverse_client, lat=float(location.lat), lng=float(location.lng))
                        reverse_display = payload.get("display_name") or ""
                        reverse_tokens = _reverse_result_tokens(payload)
                        if expected_tokens and not reverse_tokens.intersection(expected_tokens):
                            reasons.append("reverse-locality-mismatch")
                        await asyncio.sleep(1.05)

                    if reasons:
                        candidate_rows.append(
                            {
                                "school_id": school.id,
                                "location_id": location.id,
                                "school_label": stage_common.school_label(school),
                                "reasons": reasons,
                                "expected_city": expected_city or "",
                                "address": dict(location.address_i18n or {}).get("bg") or "",
                                "reverse_display": reverse_display,
                            }
                        )
        finally:
            await reverse_client.aclose()

        if not candidate_rows:
            console.print("[green]No Sofia-oblast coordinate repair candidates found.[/green]")
            if not dry_run:
                await db.commit()
            return

        console.print(f"[cyan]Found {len(candidate_rows)} Sofia-oblast geocode repair candidates[/cyan]")
        preview_table = Table(title="Oblast Geocode Repair Candidates")
        preview_table.add_column("School ID", style="cyan", no_wrap=True)
        preview_table.add_column("Location ID", style="cyan", no_wrap=True)
        preview_table.add_column("School")
        preview_table.add_column("Reasons")
        preview_table.add_column("Expected locality")
        preview_table.add_column("Address")
        for row in candidate_rows[:20]:
            preview_table.add_row(
                str(row["school_id"]),
                str(row["location_id"]),
                str(row["school_label"]),
                ", ".join(builtins.list(row["reasons"])),
                str(row["expected_city"]),
                str(row["address"]),
            )
        console.print(preview_table)
        if len(candidate_rows) > 20:
            console.print(f"[dim]Showing first 20 of {len(candidate_rows)} repair candidates[/dim]")

        if dry_run:
            return

        geocoder = GeocodingService(db)
        repaired = 0
        cleared = 0
        failed = 0

        async with httpx.AsyncClient(
            timeout=30.0,
            follow_redirects=True,
            headers={"User-Agent": user_agent},
        ) as verify_client:
            for row in candidate_rows:
                school = next((item for item in schools if item.id == row["school_id"]), None)
                if school is None:
                    continue
                location = next((item for item in builtins.list(school.locations or []) if item.id == row["location_id"]), None)
                if location is None or has_pinned_point(location):
                    # Official and hand-corrected points are not this repair's to clear.
                    continue

                result = await geocoder.geocode_location(location, force=True, country_code=country)
                if result.success and result.lat is not None and result.lng is not None:
                    expected_tokens = _expected_locality_tokens(str(row["expected_city"]))
                    if expected_tokens:
                        payload = await _reverse_geocode_payload(
                            client=verify_client,
                            lat=float(result.lat),
                            lng=float(result.lng),
                        )
                        reverse_tokens = _reverse_result_tokens(payload)
                        await asyncio.sleep(1.05)
                        if not reverse_tokens.intersection(expected_tokens):
                            location.lat = None
                            location.lng = None
                            tags = builtins.list(location.location_tags or [])
                            marker = "coords_cleared=oblast_locality_mismatch"
                            if marker not in tags:
                                tags.append(marker)
                            location.location_tags = tags
                            db.add(location)
                            await db.commit()
                            cleared += 1
                            continue
                    marker = "coords_cleared=oblast_locality_mismatch"
                    tags = [tag for tag in builtins.list(location.location_tags or []) if str(tag) != marker]
                    if tags != builtins.list(location.location_tags or []):
                        location.location_tags = tags
                        db.add(location)
                        await db.commit()
                    repaired += 1
                    continue

                if "reverse-locality-mismatch" in builtins.list(row["reasons"]):
                    location.lat = None
                    location.lng = None
                    tags = builtins.list(location.location_tags or [])
                    marker = "coords_cleared=oblast_locality_mismatch"
                    if marker not in tags:
                        tags.append(marker)
                    location.location_tags = tags
                    db.add(location)
                    await db.commit()
                    cleared += 1
                else:
                    attrs = dict(school.attributes or {})
                    fallback_specs = _oblast_fallback_query_specs(
                        address=str(row["address"]),
                        school_name=stage_common.school_label(school),
                        town_name=attrs.get("moe_town_name"),
                        municipality_name=attrs.get("moe_municipality_name"),
                    )
                    expected_tokens = _expected_locality_tokens(
                        attrs.get("moe_town_name"),
                        attrs.get("moe_municipality_name"),
                    )
                    fallback_match = None

                    for query, precision, allowed_types in fallback_specs:
                        search_results = await _search_nominatim_query(
                            verify_client,
                            query=query,
                            country_code=country,
                        )
                        await asyncio.sleep(1.05)
                        for item in search_results:
                            result_tokens = _reverse_result_tokens(item)
                            if expected_tokens and not result_tokens.intersection(expected_tokens):
                                continue
                            result_type = str(item.get("type") or "").strip().lower()
                            if allowed_types and result_type not in allowed_types:
                                continue
                            lat = item.get("lat")
                            lng = item.get("lon")
                            if lat is None or lng is None:
                                continue
                            try:
                                fallback_match = (float(lat), float(lng), precision)
                            except (TypeError, ValueError):
                                continue
                            break
                        if fallback_match:
                            break

                    if fallback_match:
                        lat, lng, precision = fallback_match
                        location.lat = lat
                        location.lng = lng
                        tags = [
                            tag
                            for tag in builtins.list(location.location_tags or [])
                            if not str(tag).startswith("coords_precision=")
                            and str(tag) != "coords_source=nominatim_approximate"
                            and str(tag) != "coords_cleared=oblast_locality_mismatch"
                        ]
                        tags.append("coords_source=nominatim_approximate")
                        tags.append(f"coords_precision={precision}")
                        location.location_tags = tags
                        db.add(location)
                        await db.commit()
                        repaired += 1
                    else:
                        failed += 1

        console.print(
            f"[green]Oblast repair complete[/green] repaired={repaired} cleared={cleared} failed={failed}"
        )


async def repair_out_of_bounds_geocodes_command(
    *,
    school_name: Optional[str],
    school_id: Optional[int],
    city: Optional[str],
    country: str,
    limit: Optional[int],
    dry_run: bool,
):
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from app.config import get_settings
    from app.database import async_session_maker
    from app.models import School
    from app.services.geocoding.bounds import get_city_bounds, point_in_bounds
    from app.services.geocoding.nominatim import NominatimProvider
    from app.services.geocoding.service import nominatim_user_agent

    bounds = get_city_bounds(country, city)
    if bounds is None:
        console.print(f"[yellow]No configured city bounds for {country}/{city}; nothing to repair.[/yellow]")
        return

    async with async_session_maker() as db:
        if school_name and not school_id:
            school_id = await stage_common.find_school_by_name(db, school_name, country)
            if not school_id:
                console.print(f"[red]School not found: {school_name}[/red]")
                return

        query = (
            select(School)
            .options(selectinload(School.locations))
            .where(School.country_code == country)
        )
        if city:
            query = query.where(School.city == city)
        if school_id:
            query = query.where(School.id == school_id)
        if limit:
            query = query.limit(limit)

        schools = (await db.execute(query)).scalars().all()
        candidate_rows: list[dict[str, object]] = []
        skipped_oblast = 0

        for school in schools:
            if country == "bg" and (city or "").casefold() == "sofia" and _is_sofia_oblast_school(school):
                skipped_oblast += 1
                continue
            for location in builtins.list(school.locations or []):
                if not _location_out_of_bounds(location, bounds):
                    continue
                address_candidates = _out_of_bounds_repair_address_candidates(school, location)
                candidate_rows.append(
                    {
                        "school": school,
                        "location": location,
                        "school_id": school.id,
                        "location_id": location.id,
                        "school_label": stage_common.school_label(school),
                        "current": f"{location.lat}, {location.lng}",
                        "address": address_candidates[0] if address_candidates else "",
                        "address_candidates": address_candidates,
                    }
                )

        if not candidate_rows:
            console.print("[green]No out-of-bounds city geocode repair candidates found.[/green]")
            if skipped_oblast:
                console.print(f"[dim]Skipped {skipped_oblast} Sofia-oblast schools outside the Sofia municipality scope.[/dim]")
            return

        console.print(f"[cyan]Found {len(candidate_rows)} out-of-bounds city geocode repair candidates[/cyan]")
        if skipped_oblast:
            console.print(f"[dim]Skipped {skipped_oblast} Sofia-oblast schools outside the Sofia municipality scope.[/dim]")

        preview_table = Table(title="Out-of-Bounds Geocode Repair Candidates")
        preview_table.add_column("School ID", style="cyan", no_wrap=True)
        preview_table.add_column("Location ID", style="cyan", no_wrap=True)
        preview_table.add_column("School")
        preview_table.add_column("Current")
        preview_table.add_column("First repair address")
        for row in candidate_rows[:20]:
            preview_table.add_row(
                str(row["school_id"]),
                str(row["location_id"]),
                str(row["school_label"]),
                str(row["current"]),
                str(row["address"]),
            )
        console.print(preview_table)
        if len(candidate_rows) > 20:
            console.print(f"[dim]Showing first 20 of {len(candidate_rows)} repair candidates[/dim]")

        if dry_run:
            return

        geocoder = NominatimProvider(user_agent=nominatim_user_agent(get_settings()))
        repaired = 0
        failed = 0

        for row in candidate_rows:
            location = row["location"]
            if location is None:
                continue
            match = None
            for address in builtins.list(row["address_candidates"]):
                result = await geocoder.geocode(
                    address=str(address),
                    country_code=country,
                    city=city,
                    institution_name=(row["school"].name_i18n or {}).get("bg"),
                )
                if not result.success or result.lat is None or result.lng is None:
                    continue
                if not point_in_bounds(float(result.lat), float(result.lng), bounds):
                    logger.warning(
                        "Rejected repaired geocode outside bounds for location %s: %s, %s from %s",
                        getattr(location, "id", None),
                        result.lat,
                        result.lng,
                        address,
                    )
                    continue
                match = (result, str(address))
                break

            if not match:
                failed += 1
                continue

            result, matched_address = match
            location.lat = result.lat
            location.lng = result.lng
            tags = [
                tag
                for tag in builtins.list(location.location_tags or [])
                if not str(tag).startswith("coords_source=")
                and not str(tag).startswith("coords_repair=")
            ]
            tags.extend(
                [
                    "coords_source=nominatim_repair",
                    "coords_repair=out_of_bounds",
                ]
            )
            location.location_tags = tags
            db.add(location)
            await db.commit()
            console.print(
                f"[green]Repaired location {location.id}[/green] → "
                f"({result.lat}, {result.lng}) from {matched_address}"
            )
            repaired += 1

        console.print(f"[green]Out-of-bounds repair complete[/green] repaired={repaired} failed={failed}")
