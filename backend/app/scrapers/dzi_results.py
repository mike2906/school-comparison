"""Bulgaria ДЗИ (state matura exam) results importer.

МОН publishes per-school ДЗИ results on data.egov.bg, one CSV per exam session. This
imports the mandatory May–June session: per school and subject, the number of pupils
who sat it ("Бр.") and their average grade ("Ср.усп.", the 2–6 scale), stored in
``exam_results`` as ``exam_type="dzi"``, ``metric="average_grade"``.

Grades are not NVO points: the two never share a row, a sort or a benchmark.

Unverified (data.egov.bg is unreachable from where this was written): the layouts below
come from a third-party parser that reads these files (atanasster/electionsbg,
``scripts/schools/build_index.ts``), not from the files themselves. The parser fails a
year loudly rather than guess when a layout does not fit:
- 2022–2025, 2026: one header row with "Бр. БЕЛ(ООП) З" / "Ср.усп. БЕЛ(ООП) З" pairs
  (2026 has line breaks inside the quoted header cells);
- 2023: a three-row header (subject, then "З", then "Бр." / "Ср.усп.");
- 2022–2023 name the code column "Код по Админ", later years "Код по НЕИСПУО".
The dataset is a rolling window of about ten resources: older sessions drop off the
portal (the 2022 one did on 2026-10-03), so years already imported are kept, never
deleted. 2018–2021 were never published there.
"""

from __future__ import annotations

import csv
import io
import logging
import re
from datetime import UTC, datetime
from typing import Optional
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.scrape_log import ScrapeLog, ScrapeStatus, ScrapeType
from app.scrapers.nvo_results import (
    NvoImportError,
    NvoResource,
    NvoSliceError,
    ParsedExamResult,
    _clean_institutional_id,
    _collapse_ws,
    _fetch_text,
    _load_school_match_index,
    _normalize_city_key,
    _normalize_header,
    _parse_float,
    _upsert_resource_rows,
)

logger = logging.getLogger(__name__)

DZI_DATASET_URL = "https://data.egov.bg/data/view/066b4b04-d81d-444e-a61c-8ca0516079e4"
DZI_EXAM_TYPE = "dzi"
DZI_METRIC = "average_grade"
DEFAULT_HISTORY_YEARS = 5
GRADE_MIN = 2.0
GRADE_MAX = 6.0
# Share of grades outside 2–6 above which a file is taken to be in another unit (points)
# and the whole year is refused. Below it, the odd bad cell is skipped and counted.
MAX_OUT_OF_RANGE_SHARE = 0.05

_RESOURCE_ID_RE = re.compile(r"/data/resourceView/([0-9a-f-]{36})")
_ACADEMIC_YEAR_RE = re.compile(r"(20\d{2})\s*/\s*(20\d{2})")
_MARKER = r"(?P<marker>бр\.?|ср\.?\s*усп\.?)"
_MARKER_ONLY_RE = re.compile(rf"^{_MARKER}$", re.IGNORECASE)
_MARKER_FIRST_RE = re.compile(rf"^{_MARKER}\s+(?P<label>.+)$", re.IGNORECASE)
_MARKER_LAST_RE = re.compile(rf"^(?P<label>.+?)\s+{_MARKER}$", re.IGNORECASE)
_CURRICULUM_RE = re.compile(r"\((ооп|пп)\)", re.IGNORECASE)
# CEFR level of a language exam ("B1", "B2", "B1.1"); МОН may type the letter in Cyrillic.
_CEFR_RE = re.compile(r"(?<![^\s(])([AaBbАаВв][12](?:\.\d)?)(?![^\s()])")
_CYRILLIC_TO_LATIN = str.maketrans({"А": "A", "а": "a", "В": "B", "в": "b"})

# Subject abbreviations in the file headers → our subject keys. Only "БЕЛ" and "Мат"
# are confirmed; the rest are МОН's usual short forms and unverified. A subject that
# matches none is not imported and is listed in the summary, so a new one is added here
# rather than shown under a made-up name.
_SUBJECT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern, re.IGNORECASE), key)
    for pattern, key in (
        (r"^бел", "bulgarian"),
        (r"^мат", "math"),
        (r"^ист", "history"),
        (r"^геогр?", "geography"),
        (r"^фил", "philosophy"),
        (r"^био", "biology"),
        (r"^физ", "physics"),
        (r"^хим", "chemistry"),
        (r"^инф", "informatics"),
        (r"^ит$", "it"),
        (r"^(ае|англ)", "english"),
        (r"^(не|нем)", "german"),
        (r"^(фе|фр)", "french"),
        (r"^(ие|исп)", "spanish"),
        (r"^(ите|итал)", "italian"),
        (r"^(ре|рус)", "russian"),
    )
)


