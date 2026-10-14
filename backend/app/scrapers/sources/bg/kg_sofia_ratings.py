"""Admission cut-offs from Sofia's ИСОДЗ (kg.sofia.bg) round lists.

kg.sofia.bg publishes, for every round (класиране) and every group, three HTML lists:
``entry`` (who waited, with the places per queue), ``rated`` (who was admitted) and
``not-rated`` (who was not). Only the current school year's rounds stay online, so the
history exists only if each round is captured while it is published.

The lists name children (initials, child number). Nothing per child leaves this module:
``aggregate_round`` reduces the three lists to one row per queue (places, applicants,
admitted, the last admitted points and whether a tie at that line was decided by the
random number). Callers store those rows only.

Endpoint notes (discovered 2026-10-14 from the site's JS bundle):
- ``GET /api/public/kg/type/{kinderGarden|preparative|school}/all?filterType=by_region&kgType=0&regionId=0``
  lists the institutions (one entry per building).
- ``GET /api/public/{0|1}/dz/{id}`` (0 = kindergarten/ПГУ, 1 = school) gives the groups
  (``currentGroups`` or ``featureGroups``), each with its birth year (``draft``), label and
  ``ratingHistory`` (round id + date).
- ``GET /api/stat-rating/{rated|not-rated|entry}/{ratingId}/{groupId}`` returns
  ``{"content": "<html>..."}``; ``content`` is null when the list does not exist.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

API_BASE = "https://kg.sofia.bg/api"
PUBLIC_SITE = "https://kg.sofia.bg/"
SOFIA_TZ = ZoneInfo("Europe/Sofia")

# Institution list type -> (stage family, dz endpoint type).
INSTITUTION_TYPES = {
    "kinderGarden": ("kindergarten", 0),
    "preparative": ("preparatory", 0),
    "school": ("grade_1", 1),
}

QUEUE_MARKERS = (
    ("СОП", "sop"),
    ("хронични", "chronic"),
    ("социални", "social"),
    ("общи", "general"),
)

PRIORITY_GROUPS = {"първа": 1, "втора": 2, "трета": 3, "четвърта": 4}

_POINTS_RE = re.compile(r"-?\d+(?:[.,]\d+)?")
_PLACES_RE = re.compile(r"за\s+(\d+)\s+места")


@dataclass
class ListEntry:
    """One child's line, reduced to what ranking needs; no identity is kept."""

    points: Optional[float]
    priority_group: Optional[int] = None
    has_random: bool = False
    changed_cohort: bool = False


@dataclass
class ListSection:
    queue: str
    places: Optional[int] = None
    entries: list[ListEntry] = field(default_factory=list)


def _queue_for_title(title: str) -> str:
    lowered = title.lower()
    for marker, queue in QUEUE_MARKERS:
        if marker.lower() in lowered:
            return queue
    # First-grade lists have one unlabelled section ("Списък класирани на ...").
    return "general"


def _parse_number(text: str) -> Optional[float]:
    match = _POINTS_RE.search(text or "")
    return float(match.group(0).replace(",", ".")) if match else None


def _parse_row(cells: list[str], changed_cohort: bool) -> Optional[ListEntry]:
    # Kindergarten: rank, initials, child number, points, random.
    # First grade:  rank, initials, child number, priority group, "Доп. точки: N", random.
    if len(cells) < 5 or not re.match(r"^\d+\.?$", cells[0]):
        return None
    if len(cells) >= 6:
        group_word = cells[3].split()[0].lower() if cells[3].split() else ""
        return ListEntry(
            points=_parse_number(cells[4]),
            priority_group=PRIORITY_GROUPS.get(group_word),
            has_random=bool(cells[5].strip()),
            changed_cohort=changed_cohort,
        )
    return ListEntry(
        points=_parse_number(cells[3]),
        has_random=bool(cells[4].strip()),
        changed_cohort=changed_cohort,
    )


