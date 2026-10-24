"""Today's free kindergarten, ПГУ and 1st-grade places per school (docs/FREE_PLACES.md).

The capture workflow writes one ИСОДЗ snapshot to the public ``free-places-data`` branch
twice a day. The API reads it from there (``free_places_url``) rather than from the
launch DB, so new numbers reach the site without a data publish. Buildings are matched to
schools the way the cut-off import matches them: kindergartens by their kg.sofia.bg id in
``attributes.kg_sofia_id(s)``, school ПГУ and 1st-grade records by the Sofia school number
(``isodz_school_match``).

A snapshot older than ``MAX_AGE`` shows nothing: a stale count of free places misleads
more than none.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Iterable, Optional

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.school import School
from app.scrapers.sources.bg.isodz_school_match import match_school, number_index
from app.services.admission_cutoffs import school_index

logger = logging.getLogger(__name__)

MAX_AGE = timedelta(hours=48)
SIDE_QUEUES = ("sop", "chronic")
SCHOOL_FAMILIES = ("preparatory", "grade_1")


def snapshot_is_fresh(snapshot: dict[str, Any], now: Optional[datetime] = None) -> bool:
    raw = ((snapshot or {}).get("meta") or {}).get("retrieved_at")
    try:
        retrieved = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return False
    return (now or datetime.now(timezone.utc)) - retrieved <= MAX_AGE


def _entry(group: dict[str, Any], building: Optional[str]) -> Optional[dict[str, Any]]:
    general = (group.get("queues") or {}).get("general")
    if not general:
        return None
    entry: dict[str, Any] = {
        "birth_year": group.get("birth_year"),
        "campaign": group.get("campaign"),
        "stage": group.get("stage"),
        "age_group": group.get("age_group"),
        "label": group.get("label"),
        "building": building,
        "capacity": general.get("capacity"),
        "enrolled": general.get("enrolled"),
        "free": general.get("free"),
        "ranked_not_enrolled": general.get("ranked_not_enrolled"),
    }
    for queue in SIDE_QUEUES:
        free = ((group.get("queues") or {}).get(queue) or {}).get("free")
        if free:
            entry[f"free_{queue}"] = free
    return {key: value for key, value in entry.items() if value is not None}


async def matchable_schools(db: AsyncSession) -> list[SimpleNamespace]:
    """What matching needs of each school, and nothing else. A whole ``School`` row carries
    its admission history and website data (megabytes across the city), and loading every
    one of them in the request blocked the API long enough to fail #485's deploy check."""
    rows = await db.execute(
        select(
            School.id,
            School.institutional_id,
            School.school_type,
            School.education_level,
            School.name_i18n,
            School.attributes["kg_sofia_id"].label("kg_sofia_id"),
            School.attributes["kg_sofia_ids"].label("kg_sofia_ids"),
        ).where(School.country_code == "bg")
    )
    return [
        SimpleNamespace(
            id=row.id,
            institutional_id=row.institutional_id,
            school_type=row.school_type,
            education_level=row.education_level,
            name_i18n=row.name_i18n,
            attributes={"kg_sofia_id": row.kg_sofia_id, "kg_sofia_ids": row.kg_sofia_ids},
        )
        for row in rows
    ]


def places_by_school(snapshot: dict[str, Any], schools: Iterable[Any]) -> tuple[dict[int, list[dict[str, Any]]], int]:
    """School id -> its groups' counts, and how many buildings matched no school."""
    schools = list(schools)
    index = school_index(schools)
    numbers = number_index(schools)
    matched: dict[int, list[dict[str, Any]]] = {}
    unmatched = 0
    for building in (snapshot or {}).get("buildings") or []:
        family = building.get("family")
        # 1st-grade records have ids of their own (a separate ИСОДЗ list), which can equal a
        # kindergarten's: those match by school number only.
        school_id = None if family == "grade_1" else index.get(("kg_sofia_id", str(building.get("source_id"))))
        if school_id is None and family in SCHOOL_FAMILIES:
            school = match_school(building.get("name"), numbers)
            school_id = school.id if school else None
        if school_id is None:
            unmatched += 1
            continue
        matched.setdefault(school_id, []).append(building)
    out: dict[int, list[dict[str, Any]]] = {}
    for school_id, buildings in matched.items():
        several = len(buildings) > 1
        entries = [
            entry
            for building in buildings
            for group in building.get("groups") or []
            if (entry := _entry(group, building.get("name") if several else None)) is not None
        ]
        if entries:
            out[school_id] = sorted(
                entries,
                key=lambda e: (e.get("campaign") or "", -(e.get("birth_year") or 0), e.get("building") or ""),
            )
    return out, unmatched


# The last snapshot read and its ETag: the file changes twice a day while the API asks
# every few minutes, so an unchanged file costs a 304 and no download.
_last: dict[str, Any] = {}


async def fetch_snapshot(url: str, timeout: float = 20.0) -> dict[str, Any]:
    headers = {"If-None-Match": _last["etag"]} if _last.get("url") == url and _last.get("etag") else {}
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        response = await client.get(url, headers=headers)
    if response.status_code == 304 and headers:
        return _last["snapshot"]
    response.raise_for_status()
    snapshot = response.json()
    _last.update(url=url, etag=response.headers.get("etag"), snapshot=snapshot)
    return snapshot
