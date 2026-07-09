"""Golden-fixture corpus (P1.5).

Runs the deterministic extraction path against ~20 real cached website pages and
asserts the output matches committed snapshots. No LLM, no network, no DB — pure
deterministic regression coverage that runs in CI.

To (re)generate fixtures from the live database:
    uv run python -m scripts.build_golden_corpus

When a deterministic pricing/display-name/general-info fix intentionally changes
output, regenerate (or hand-edit) the affected ``case.json`` and review the diff.

Snapshots are characterization baselines of *current* behavior, so they can
enshrine known bugs. Each ``case.json`` carries a ``known_issues`` list naming the
fields that are wrong-on-purpose and the plan task that will fix them. Review rule:
**a snapshot diff that touches a field without a matching ``known_issues`` entry is
an unexplained behavior change and must be justified in the PR.**
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


@pytest.mark.parametrize("case_dir", CASE_DIRS, ids=[p.name for p in CASE_DIRS])
def test_known_issues_are_well_formed(case_dir: Path):
    """Each known-issue annotation must name a field, a plan task, and a description."""
    case = json.loads((case_dir / "case.json").read_text(encoding="utf-8"))
    for issue in case.get("known_issues", []):
        assert issue.get("field"), f"known_issue missing 'field' in {case_dir.name}"
        assert issue.get("task"), f"known_issue missing 'task' in {case_dir.name}"
        assert issue.get("issue"), f"known_issue missing 'issue' in {case_dir.name}"
