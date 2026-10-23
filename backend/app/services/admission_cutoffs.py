"""Admission cut-off archive: versioned JSON files of aggregate round results.

Official sites keep only the current campaign online (kg.sofia.bg) or publish one file
per round (РУО min/max scores), so the history lives in our own archive. The capture job
writes it to the ``cutoff-data`` branch; ``docs/ADMISSION_CUTOFFS.md`` describes the
layout and the row fields.

Layout: ``<country>/<city>/<stage>/<campaign-slug>/round-<round_id>.json``, each file
``{"meta": {...}, "rows": [...]}``. Rows hold aggregates only, never a child's line.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Iterator

ROW_SORT_KEYS = ("source_id", "group_id", "class_code", "queue")
FORBIDDEN_ROW_KEYS = frozenset({"initials", "child_number", "random_number", "children", "entries"})


def campaign_slug(campaign: str) -> str:
    return campaign.replace("/", "-")


def round_path(root: Path, row: dict[str, Any]) -> Path:
    return (
        root
        / row["country"]
        / row["city"]
        / row["stage"]
        / campaign_slug(row["campaign"])
        / f"round-{row['round_id']}.json"
    )


def _sort_key(row: dict[str, Any]) -> tuple:
    return tuple(str(row.get(key) if row.get(key) is not None else "") for key in ROW_SORT_KEYS)


def _identity(row: dict[str, Any]) -> tuple:
    return (row.get("source"), row.get("source_id"), row.get("group_id"), row.get("class_code"), row.get("queue"))


def check_row(row: dict[str, Any]) -> None:
    missing = [key for key in ("country", "city", "stage", "campaign", "round_id", "source", "source_id", "queue") if row.get(key) in (None, "")]
    if missing:
        raise ValueError(f"cut-off row is missing {missing}: {row}")
    leaked = FORBIDDEN_ROW_KEYS & set(row)
    if leaked:
        raise ValueError(f"cut-off row carries per-child fields {sorted(leaked)}")


def write_rows(root: Path, rows: Iterable[dict[str, Any]]) -> list[Path]:
    """Merge rows into their round files (a row replaces the one with the same identity)."""
    by_path: dict[Path, list[dict[str, Any]]] = {}
    for row in rows:
        check_row(row)
        by_path.setdefault(round_path(root, row), []).append(row)
    written = []
    for path, new_rows in by_path.items():
        existing = read_file(path)["rows"] if path.exists() else []
        merged = {_identity(row): row for row in existing}
        merged.update({_identity(row): row for row in new_rows})
        first = new_rows[0]
        payload = {
            "meta": {
                key: first.get(key)
                for key in ("country", "city", "stage", "campaign", "round_id", "classified_on", "source", "rule_set")
            },
            "rows": sorted(merged.values(), key=_sort_key),
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        written.append(path)
    return written


def read_file(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def iter_rows(root: Path) -> Iterator[dict[str, Any]]:
    for path in sorted(root.glob("*/*/*/*/round-*.json")):
        yield from read_file(path)["rows"]


def load_state(root: Path) -> dict[str, Any]:
    path = root / "_state.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def save_state(root: Path, state: dict[str, Any]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "_state.json").write_text(json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")


# --- per-school projection (the import writes these into ``schools.admission_info``) ----

PUBLIC_ROW_FIELDS = (
    "stage", "campaign", "rule_set", "round_id", "round_name", "round_kind", "classified_on",
    "age_group", "birth_year", "queue", "building", "places", "applicants_count", "admitted_count",
    "undersubscribed", "last_admitted_points", "last_admitted_group", "tie_at_cutoff",
    "not_admitted_at_cutoff", "class_code", "class_name", "quota", "min_score", "max_score",
    "min_score_boys", "min_score_girls", "source_url",
)
SUMMARY_ROUND_FIELDS = (
    "round_id", "round_name", "round_kind", "classified_on", "places", "applicants_count",
    "admitted_count", "undersubscribed", "last_admitted_points", "last_admitted_group",
    "tie_at_cutoff", "min_score", "max_score", "min_score_boys", "min_score_girls",
)


def _campaign_start(campaign: str) -> int:
    return int(str(campaign)[:4])


def public_row(row: dict[str, Any], building: Any) -> dict[str, Any]:
    out = {key: row.get(key) for key in PUBLIC_ROW_FIELDS if key != "building"}
    out["building"] = building
    return {key: value for key, value in out.items() if value is not None}


def _stages_with_several_buildings(rows: list[dict[str, Any]]) -> set[str]:
    sources: dict[str, set[str]] = {}
    for row in rows:
        sources.setdefault(row["stage"], set()).add(str(row["source_id"]))
    return {stage for stage, ids in sources.items() if len(ids) > 1}


def school_cutoffs(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """All of one school's archive rows, trimmed for the page, newest first.

    ``building`` names the source institution only when the school has several (a
    kindergarten with branch buildings ranks each one separately) within that stage.
    """
    several = _stages_with_several_buildings(rows)
    out = [public_row(row, row.get("institution_name") if row["stage"] in several else None) for row in rows]
    return sorted(
        out,
        key=lambda r: (
            r.get("classified_on") or "", r.get("stage") or "", r.get("age_group") or "",
            r.get("class_code") or "", r.get("building") or "", r.get("queue") or "",
        ),
        reverse=True,
    )


def school_cutoff_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The small list-response view: for the latest campaign, per stage x age group x birth
    year (or 8th-grade class) x building, the general-queue result of the first and the
    latest round. A nursery group ranks two birth years separately, so they are never mixed.

    The latest campaign is chosen per stage and building before grouping, so a class or
    group offered in an earlier year only (8th-grade class codes are unique within a year)
    never shows as current.
    """
    general = [row for row in rows if row.get("queue") == "general"]
    if not general:
        return []
    several = _stages_with_several_buildings(rows)
    latest_by_stage: dict[tuple, int] = {}
    for row in general:
        key = (row["stage"], row["source_id"])
        latest_by_stage[key] = max(latest_by_stage.get(key, 0), _campaign_start(row["campaign"]))
    groups: dict[tuple, list[dict[str, Any]]] = {}
    for row in general:
        if _campaign_start(row["campaign"]) != latest_by_stage[(row["stage"], row["source_id"])]:
            continue
        key = (row["stage"], row.get("age_group"), row.get("birth_year"), row.get("class_code"), row["source_id"])
        groups.setdefault(key, []).append(row)
    summary = []
    for (stage, age_group, birth_year, class_code, _source_id), items in groups.items():
        in_campaign = sorted(items, key=lambda r: (r.get("classified_on") or "", r["round_id"]))
        latest_campaign = in_campaign[0]["campaign"]
        first, latest = in_campaign[0], in_campaign[-1]
        entry = {
            "stage": stage,
            "age_group": age_group,
            "birth_year": birth_year,
            "class_code": class_code,
            "class_name": first.get("class_name"),
            "quota": first.get("quota"),
            "building": first.get("institution_name") if stage in several else None,
            "campaign": latest_campaign,
            "rule_set": first.get("rule_set"),
            "rounds": len(in_campaign),
            "first": {k: first.get(k) for k in SUMMARY_ROUND_FIELDS if first.get(k) is not None},
            "latest": {k: latest.get(k) for k in SUMMARY_ROUND_FIELDS if latest.get(k) is not None},
        }
        summary.append({k: v for k, v in entry.items() if v is not None})
    return sorted(
        summary,
        key=lambda e: (e["stage"], e.get("age_group") or "", -(e.get("birth_year") or 0), e.get("class_code") or "", e.get("building") or ""),
    )


def match_key(row: dict[str, Any]) -> tuple[str, str]:
    """How a row finds its school: kg.sofia.bg rows by institution id, РУО rows by НЕИСПУО code."""
    if row.get("source") == "kg.sofia.bg":
        return ("kg_sofia_id", str(row["source_id"]))
    return ("institutional_id", str(row["source_id"]))


def school_index(schools: list[Any]) -> dict[tuple[str, str], int]:
    """(match kind, id) -> school id. A school's own kg_sofia_id wins over a family listing."""
    index: dict[tuple[str, str], int] = {}
    for school in sorted(schools, key=lambda s: s.id):
        attrs = school.attributes or {}
        for kg_id in attrs.get("kg_sofia_ids") or []:
            if kg_id is not None:
                index.setdefault(("kg_sofia_id", str(kg_id)), school.id)
    # Highest id first, so on a shared id the lowest school id wins, the same on every run.
    for school in sorted(schools, key=lambda s: s.id, reverse=True):
        attrs = school.attributes or {}
        if attrs.get("kg_sofia_id") is not None:
            index[("kg_sofia_id", str(attrs["kg_sofia_id"]))] = school.id
        if school.institutional_id:
            index[("institutional_id", str(school.institutional_id))] = school.id
    return index
