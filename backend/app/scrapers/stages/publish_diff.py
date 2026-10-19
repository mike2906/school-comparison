"""Publish diff: before a publish, compare what parents would see with what the site shows now.

The school list the site serves (``GET /schools``) is built twice: from the live API, and
in-process from the launch DB about to be published. For each school we note what a parent
sees on its card (listed at all, a pin, fees, exam results, admission information, details), and
count the schools that lose each one. A bug that wipes data (a stage that clears a column, a
gate that suddenly withholds everything) shows up as many schools losing the same thing at
once, which a normal run never does, so the publish is stopped until someone looks.

Only losses count: new data is checked by the stages that write it. No model calls; one
request to the live API.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import CRAWLER_USER_AGENT

logger = logging.getLogger(__name__)

LIVE_API = "https://api.schooldecider.com"
FEATURES = ("listed", "pin", "fees", "exam_results", "admission", "details")
# More schools than this losing one feature stops the publish: max(fixed, share of the
# schools that had it). Leaving the list entirely has a fixed, lower bar.
MIN_LOSSES = {"listed": 5}
DEFAULT_MIN_LOSSES = 10
LOSS_SHARE = 0.05


def _meaningful(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_meaningful(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_meaningful(item) for item in value)
    if isinstance(value, str):
        return bool(value.strip())
    return value is not None and value is not False


def card_features(school: dict) -> set[str]:
    """What a parent sees for one school in the list response."""
    features = {"listed"}
    if any(loc.get("lat") is not None and loc.get("lng") is not None for loc in school.get("locations") or []):
        features.add("pin")
    if school.get("pricing"):
        features.add("fees")
    if school.get("exam_results"):
        features.add("exam_results")
    if _meaningful(school.get("admission_info")):
        features.add("admission")
    if _meaningful(school.get("attributes")) or _meaningful(school.get("attributes_i18n")):
        features.add("details")
    return features


def _name(school: dict) -> str:
    names = school.get("resolved_name_i18n") or school.get("name_i18n") or {}
    return str(names.get("bg") or names.get("en") or school.get("id"))


def diff_lists(live: list[dict], new: list[dict]) -> dict[str, Any]:
    """Schools losing each feature between the live list and the new one, and whether to stop."""
    before = {school["id"]: (school, card_features(school)) for school in live}
    after = {school["id"]: card_features(school) for school in new}
    features: dict[str, dict[str, Any]] = {}
    for feature in FEATURES:
        had = [school_id for school_id, (_, have) in before.items() if feature in have]
        # A school that left the list is counted once, under "listed".
        lost = sorted(
            school_id
            for school_id in had
            if feature not in after.get(school_id, set()) and (feature == "listed" or school_id in after)
        )
        limit = MIN_LOSSES.get(feature) or max(DEFAULT_MIN_LOSSES, round(LOSS_SHARE * len(had)))
        features[feature] = {
            "before": len(had),
            "after": sum(1 for have in after.values() if feature in have),
            "lost": [{"school_id": school_id, "name": _name(before[school_id][0])} for school_id in lost],
            "limit": limit,
            "stop": len(lost) > limit,
        }
    return {
        "schools_live": len(live),
        "schools_new": len(new),
        "features": features,
        "stop": any(entry["stop"] for entry in features.values()),
    }


async def read_live_list(api: str = LIVE_API, *, country: str = "bg", city: str = "sofia") -> list[dict]:
    async with httpx.AsyncClient(headers={"User-Agent": CRAWLER_USER_AGENT}, timeout=120) as client:
        response = await client.get(f"{api.rstrip('/')}/schools", params={"country_code": country, "city": city})
        response.raise_for_status()
        return response.json()


async def build_new_list(db: AsyncSession, *, country: str = "bg", city: str = "sofia") -> list[dict]:
    # The list endpoint's own builder, with the filters an unfiltered request uses, so the
    # two sides go through the same gates.
    from app.routers.schools import _build_list_body

    body = await _build_list_body(
        db,
        country_code=country,
        city=city,
        age_group=None,
        school_type=None,
        education_level=None,
        include_crossover=False,
    )
    return json.loads(body)


_LABELS = {
    "listed": "shown on the site",
    "pin": "a pin on the map",
    "fees": "fees",
    "exam_results": "exam results",
    "admission": "admission information",
    "details": "school details",
}


def summary(report: dict) -> str:
    """One line per feature that loses schools, for the publish log."""
    lines = [
        f"{_LABELS[feature]}: {len(entry['lost'])} schools lose it ({entry['before']} -> {entry['after']}, limit {entry['limit']})"
        + (" STOP" if entry["stop"] else "")
        for feature, entry in report["features"].items()
        if entry["lost"]
    ]
    return "\n".join(lines) or "no school loses anything it shows now"


def issue_body(report: dict, site: str = "https://schooldecider.com", max_lines: int = 40) -> str:
    """The text of the GitHub issue raised when a publish is stopped."""
    lines = [
        "A publish was stopped because many schools would lose something parents see now. "
        "The live site is unchanged. Find what wiped the data in the launch DB and fix it; "
        "if the drop was made on purpose, publish once with `PUBLISH_ALLOW_DROPS=1` and say why. "
        "Close this issue once the data is back.",
        "",
        f"Schools in the list: {report['schools_live']} live, {report['schools_new']} after the publish.",
        "",
    ]
    for feature, entry in report["features"].items():
        if not entry["stop"]:
            continue
        lines.append(
            f"## {len(entry['lost'])} schools lose {_LABELS[feature]} ({entry['before']} -> {entry['after']}, limit {entry['limit']})"
        )
        for lost in entry["lost"][:max_lines]:
            name = lost["name"].replace("`", "'")
            lines.append(f"- [{lost['school_id']}]({site}/schools/{lost['school_id']}) `{name}`")
        if len(entry["lost"]) > max_lines:
            lines.append(f"- and {len(entry['lost']) - max_lines} more")
        lines.append("")
    return "\n".join(lines)
