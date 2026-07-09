"""Bootstrap the P1.5 golden-fixture corpus from the live database.

Run once (against a populated DB) to (re)generate fixtures under
``tests/golden_corpus/cases/``. Each case captures a school's real cached
website markdown plus a snapshot of the *current* deterministic extraction
output. The committed snapshots become the regression baseline — future
pricing/display-name/general-info fixes update snapshots (or add cases)
instead of gambling.

    uv run python -m scripts.build_golden_corpus

This script needs the database; the resulting tests do not.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
from pathlib import Path

from sqlalchemy import select

from app.database import async_session_maker
from app.models.school import School
from app.models.scrape_log import ScrapeType
from app.models.source_page import SourcePage
from app.utils.transliteration import transliterate_bulgarian

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.golden_corpus.harness import (  # noqa: E402
    FixturePage,
    build_pages,
    build_school,
    run_deterministic_extraction,
)

CASES_DIR = Path(__file__).resolve().parents[1] / "tests" / "golden_corpus" / "cases"

# Curated for coverage: private schools with pricing tables, an international
# school, English-primary edge cases (510), the Fusion school referenced by the
# extractor tests (520), and a couple of state schools (general-info only).
SCHOOL_IDS = [
    392, 634, 510, 520, 525, 608, 183, 151, 635, 584,
    542, 560, 565, 154, 506, 301, 625, 176, 160, 235,
]


# Keep the corpus lean: every categorized (signal-bearing) page is kept; only a
# small allowance of uncategorized pages (galleries/news carry little extraction
# signal) is retained, bounded by MAX_PAGES_PER_CASE. Expected snapshots are
# regenerated from exactly the committed pages, so the corpus stays faithful.
MAX_PAGES_PER_CASE = 8


def _trim_pages(pages: list[SourcePage]) -> list[SourcePage]:
    categorized = [p for p in pages if (p.page_category or "").strip()]
    uncategorized = [p for p in pages if not (p.page_category or "").strip()]
    allowance = max(0, MAX_PAGES_PER_CASE - len(categorized))
    kept = categorized + uncategorized[:allowance]
    kept.sort(key=lambda p: p.id)
    return kept


def _slug(text: str) -> str:
    ascii_text = transliterate_bulgarian(text).encode("ascii", "ignore").decode("ascii")
    ascii_text = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_text).strip("-").lower()
    # Drop the ubiquitous "chastna/chastno" (private) prefix for readability.
    ascii_text = re.sub(r"^chastn[oa]-", "", ascii_text)
    return ascii_text or "school"


def _detect_known_issues(expected: dict) -> list[dict]:
    """Flag snapshot fields that enshrine a *known*, plan-tracked extraction bug.

    Characterization snapshots deliberately capture current (sometimes wrong)
    behavior. These annotations keep the wrongness greppable so a bug can't gain
    silent legitimacy, and they anchor a review rule (see test_golden_corpus.py):
    a snapshot diff that touches a field WITHOUT a matching known_issues entry is
    an unexplained behavior change and must be justified in the PR.
    """
    issues: list[dict] = []
    class_size = expected.get("general_info", {}).get("extracted", {}).get("class_size")
    if isinstance(class_size, str) and re.search(r"\d", class_size):
        issues.append(
            {
                "field": "expected.general_info.extracted.class_size",
                "task": "P1.10",
                "issue": (
                    "Deterministic class_size heuristic parses bare 'N students' phrases and can "
                    "conflate teacher ratios / group sizes with real class size (e.g. '5 students')."
                ),
            }
        )
    return issues


async def build_case(session, school_id: int) -> str | None:
    school = (
        await session.execute(select(School).where(School.id == school_id))
    ).scalar_one_or_none()
    if school is None:
        print(f"  ! school {school_id} not found, skipping")
        return None

    pages_rows = (
        (
            await session.execute(
                select(SourcePage)
                .where(
                    SourcePage.school_id == school_id,
                    SourcePage.scrape_type == ScrapeType.WEBSITE,
                    SourcePage.is_valid.is_(True),
                    SourcePage.raw_markdown.isnot(None),
                )
                .order_by(SourcePage.id)
            )
        )
        .scalars()
        .all()
    )
    content_pages = [p for p in pages_rows if (p.raw_markdown or "").strip()]
    if not content_pages:
        print(f"  ! school {school_id} has no content pages, skipping")
        return None
    pages = _trim_pages(content_pages)

    name = (school.name_i18n or {}).get("en") or (school.name_i18n or {}).get("bg") or ""
    slug = f"{school_id:03d}-{_slug(name)[:32]}"
    case_dir = CASES_DIR / slug
    if case_dir.exists():
        shutil.rmtree(case_dir)
    case_dir.mkdir(parents=True)

    fixture_pages: list[FixturePage] = []
    page_meta: list[dict] = []
    for idx, page in enumerate(pages, start=1):
        filename = f"page{idx:02d}.md"
        (case_dir / filename).write_text(page.raw_markdown, encoding="utf-8")
        fixture_pages.append(
            FixturePage(
                id=page.id,
                source_url=page.source_url,
                page_category=page.page_category,
                raw_markdown=page.raw_markdown,
            )
        )
        page_meta.append(
            {
                "id": page.id,
                "source_url": page.source_url,
                "page_category": page.page_category,
                "file": filename,
            }
        )

    case = {
        "school_id": school.id,
        "name_i18n": school.name_i18n or {},
        "school_type": school.school_type,
        "country_code": school.country_code or "bg",
        "website_url": school.website_url,
        "name_aliases": list((school.attributes or {}).get("name_aliases", [])),
        "pages": page_meta,
    }

    built_school = build_school(case)
    built_pages = build_pages(fixture_pages)
    case["expected"] = run_deterministic_extraction(built_school, built_pages)
    case["known_issues"] = _detect_known_issues(case["expected"])

    (case_dir / "case.json").write_text(
        json.dumps(case, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    n_prices = len(case["expected"]["pricing"])
    has_display = bool(case["expected"]["general_info"]["display_name_i18n"])
    print(f"  + {slug}: {len(pages)} pages, {n_prices} price rows, display={has_display}")
    return slug


async def main() -> None:
    CASES_DIR.mkdir(parents=True, exist_ok=True)
    async with async_session_maker() as session:
        built = []
        for school_id in SCHOOL_IDS:
            slug = await build_case(session, school_id)
            if slug:
                built.append(slug)
    print(f"\nBuilt {len(built)} golden cases in {CASES_DIR}")


if __name__ == "__main__":
    asyncio.run(main())