class DziSliceError(NvoSliceError):
    """Raised when a single ДЗИ session file is invalid."""


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def parse_dzi_dataset_resources(html: str, dataset_url: str = DZI_DATASET_URL) -> list[NvoResource]:
    """The mandatory May–June session per year, newest first.

    Re-sits (August–September) and the optional ("по желание") exams are left out.
    """
    soup = BeautifulSoup(html, "html.parser")
    by_year: dict[int, NvoResource] = {}

    for anchor in soup.select('a[href*="/data/resourceView/"]'):
        href = urljoin(dataset_url, anchor["href"])
        resource_id = _RESOURCE_ID_RE.search(href)
        if not resource_id:
            continue
        title = _collapse_ws(anchor.get_text(" ", strip=True))
        lowered = title.lower()
        if "задължителн" not in lowered or "май" not in lowered:
            continue
        year = _parse_session_year(title)
        if year is None or year in by_year:
            continue
        by_year[year] = NvoResource(
            exam_type=DZI_EXAM_TYPE,
            year=year,
            title=title,
            dataset_url=dataset_url,
            resource_view_url=href,
            download_url=f"https://data.egov.bg/resource/download/{resource_id.group(1)}/csv",
        )

    return [by_year[year] for year in sorted(by_year, reverse=True)]


def _parse_session_year(title: str) -> Optional[int]:
    # "учебна 2025/2026 година" is the 2026 session; otherwise the year the title names.
    match = _ACADEMIC_YEAR_RE.search(title)
    if match:
        return int(match.group(2))
    years = re.findall(r"20\d{2}", title)
    return int(years[-1]) if years else None


def subject_key(label: str) -> Optional[str]:
    """``"БЕЛ(ООП) З"`` → ``"bulgarian_oop"``, ``"АЕ B2(ПП)"`` → ``"english_b2_pp"``.

    None when the abbreviation is not one we know.
    """
    text = _collapse_ws(label)
    curriculum = _CURRICULUM_RE.search(text)
    cefr = _CEFR_RE.search(text)
    base = re.split(r"[\s(]", text, maxsplit=1)[0]
    slug = next((key for pattern, key in _SUBJECT_PATTERNS if pattern.search(base)), None)
    if slug is None:
        return None
    parts = [slug]
    if cefr:
        parts.append(cefr.group(1).translate(_CYRILLIC_TO_LATIN).lower().replace(".", ""))
    if curriculum:
        parts.append({"ооп": "oop", "пп": "pp"}[curriculum.group(1).lower()])
    return "_".join(parts)


def _subject_label(cell: str) -> Optional[tuple[str, str]]:
    """(marker, subject label) for a "Бр. X" / "Ср.усп. X" header cell, else None."""
    text = _collapse_ws(cell.replace("﻿", ""))
    match = _MARKER_FIRST_RE.match(text) or _MARKER_LAST_RE.match(text)
    if not match:
        return None
    marker = "count" if match.group("marker").lower().startswith("бр") else "grade"
    # A trailing one-letter session tag ("З" for задължителен) is not part of the subject.
    label = re.sub(r"\s+[А-Яа-я]$", "", _collapse_ws(match.group("label")))
    return marker, label


def _resolve_header(rows: list[list[str]]) -> tuple[list[str], int]:
    """One flat header row and the index where data starts."""
    header_idx = next(
        (
            idx
            for idx, row in enumerate(rows[:10])
            if {"област", "училище"} <= {_normalize_header(cell) for cell in row}
        ),
        None,
    )
    if header_idx is None:
        return [], 0

    top = [_collapse_ws(cell.replace("﻿", "")) for cell in rows[header_idx]]
    if any(_subject_label(cell) for cell in top):
        return top, header_idx + 1

    # Multi-row header (2023): subjects span their two columns on the top row and the
    # bare "Бр." / "Ср.усп." markers sit a row or two below.
    for marker_idx in range(header_idx + 1, min(header_idx + 4, len(rows))):
        markers = rows[marker_idx]
        if not any(_MARKER_ONLY_RE.match(_collapse_ws(cell)) for cell in markers):
            continue
        header: list[str] = []
        carried = ""
        for idx, cell in enumerate(top):
            if cell:
                carried = cell
            marker = _collapse_ws(markers[idx]) if idx < len(markers) else ""
            header.append(f"{marker} {carried}" if _MARKER_ONLY_RE.match(marker) else cell)
        return header, marker_idx + 1
    return top, header_idx + 1


