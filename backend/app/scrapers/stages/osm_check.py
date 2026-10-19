"""OSM-check stage: list schools whose published pin, number or website disagrees with OpenStreetMap.

OSM is mapped independently of our sources, and Sofia's schools and kindergartens there carry
the MoE code (``ref:BG:neispuo``, on about three in four in Oct 2026), so a school matches its
OSM features by code, not by name. A disagreement is a lead to check against the school's own
site or an official source, never a correction: the stage writes nothing. Flags:

* ``pin_far``: none of our pinned locations is within ``FAR_M`` of an OSM feature with the code.
* ``number_differs``: the school number in our name and OSM's name share nothing ("ДГ №97" vs "ДГ №79").
* ``website_differs``: both name a website and the hosts differ.

No model calls; one Overpass query. Schools OSM doesn't tag are not checked.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import School, SchoolLocation
from app.scrapers.extraction.text_values import _canonical_host
from app.scrapers.stages.website_pins import needs_pin
from app.services.geocoding.bg.sofia_address_register import distance_m
from app.services.geocoding.bounds import get_city_bounds

logger = logging.getLogger(__name__)

NEISPUO_TAG = "ref:BG:neispuo"
# Further than this from every OSM feature of the school: a wrong building, not a gate or wing.
FAR_M = 300
# Hosts that are not a school's own site: two schools' pages there say nothing about each other.
_SHARED_HOSTS = ("facebook.com", "instagram.com", "google.com", "sites.google.com", "wixsite.com")

_NUMBER_RE = re.compile(r"(?:^|№\s*)(\d{1,3})(?!\d)")


def neispuo_codes(tags: dict) -> set[str]:
    """The MoE codes on an OSM feature (one tag may list several, ``;``-separated)."""
    return {code.strip().lstrip("0") for code in str(tags.get(NEISPUO_TAG) or "").split(";") if code.strip().strip("0")}


def name_numbers(name: Optional[str]) -> set[str]:
    """School numbers in a name: a leading "115." or "115 ОУ", or one after "№"."""
    return {match.lstrip("0") for match in _NUMBER_RE.findall((name or "").strip()) if match.strip("0")}


def site_hosts(url: Optional[str]) -> set[str]:
    """Hosts of a website tag or URL; OSM may list several, ``;``-separated."""
    hosts = set()
    for part in str(url or "").split(";"):
        part = part.strip()
        if part and (host := _canonical_host(part if "://" in part else f"https://{part}")):
            hosts.add(host)
    return hosts


def _same_site(a: str, b: str) -> bool:
    return a == b or a.endswith("." + b) or b.endswith("." + a)


def _is_shared(host: str) -> bool:
    return any(host == shared or host.endswith("." + shared) for shared in _SHARED_HOSTS)


def check_school(
    school_name: Optional[str],
    website_url: Optional[str],
    points: Sequence[tuple[float, float]],
    features: Sequence[dict],
) -> list[dict[str, Any]]:
    """The disagreements between one school and its OSM features (all carry its code)."""
    flags: list[dict[str, Any]] = []
    osm_points = [
        (float(c["lat"]), float(c["lon"]))
        for f in features
        if (c := f.get("center") or f).get("lat") is not None and c.get("lon") is not None
    ]
    if points and osm_points:
        nearest = min(distance_m(p, o) for p in points for o in osm_points)
        if nearest > FAR_M:
            flags.append({"flag": "pin_far", "metres": round(nearest), "osm_points": osm_points[:5]})
    ours = name_numbers(school_name)
    osm_names = {
        t.get(k) for f in features for t in [f.get("tags") or {}] for k in ("name", "official_name") if t.get(k)
    }
    theirs = set().union(*(name_numbers(n) for n in osm_names)) if osm_names else set()
    if ours and theirs and not ours & theirs:
        flags.append({"flag": "number_differs", "osm_names": sorted(osm_names)})
    ours_hosts = {h for h in site_hosts(website_url) if not _is_shared(h)}
    osm_hosts = {
        h
        for f in features
        for t in [f.get("tags") or {}]
        for k in ("website", "contact:website")
        for h in site_hosts(t.get(k))
        if not _is_shared(h)
    }
    if ours_hosts and osm_hosts and not any(_same_site(a, b) for a in ours_hosts for b in osm_hosts):
        flags.append({"flag": "website_differs", "osm_websites": sorted(osm_hosts)})
    return flags


async def read_osm(country: str = "bg", city: str = "sofia") -> list[dict]:
    """Every named OSM school and kindergarten in the city (pin-agent's query); raises if no server answers."""
    import httpx

    from app.config import CRAWLER_USER_AGENT
    from app.scrapers.stages.pin_agent import CityOsm

    bounds = get_city_bounds(country, city)
    if bounds is None:
        raise ValueError(f"no bounds for {country}/{city}")
    async with httpx.AsyncClient(headers={"User-Agent": CRAWLER_USER_AGENT}) as client:
        city_osm = CityOsm(client, bounds)
        elements = await city_osm(0.0, 0.0, radius=None)
    if city_osm.failed:
        raise RuntimeError(city_osm.failed)
    return elements


async def run_osm_check(
    db: AsyncSession,
    osm_elements: Sequence[dict],
    *,
    country: str = "bg",
    city: str = "sofia",
) -> dict[str, Any]:
    """Compare the city's schools with OSM features (from ``CityOsm``); returns the report."""
    by_code: dict[str, list[dict]] = {}
    for element in osm_elements:
        for code in neispuo_codes(element.get("tags") or {}):
            by_code.setdefault(code, []).append(element)
    schools = (
        (
            await db.execute(
                select(School)
                .where(School.country_code == country, func.lower(School.city) == city.lower())
                .order_by(School.id)
            )
        )
        .scalars()
        .all()
    )
    pins: dict[int, list[tuple[float, float]]] = {}
    locations = select(SchoolLocation).where(
        SchoolLocation.lat.is_not(None), SchoolLocation.school_id.in_([school.id for school in schools])
    )
    for location in (await db.execute(locations)).scalars():
        if not needs_pin(location):  # an approximate point is hidden, so not worth checking
            pins.setdefault(location.school_id, []).append((location.lat, location.lng))
    results, checked = [], 0
    for school in schools:
        features = by_code.get((school.institutional_id or "").strip().lstrip("0"))
        if not features:
            continue
        checked += 1
        name = (school.name_i18n or {}).get("bg")
        flags = check_school(name, school.website_url, pins.get(school.id, []), features)
        if flags:
            results.append({"school_id": school.id, "name": name, "flags": flags})
    counts: dict[str, int] = {}
    for result in results:
        for flag in result["flags"]:
            counts[flag["flag"]] = counts.get(flag["flag"], 0) + 1
    return {
        "schools": len(schools),
        "checked": checked,
        "osm_features": len(osm_elements),
        "osm_with_code": sum(len(v) for v in by_code.values()),
        "flags": counts,
        "results": results,
    }


_FLAG_WORDS = {
    "pin_far": lambda f: f"pin {f['metres']} m from OSM's",
    "number_differs": lambda f: f"number differs from OSM's {', '.join(map(_plain, f['osm_names']))}",
    "website_differs": lambda f: f"website differs from OSM's {', '.join(map(_plain, f['osm_websites']))}",
}


def _plain(text: str) -> str:
    """OSM text as an inline code span: no links, mentions or formatting from a stranger's edit."""
    return "`" + str(text).replace("`", "'").replace("\n", " ")[:120] + "`"


def issue_body(report: dict[str, Any], site: str = "https://schooldecider.com") -> str:
    """Markdown for the rolling GitHub issue: one line per school."""
    lines = [
        f"{len(report['results'])} of {report['checked']} schools OSM tags with their MoE code disagree with what we publish.",
        "Each is a lead: check the school's own website or an official source before changing anything,",
        "and never copy OSM's value as the fix (backend/app/scrapers/stages/osm_check.py).",
        "",
    ]
    for result in report["results"]:
        words = "; ".join(_FLAG_WORDS[f["flag"]](f) for f in result["flags"])
        lines.append(f"- [{result['school_id']}]({site}/schools/{result['school_id']}) {result['name']}: {words}")
    lines += ["", "Map data © OpenStreetMap contributors, ODbL."]
    return "\n".join(lines)
