"""Today's free places in Sofia's municipal kindergartens, ПГУ and 1st grade, from ИСОДЗ (kg.sofia.bg).

Every building's ``GET /api/public/{0|1}/dz/{id}`` record (1 = a school's 1st grade) lists its groups (birth year,
label) with ``spots`` per queue: ``capacity``, ``zapisani`` (enrolled), ``svobodni`` (free)
and ``nezapisani`` (ranked in a round but not yet enrolled, so the place is held for them).
These are the numbers kg.sofia.bg shows on a kindergarten's page and in its "Свободни
места" view. -1 means the queue does not apply. Counts only: nothing names a child.

``currentGroups`` are this school year's groups; ``featureGroups`` (when present, from the
spring) are next year's. Docs: docs/FREE_PLACES.md
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from app.scrapers.sources.bg.kg_sofia_ratings import (
    AGE_GROUP_BY_OFFSET,
    campaign_for,
    stage_for,
    start_offset,
)

logger = logging.getLogger(__name__)

# ИСОДЗ queue type -> ours (the cut-off archive's names). "workgroup" is always -1.
QUEUES = {"general": "general", "sop": "sop", "chronic": "chronic", "social": "social"}
SPOT_FIELDS = {"capacity": "capacity", "zapisani": "enrolled", "svobodni": "free", "nezapisani": "ranked_not_enrolled"}


def _count(value: Any) -> Optional[int]:
    """A count, or None for ИСОДЗ's -1 ("does not apply") and anything not a number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return None
    return int(value)


def _queues(spots: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for spot in spots or []:
        queue = QUEUES.get(spot.get("type"))
        if queue is None:
            continue
        counts = {ours: _count(spot.get(theirs)) for theirs, ours in SPOT_FIELDS.items()}
        counts = {key: value for key, value in counts.items() if value is not None}
        # A queue with no capacity offers nothing (most СОП and chronic-illness queues).
        if counts.get("capacity"):
            out[queue] = counts
    return out


def building_free_places(dz_payload: dict[str, Any], family: str) -> Optional[dict[str, Any]]:
    """One building's groups with their counts per queue; None when the record is empty.

    ``family`` is the institution list it came from: ``kindergarten``, ``preparatory`` or
    ``grade_1``.
    """
    items = (dz_payload or {}).get("items") or {}
    dz = items.get("dz") or {}
    if not dz.get("id"):
        return None
    school_years = {items.get("schoolYear"), items.get("schoolYearNext")} - {None}
    groups = []
    for key in ("currentGroups", "featureGroups"):
        for group in dz.get(key) or []:
            label = group.get("label") or ""
            offset = start_offset(label)
            if offset is None or group.get("draft") is None or group.get("groupId") is None:
                logger.warning("ИСОДЗ: unrecognised group label %r (dz %s)", label, dz.get("id"))
                continue
            if group.get("active") == 0:
                continue
            birth_year = int(group["draft"])
            campaign = campaign_for(birth_year + offset)
            if school_years and campaign not in school_years:
                logger.warning("ИСОДЗ: group %s %r derives campaign %s; skipped", group["groupId"], label, campaign)
                continue
            queues = _queues(group.get("spots") or [])
            if not queues:
                continue
            groups.append({
                "group_id": int(group["groupId"]),
                "label": label,
                "birth_year": birth_year,
                "campaign": campaign,
                "stage": stage_for(family, offset),
                "age_group": AGE_GROUP_BY_OFFSET.get(offset),
                "groups": group.get("countGroups"),
                "queues": queues,
            })
    if not groups:
        return None
    groups.sort(key=lambda g: (g["campaign"], -g["birth_year"], g["group_id"]))
    return {
        "source_id": str(dz["id"]),
        "family": family,
        "name": dz.get("nameFull") or dz.get("name"),
        "district": dz.get("region"),
        "groups": groups,
    }