def parse_dzi_csv(resource: NvoResource, csv_text: str) -> tuple[list[ParsedExamResult], dict]:
    rows = list(csv.reader(io.StringIO(csv_text.lstrip("﻿"))))
    name = f"ДЗИ {resource.year}"
    if not rows:
        raise DziSliceError(f"{name}: empty CSV payload")

    header, data_start = _resolve_header(rows)
    if not header:
        raise DziSliceError(f"{name}: missing table header row (Област, Училище)")

    normalized = [_normalize_header(cell) for cell in header]

    def column(*names: str) -> Optional[int]:
        return next((idx for idx, cell in enumerate(normalized) if cell in names), None)

    school_col = column("училище")
    code_col = column("код по неиспуо", "код по админ")
    city_col = column("населено място")
    municipality_col = column("община")
    if school_col is None or code_col is None:
        raise DziSliceError(f"{name}: missing column(s) Училище / Код по НЕИСПУО")

    pairs: dict[str, dict[str, int]] = {}
    for idx, cell in enumerate(header):
        parsed = _subject_label(cell)
        if parsed:
            marker, label = parsed
            pairs.setdefault(label, {})[marker] = idx

    subjects: list[tuple[str, int, int]] = []
    unknown_subjects: list[str] = []
    for label, cols in pairs.items():
        if "count" not in cols or "grade" not in cols:
            continue
        key = subject_key(label)
        if key is None:
            unknown_subjects.append(label)
            continue
        subjects.append((key, cols["count"], cols["grade"]))
    if not subjects:
        raise DziSliceError(f"{name}: no subject column pairs (Бр. / Ср.усп.) found")
    if len({key for key, _, _ in subjects}) != len(subjects):
        raise DziSliceError(f"{name}: two subject columns map to the same subject")

    stats = {
        "rows_seen": 0,
        "empty_rows": 0,
        "missing_subject_values": 0,
        "missing_counts": 0,
        "out_of_range_values": 0,
        "unknown_subjects": sorted(unknown_subjects),
    }
    entries: list[ParsedExamResult] = []
    grades_seen = 0

    for raw_row in rows[data_start:]:
        if not any(_collapse_ws(cell) for cell in raw_row):
            continue
        row = raw_row + [""] * (len(header) - len(raw_row))
        stats["rows_seen"] += 1
        school_name = _collapse_ws(row[school_col])
        institutional_id = _clean_institutional_id(row[code_col])
        if not school_name or not institutional_id:
            # Totals and region rows carry no school code.
            stats["empty_rows"] += 1
            continue

        row_values = 0
        for key, count_col, grade_col in subjects:
            grade = _parse_float(row[grade_col])
            if grade is None or grade == 0:
                stats["missing_subject_values"] += 1
                continue
            grades_seen += 1
            count = _parse_float(row[count_col])
            # A grade without a count is withheld: the count gates publishing.
            if count is None or count <= 0:
                stats["missing_counts"] += 1
                continue
            if not GRADE_MIN <= grade <= GRADE_MAX:
                stats["out_of_range_values"] += 1
                continue
            entries.append(
                ParsedExamResult(
                    exam_type=DZI_EXAM_TYPE,
                    year=resource.year,
                    institutional_id=institutional_id,
                    school_name=school_name,
                    city_key=_normalize_city_key(row[city_col]) if city_col is not None else None,
                    subject=key,
                    value=grade,
                    source_url=resource.resource_view_url,
                    sat_count=int(count),
                    municipality_key=(
                        _normalize_city_key(row[municipality_col]) if municipality_col is not None else None
                    ),
                )
            )
            row_values += 1
        if row_values == 0:
            stats["empty_rows"] += 1

    if grades_seen and stats["out_of_range_values"] > grades_seen * MAX_OUT_OF_RANGE_SHARE:
        raise DziSliceError(
            f"{name}: {stats['out_of_range_values']} of {grades_seen} averages are outside "
            f"{GRADE_MIN:g}–{GRADE_MAX:g}; the file may be in points, not grades"
        )
    if not entries:
        raise DziSliceError(f"{name}: parsed payload contained no grades")
    return entries, stats


async def discover_dzi_resources(
    *,
    year: Optional[int] = None,
    history_years: int = DEFAULT_HISTORY_YEARS,
    client: httpx.AsyncClient,
) -> list[NvoResource]:
    if history_years < 1:
        raise ValueError("history_years must be at least 1")
    html = await _fetch_text(client, DZI_DATASET_URL)
    resources = parse_dzi_dataset_resources(html)
    if year is not None:
        return [resource for resource in resources if resource.year == year]
    return resources[:history_years]