def parse_list(html: Optional[str]) -> Optional[list[ListSection]]:
    """Parse one ИСОДЗ list page into sections; None when the list does not exist."""
    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")
    sections: list[ListSection] = []
    for table in soup.find_all("table"):
        section: Optional[ListSection] = None
        changed_cohort = False
        for tr in table.find_all("tr"):
            title = tr.find(class_="title_sub")
            if title is not None:
                text = " ".join(title.get_text(" ").split())
                section = ListSection(queue=_queue_for_title(text))
                places = _PLACES_RE.search(text)
                if places:
                    section.places = int(places.group(1))
                sections.append(section)
                continue
            cells = [" ".join(td.get_text(" ").split()) for td in tr.find_all("td")]
            if len(cells) == 1 and "сменен набор" in cells[0].lower():
                changed_cohort = True
                continue
            if section is None:
                continue
            entry = _parse_row(cells, changed_cohort)
            if entry is not None:
                section.entries.append(entry)
    return sections


def _rank_key(entry: ListEntry) -> tuple:
    return (entry.priority_group or 0, entry.points)


def aggregate_round(
    rated: Optional[list[ListSection]],
    not_rated: Optional[list[ListSection]],
    entry: Optional[list[ListSection]],
) -> list[dict[str, Any]]:
    """Reduce one group's three round lists to one aggregate row per queue.

    ``last_admitted_points`` (and, for first grade, ``last_admitted_group``) is the last
    line of the admitted list. ``not_admitted_at_cutoff`` counts the children with that
    same score who were not admitted, so ``tie_at_cutoff`` means the random number decided
    the line. (The admitted list shows random numbers for every tied block, even one that
    got in whole, so they say nothing about the line on their own.)

    ``applicants_count`` is the waiting list before the round. Children admitted to a
    school they ranked higher drop out of this group's admitted and not-admitted lists, so
    those two lists do not add up to it. A queue nobody applied to is left out unless it
    had places.
    """
    if rated is None:
        return []
    by_queue: dict[str, dict[str, Any]] = {}

    def bucket(queue: str) -> dict[str, Any]:
        return by_queue.setdefault(queue, {"queue": queue, "rated": [], "not_rated": [], "places": None, "waiting": None})

    for section in rated:
        bucket(section.queue)["rated"].extend(section.entries)
    for section in not_rated or []:
        bucket(section.queue)["not_rated"].extend(section.entries)
    for section in entry or []:
        data = bucket(section.queue)
        data["waiting"] = (data["waiting"] or 0) + len(section.entries)
        if section.places is not None:
            data["places"] = section.places

    rows = []
    for queue, data in by_queue.items():
        admitted: list[ListEntry] = data["rated"]
        rejected: list[ListEntry] = data["not_rated"]
        if not admitted and not rejected and not data["places"] and not data["waiting"]:
            continue
        row: dict[str, Any] = {
            "queue": queue,
            "places": data["places"],
            "applicants_count": data["waiting"],
            "admitted_count": len(admitted),
            "undersubscribed": not rejected,
            "last_admitted_points": None,
            "tie_at_cutoff": False,
            "not_admitted_at_cutoff": 0,
        }
        if admitted:
            last = admitted[-1]
            row["last_admitted_points"] = last.points
            if last.priority_group is not None:
                row["last_admitted_group"] = last.priority_group
            tied = sum(1 for item in rejected if _rank_key(item) == _rank_key(last))
            row["not_admitted_at_cutoff"] = tied
            row["tie_at_cutoff"] = tied > 0
        rows.append(row)
    return sorted(rows, key=lambda item: [q for _, q in QUEUE_MARKERS].index(item["queue"]))


# --- groups, stages and campaigns -------------------------------------------------------

_GROUP_OFFSETS = (
    (re.compile(r"(\d)\s*-?\s*(?:ва|ра|та)?\s+ясла", re.I), None),  # nursery: offset = number
    (re.compile(r"1\s*-?\s*ва\s+група", re.I), 3),
    (re.compile(r"2\s*-?\s*ра\s+група", re.I), 4),
    (re.compile(r"3\s*-?\s*та\s+група", re.I), 5),
    (re.compile(r"подготвителна", re.I), 6),
    (re.compile(r"1\s*-?\s*ви\s+клас|първи\s+клас", re.I), 7),
)

AGE_GROUP_BY_OFFSET = {0: "nursery", 1: "nursery", 2: "nursery", 3: "first", 4: "second", 5: "third", 6: "preschool"}


