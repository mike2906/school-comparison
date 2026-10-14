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