async def import_dzi_results(
    db: AsyncSession,
    *,
    country_code: str = "bg",
    city: Optional[str] = "sofia",
    year: Optional[int] = None,
    history_years: int = DEFAULT_HISTORY_YEARS,
    school_ids: Optional[list[int]] = None,
    client: Optional[httpx.AsyncClient] = None,
    resources: Optional[list[NvoResource]] = None,
) -> dict:
    """Import ДЗИ grades for one city's schools. Never deletes stored years."""
    started_at = _utcnow()
    requested_school_ids = sorted(set(school_ids or []))
    effective_city_filter = None if requested_school_ids else city
    summary: dict = {
        "source_url": DZI_DATASET_URL,
        "resource_urls": [],
        "country_code": country_code,
        "city": city,
        "requested_year": year,
        "history_years": history_years,
        "years_imported": [],
        "matched_schools": 0,
        "created_rows": 0,
        "updated_rows": 0,
        "skipped_rows": 0,
        "unmatched_rows": 0,
        "missing_counts": 0,
        "unknown_subjects": [],
        "slice_failures": [],
        "errors": [],
    }

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=60.0, follow_redirects=True)

    try:
        if resources is None:
            resources = await discover_dzi_resources(year=year, history_years=history_years, client=client)
        if not resources:
            raise NvoImportError(
                "No mandatory May–June ДЗИ resources found on " + DZI_DATASET_URL
                + (f" for {year}" if year is not None else "")
            )

        school_index = await _load_school_match_index(
            db,
            country_code=country_code,
            city=effective_city_filter,
            school_ids=requested_school_ids or None,
        )
        matched_school_ids: set[int] = set()
        unknown_subjects: set[str] = set()

        for resource in resources:
            summary["resource_urls"].append(resource.resource_view_url)
            try:
                csv_text = await _fetch_text(client, resource.download_url)
                if csv_text.lstrip()[:15].lower().startswith(("<!doctype html", "<html")):
                    # The portal answers 200 with its HTML shell during download outages.
                    raise DziSliceError(f"ДЗИ {resource.year}: the download returned a web page, not a CSV")
                parsed_rows, parse_stats = parse_dzi_csv(resource, csv_text)
                resource_summary = await _upsert_resource_rows(
                    db,
                    resource=resource,
                    parsed_rows=parsed_rows,
                    school_index=school_index,
                    city_filter=effective_city_filter,
                    allowed_school_ids=set(requested_school_ids) if requested_school_ids else None,
                    touched_school_ids=matched_school_ids,
                    metric=DZI_METRIC,
                    label="ДЗИ",
                )
            except (httpx.HTTPError, NvoSliceError) as exc:
                message = f"ДЗИ {resource.year}: {exc}"
                logger.warning("ДЗИ slice failed: %s", message)
                summary["slice_failures"].append(
                    {"year": resource.year, "resource_url": resource.resource_view_url, "error": str(exc)}
                )
                summary["errors"].append(message)
                continue

            summary["created_rows"] += resource_summary["created_rows"]
            summary["updated_rows"] += resource_summary["updated_rows"]
            summary["skipped_rows"] += (
                resource_summary["skipped_rows"] + parse_stats["empty_rows"] + parse_stats["out_of_range_values"]
            )
            summary["unmatched_rows"] += resource_summary["unmatched_rows"]
            summary["missing_counts"] += parse_stats["missing_counts"]
            unknown_subjects.update(parse_stats["unknown_subjects"])
            if resource_summary["imported_rows"] > 0:
                summary["years_imported"].append(resource.year)

        summary["matched_schools"] = len(matched_school_ids)
        summary["years_imported"] = sorted(set(summary["years_imported"]), reverse=True)
        summary["unknown_subjects"] = sorted(unknown_subjects)
        if unknown_subjects:
            logger.warning("ДЗИ: subjects not imported (unknown abbreviation): %s", ", ".join(sorted(unknown_subjects)))

        # Logged under the NVO type: the scrape_type enum is a Postgres enum and a new
        # value needs a migration; the source URL tells the two imports apart.
        db.add(
            ScrapeLog(
                school_id=None,
                scrape_type=ScrapeType.NVO,
                status=ScrapeStatus.SUCCESS,
                source_url=DZI_DATASET_URL,
                error_message="; ".join(summary["errors"][:10]) or None,
                duration_ms=int((_utcnow() - started_at).total_seconds() * 1000),
                scraped_at=_utcnow(),
            )
        )
        await db.commit()
        return summary
    except Exception as exc:
        await db.rollback()
        async with db.begin():
            db.add(
                ScrapeLog(
                    school_id=None,
                    scrape_type=ScrapeType.NVO,
                    status=ScrapeStatus.FAILED,
                    source_url=DZI_DATASET_URL,
                    error_message=str(exc),
                    duration_ms=int((_utcnow() - started_at).total_seconds() * 1000),
                    scraped_at=_utcnow(),
                )
            )
        raise
    finally:
        if owns_client:
            await client.aclose()