def start_offset(label: str) -> Optional[int]:
    """Years between birth year and the September the group starts, from its label."""
    for pattern, offset in _GROUP_OFFSETS:
        match = pattern.search(label or "")
        if match:
            return int(match.group(1)) if offset is None else offset
    return None


def campaign_for(start_year: int) -> str:
    """The repo's canonical academic-year spelling (app.utils.academic_year)."""
    return f"{start_year}/{start_year + 1}"


def stage_for(family: str, offset: int) -> str:
    if family == "grade_1":
        return "grade_1"
    if family == "preparatory":
        return "preparatory"
    return "nursery" if offset <= 2 else "kindergarten"


def rating_date(raw: Optional[str]) -> Optional[date]:
    """ИСОДЗ sends local midnight as UTC (``2026-09-03T21:00Z`` is 04.09 in Sofia)."""
    if not raw:
        return None
    return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(SOFIA_TZ).date()


@dataclass
class GroupRound:
    institution_id: int
    institution_name: str
    district: Optional[str]
    family: str
    group_id: int
    group_label: str
    birth_year: int
    rating_id: int
    classified_on: date

    @property
    def offset(self) -> Optional[int]:
        return start_offset(self.group_label)

    @property
    def stage(self) -> Optional[str]:
        return None if self.offset is None else stage_for(self.family, self.offset)

    @property
    def campaign(self) -> Optional[str]:
        return None if self.offset is None else campaign_for(self.birth_year + self.offset)

    @property
    def age_group(self) -> Optional[str]:
        if self.family == "grade_1" or self.offset is None:
            return None
        return AGE_GROUP_BY_OFFSET.get(self.offset)


def group_rounds(dz_payload: dict[str, Any], family: str) -> list[GroupRound]:
    """Every (group, round) an institution's dz record lists."""
    dz = ((dz_payload or {}).get("items") or {}).get("dz") or {}
    if not dz.get("id"):
        return []
    school_years = {
        (dz_payload.get("items") or {}).get("schoolYear"),
        (dz_payload.get("items") or {}).get("schoolYearNext"),
    }
    name = dz.get("nameFull") or dz.get("name") or ""
    out = []
    for group in (dz.get("currentGroups") or []) + (dz.get("featureGroups") or []):
        label = group.get("label") or ""
        if start_offset(label) is None or group.get("draft") is None:
            logger.warning("ИСОДЗ: unrecognised group label %r (dz %s)", label, dz.get("id"))
            continue
        for item in group.get("ratingHistory") or []:
            classified_on = rating_date(item.get("ratingDate"))
            if item.get("ratingId") is None or classified_on is None:
                continue
            candidate = GroupRound(
                institution_id=int(dz["id"]),
                institution_name=name,
                district=dz.get("region"),
                family=family,
                group_id=int(group["groupId"]),
                group_label=label,
                birth_year=int(group["draft"]),
                rating_id=int(item["ratingId"]),
                classified_on=classified_on,
            )
            if school_years - {None} and candidate.campaign not in school_years:
                logger.warning(
                    "ИСОДЗ: group %s %r derives campaign %s, outside %s; skipped",
                    candidate.group_id, label, candidate.campaign, sorted(school_years - {None}),
                )
                continue
            out.append(candidate)
    return out


# Dates the Sofia admission rules last changed, per rule family (Столичен общински съвет
# decisions; kindergarten rules also govern nurseries and ПГУ). A round is labelled with the
# latest version adopted on or before its date. Add a date here when the council amends a
# rule set; the calculator's rule files use the same `@<date>` suffix.
RULE_VERSIONS = {
    "kindergarten": ("2023-02-23", "2026-03-12"),  # Реш. 121/2023, Реш. 200/2026
    "grade_1": ("2026-03-12",),  # Реш. 201/2026
}


def rule_set_for(stage: str, campaign: str, classified_on: date) -> str:
    family = "grade_1" if stage == "grade_1" else "kindergarten"
    versions = [v for v in RULE_VERSIONS[family] if v <= classified_on.isoformat()]
    suffix = f"@{versions[-1]}" if versions else ""
    return f"bg/sofia/{family}/{campaign.replace('/', '-')}{suffix}"


