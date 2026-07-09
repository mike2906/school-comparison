"""Golden-fixture corpus (P1.5).

Runs the deterministic extraction path against ~20 real cached website pages and
asserts the output matches committed snapshots. No LLM, no network, no DB — pure
deterministic regression coverage that runs in CI.

To (re)generate fixtures from the live database:
    uv run python -m scripts.build_golden_corpus

When a deterministic pricing/display-name/general-info fix intentionally changes
output, regenerate (or hand-edit) the affected ``case.json`` and review the diff.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.golden_corpus.harness import (
    FixturePage,
    build_pages,
    build_school,
    run_deterministic_extraction,
)

CASES_DIR = Path(__file__).parent / "golden_corpus" / "cases"


def _load_cases() -> list[Path]:
    if not CASES_DIR.exists():
        return []
    return sorted(p for p in CASES_DIR.iterdir() if (p / "case.json").exists())


CASE_DIRS = _load_cases()


def test_corpus_is_present():
    """Guard against an empty/misplaced corpus silently passing everything."""
    assert len(CASE_DIRS) >= 15, (
        f"Expected the golden corpus to hold >=15 cases, found {len(CASE_DIRS)}. "
        "Regenerate with `uv run python -m scripts.build_golden_corpus`."
    )


@pytest.mark.parametrize("case_dir", CASE_DIRS, ids=[p.name for p in CASE_DIRS])
def test_deterministic_extraction_matches_snapshot(case_dir: Path):
    case = json.loads((case_dir / "case.json").read_text(encoding="utf-8"))

    fixture_pages = [
        FixturePage(
            id=page["id"],
            source_url=page["source_url"],
            page_category=page["page_category"],
            raw_markdown=(case_dir / page["file"]).read_text(encoding="utf-8"),
        )
        for page in case["pages"]
    ]

    school = build_school(case)
    pages = build_pages(fixture_pages)
    result = run_deterministic_extraction(school, pages)

    assert result == case["expected"]