_CALENDAR_PREFIX = {"grade_1": "i-ви клас", "kindergarten": "сдя, дг, пгу", "preparatory": "сдя, дг, пгу"}


def round_names(events: list[dict[str, Any]]) -> dict[tuple[str, str], str]:
    """(family, ISO date) -> round title from the ИСОДЗ calendar, e.g. "Първо класиране"."""
    names: dict[tuple[str, str], str] = {}
    for event in events or []:
        title = " ".join((event.get("title") or "").split())
        lowered = title.lower()
        if "класиране" not in lowered or not event.get("start"):
            continue
        for family, prefix in _CALENDAR_PREFIX.items():
            if lowered.startswith(prefix):
                # "СДЯ, ДГ, ПГУ - 17:00 ч. - Текущо класиране" -> "Текущо класиране"
                names[(family, event["start"])] = title.split(" - ")[-1].strip()
    return names


def rows_for_round(
    round_: GroupRound,
    aggregates: list[dict[str, Any]],
    retrieved_at: str,
    round_name: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Archive rows: the round's labels around each queue aggregate."""
    rows = []
    for aggregate in aggregates:
        row = {
            "country": "bg",
            "city": "sofia",
            "stage": round_.stage,
            "campaign": round_.campaign,
            "rule_set": rule_set_for(round_.stage, round_.campaign, round_.classified_on),
            "round_id": round_.rating_id,
            "classified_on": round_.classified_on.isoformat(),
            "round_name": round_name,
            "round_kind": None if not round_name else ("current" if "текущо" in round_name.lower() else "general"),
            "source": "kg.sofia.bg",
            "source_id": str(round_.institution_id),
            "institution_name": round_.institution_name,
            "district": round_.district,
            "group_id": round_.group_id,
            "group_label": round_.group_label,
            "birth_year": round_.birth_year,
            "age_group": round_.age_group,
            **aggregate,
            "source_url": f"{API_BASE}/stat-rating/rated/{round_.rating_id}/{round_.group_id}",
            "retrieved_at": retrieved_at,
        }
        rows.append(row)
    return rows


# --- HTTP -------------------------------------------------------------------------------


class IsodzClient:
    """Polite sequential client: one request at a time with a fixed gap, three tries."""

    def __init__(self, client: httpx.AsyncClient, delay: float = 1.0):
        self.client = client
        self.delay = delay
        self.requests = 0

    async def get_json(self, path: str, params: Optional[dict[str, Any]] = None) -> Any:
        last_error: Optional[Exception] = None
        for attempt in range(3):
            if self.requests:
                await asyncio.sleep(self.delay * (attempt + 1))
            self.requests += 1
            try:
                response = await self.client.get(f"{API_BASE}{path}", params=params)
                response.raise_for_status()
                return response.json()
            except (httpx.HTTPError, ValueError) as error:
                last_error = error
                logger.warning("ИСОДЗ %s failed (attempt %s): %s", path, attempt + 1, error)
        raise RuntimeError(f"ИСОДЗ {path} failed after 3 attempts: {last_error}")

    async def institutions(self, list_type: str) -> list[dict[str, Any]]:
        payload = await self.get_json(
            f"/public/kg/type/{list_type}/all",
            {"filterType": "by_region", "kgType": 0, "regionId": 0},
        )
        items = (payload or {}).get("items") or {}
        for key in ("kinderGardens", "schools", "preparatives", "list"):
            if isinstance(items.get(key), list):
                return items[key]
        lists = [value for value in items.values() if isinstance(value, list) and value and isinstance(value[0], dict) and "id" in value[0] and "nameStr" in value[0]]
        return lists[0] if lists else []

    async def dz(self, endpoint_type: int, institution_id: int) -> dict[str, Any]:
        return await self.get_json(f"/public/{endpoint_type}/dz/{institution_id}")

    async def round_list(self, kind: str, rating_id: int, group_id: int) -> Optional[list[ListSection]]:
        payload = await self.get_json(f"/stat-rating/{kind}/{rating_id}/{group_id}")
        return parse_list((payload or {}).get("content"))

    async def calendar(self) -> list[dict[str, Any]]:
        payload = await self.get_json("/public/calendarEvents")
        return payload if isinstance(payload, list) else []
