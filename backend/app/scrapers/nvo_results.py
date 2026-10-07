"""Bulgaria NVO results importer."""

from __future__ import annotations

import csv
import io
import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Iterable, Optional
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.exam_results import ExamResult
from app.models.school import School
from app.models.scrape_log import ScrapeLog, ScrapeStatus, ScrapeType
from app.utils.transliteration import transliterate_bulgarian

logger = logging.getLogger(__name__)

IO_MON_NVO_INDEX_URL = "https://io.mon.bg/node/745"
SUPPORTED_EXAM_TYPES = ("nvo_4", "nvo_7", "nvo_10")
REQUIRED_SUBJECTS = ("bulgarian", "math")
DEFAULT_HISTORY_YEARS = 5

_INDEX_LABEL_TO_EXAM_TYPE = {
    "нво 4 клас": "nvo_4",
    "нво 7 клас": "nvo_7",
    "нво 10 клас": "nvo_10",
}
_RESOURCE_YEAR_RE = re.compile(r"(20\d{2})\s*/\s*(20\d{2})")
_RESOURCE_ID_RE = re.compile(r"/data/resourceView/([0-9a-f-]{36})")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_SOURCE_CITY_PREFIX_RE = re.compile(r"^(?:гр|с|с\.)\s*\.?\s*", flags=re.IGNORECASE)
_WHITESPACE_RE = re.compile(r"\s+")


class NvoImportError(RuntimeError):
    """Raised when the NVO import cannot continue."""


class NvoSliceError(NvoImportError):
    """Raised when a single NVO dataset slice is invalid."""


@dataclass(frozen=True)
class NvoResource:
    exam_type: str
    year: int
    title: str
    dataset_url: str
    resource_view_url: str
    download_url: str


@dataclass(frozen=True)
class ParsedExamResult:
    exam_type: str
    year: int
    institutional_id: Optional[str]
    school_name: str
    city_key: Optional[str]
    subject: str
    value: float
    source_url: str


@dataclass(frozen=True)
class SchoolMatchIndex:
    by_institutional_id: dict[str, int]
    by_name_city: dict[tuple[str, str], list[int]]


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def normalize_exam_types(exam_types: Optional[Iterable[str]]) -> list[str]:
    if exam_types is None:
        return list(SUPPORTED_EXAM_TYPES)

    normalized: list[str] = []
    invalid: list[str] = []
    for exam_type in exam_types:
        value = (exam_type or "").strip().lower()
        if value in SUPPORTED_EXAM_TYPES:
            normalized.append(value)
        else:
            invalid.append(exam_type or "")

    if invalid:
        raise ValueError(f"Unsupported exam type(s): {', '.join(invalid)}")

    deduped: list[str] = []
    for exam_type in normalized:
        if exam_type not in deduped:
            deduped.append(exam_type)
    return deduped


def parse_index_dataset_links(html: str) -> dict[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    links: dict[str, str] = {}

    for anchor in soup.select("a[href]"):
        text = _collapse_ws(anchor.get_text(" ", strip=True)).lower()
        exam_type = _INDEX_LABEL_TO_EXAM_TYPE.get(text)
        if not exam_type:
            continue
        links[exam_type] = urljoin(IO_MON_NVO_INDEX_URL, anchor["href"])

    missing = [exam_type for exam_type in SUPPORTED_EXAM_TYPES if exam_type not in links]
    if missing:
        raise NvoImportError(f"Missing dataset links for exam type(s): {', '.join(missing)}")
    return links


def parse_dataset_resources(exam_type: str, dataset_url: str, html: str) -> list[NvoResource]:
    soup = BeautifulSoup(html, "html.parser")
    resources: list[NvoResource] = []

    for anchor in soup.select('a[href*="/data/resourceView/"]'):
        href = urljoin(dataset_url, anchor["href"])
        title = _collapse_ws(anchor.get_text(" ", strip=True))
        year = _parse_resource_year(title)
        if year is None:
            continue

        resource_id_match = _RESOURCE_ID_RE.search(href)
        if not resource_id_match:
            continue

        resource_id = resource_id_match.group(1)
        resources.append(
            NvoResource(
                exam_type=exam_type,
                year=year,
                title=title,
                dataset_url=dataset_url,
                resource_view_url=href,
                download_url=f"https://data.egov.bg/resource/download/{resource_id}/csv",
            )
        )

    deduped: dict[int, NvoResource] = {}
    for resource in sorted(resources, key=lambda item: item.year, reverse=True):
        deduped.setdefault(resource.year, resource)
    return list(deduped.values())


def parse_nvo_csv(resource: NvoResource, csv_text: str) -> tuple[list[ParsedExamResult], dict[str, int]]:
    normalized_text = csv_text.lstrip("\ufeff")
    rows = list(csv.reader(io.StringIO(normalized_text)))
    if not rows:
        raise NvoSliceError(f"{resource.exam_type} {resource.year}: empty CSV payload")

    fieldnames, data_rows = _extract_table_rows(rows)
    if not fieldnames:
        raise NvoSliceError(f"{resource.exam_type} {resource.year}: missing table header row")

    header_map = {_normalize_header(field): field for field in fieldnames if field is not None}
    school_col = _find_first_header(header_map, ("училище",))
    institutional_id_col = _find_first_header(header_map, ("код по неиспуо", "код по админ"))
    city_col = _find_first_header(header_map, ("населено място",))
    bulgarian_col = _find_subject_column(header_map, "бел")
    math_col = _find_subject_column(header_map, "мат")
    # Optional: older files may lack the per-subject "sat the exam" counts.
    sat_cols = {
        "bulgarian": _find_subject_column(header_map, "бел", "явили се"),
        "math": _find_subject_column(header_map, "мат", "явили се"),
    }

    missing_headers = [
        label
        for label, value in {
            "училище": school_col,
            "код по неиспуо": institutional_id_col,
            "населено място": city_col,
            "БЕЛ Ср. успех в точки": bulgarian_col,
            "МАТ Ср. успех в точки": math_col,
        }.items()
        if value is None
    ]
    if missing_headers:
        raise NvoSliceError(
            f"{resource.exam_type} {resource.year}: missing required column(s): {', '.join(missing_headers)}"
        )

    entries: list[ParsedExamResult] = []
    stats = {
        "rows_seen": 0,
        "empty_rows": 0,
        "missing_subject_values": 0,
    }

    for raw_row in data_rows:
        row = dict(zip(fieldnames, raw_row))
        stats["rows_seen"] += 1
        school_name = _collapse_ws(row.get(school_col, ""))
        if not school_name:
            stats["empty_rows"] += 1
            continue

        institutional_id = _clean_institutional_id(row.get(institutional_id_col))
        city_key = _normalize_city_key(row.get(city_col))
        row_values = 0

        for subject, column_name in (("bulgarian", bulgarian_col), ("math", math_col)):
            raw_value = row.get(column_name, "")
            parsed_value = _parse_float(raw_value)
            sat_col = sat_cols[subject]
            sat_count = _parse_float(row.get(sat_col, "")) if sat_col else None
            # The files write 0 as the average when nobody sat the subject. That is a
            # missing value, not a score, so it is never stored.
            if parsed_value is None or parsed_value == 0 or sat_count == 0:
                stats["missing_subject_values"] += 1
                continue

            entries.append(
                ParsedExamResult(
                    exam_type=resource.exam_type,
                    year=resource.year,
                    institutional_id=institutional_id,
                    school_name=school_name,
                    city_key=city_key,
                    subject=subject,
                    value=parsed_value,
                    source_url=resource.resource_view_url,
                )
            )
            row_values += 1

        if row_values == 0:
            stats["empty_rows"] += 1

    if not entries:
        raise NvoSliceError(f"{resource.exam_type} {resource.year}: parsed payload contained no scores")

    return entries, stats


def _extract_table_rows(rows: list[list[str]]) -> tuple[list[str], list[list[str]]]:
    header_idx = _find_header_row_index(rows)
    if header_idx is None:
        return [], []

    top_row = _pad_row(rows[header_idx], len(rows[header_idx]))
    next_row = rows[header_idx + 1] if header_idx + 1 < len(rows) else []

    if _looks_like_secondary_header(next_row):
        secondary_row = _expand_secondary_header(_pad_row(next_row, len(top_row)), top_row)
        fieldnames = [_combine_header_cells(top, bottom) for top, bottom in zip(top_row, secondary_row)]
        data_start = header_idx + 2
    else:
        fieldnames = [_collapse_ws(cell.replace("\ufeff", "")) for cell in top_row]
        data_start = header_idx + 1

    data_rows: list[list[str]] = []
    for row in rows[data_start:]:
        padded = _pad_row(row, len(fieldnames))
        if any(_collapse_ws(cell) for cell in padded):
            data_rows.append(padded)
    return fieldnames, data_rows


def _find_header_row_index(rows: list[list[str]]) -> Optional[int]:
    for idx, row in enumerate(rows):
        normalized_cells = [_normalize_header(cell) for cell in row]
        if "област" in normalized_cells and "училище" in normalized_cells:
            return idx
    return None


def _looks_like_secondary_header(row: list[str]) -> bool:
    if not row:
        return False
    normalized = [_normalize_header(cell) for cell in row]
    non_empty = [cell for cell in normalized if cell]
    joined = " ".join(cell for cell in normalized if cell)
    if len(non_empty) > 6:
        return False
    return (
        "ср. успех" in joined
        or "явили се" in joined
        or any(cell in {"бел", "мат"} for cell in non_empty)
    )


def _expand_secondary_header(row: list[str], top_row: list[str]) -> list[str]:
    expanded: list[str] = []
    carry = ""
    for _idx, (top_cell, bottom_cell) in enumerate(zip(top_row, row)):
        normalized_top = _normalize_header(top_cell)
        if normalized_top in {"бел", "мат"}:
            carry = normalized_top
        elif normalized_top:
            carry = ""

        value = _collapse_ws(bottom_cell.replace("\ufeff", ""))
        if not value:
            expanded.append(value)
            continue

        if not _collapse_ws(top_cell) and carry:
            expanded.append(f"{carry} {value}")
        else:
            expanded.append(value)
    return expanded


def _combine_header_cells(top: str, bottom: str) -> str:
    top_clean = _collapse_ws(top.replace("\ufeff", ""))
    bottom_clean = _collapse_ws(bottom.replace("\ufeff", ""))
    if top_clean and bottom_clean:
        return f"{top_clean} {bottom_clean}"
    return top_clean or bottom_clean


def _pad_row(row: list[str], width: int) -> list[str]:
    if len(row) >= width:
        return row[:width]
    return row + [""] * (width - len(row))


async def discover_nvo_resources(
    exam_types: Optional[Iterable[str]] = None,
    *,
    year: Optional[int] = None,
    history_years: int = DEFAULT_HISTORY_YEARS,
    client: Optional[httpx.AsyncClient] = None,
) -> list[NvoResource]:
    selected_exam_types = normalize_exam_types(exam_types)
    if history_years < 1:
        raise ValueError("history_years must be at least 1")

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=60.0, follow_redirects=True)

    try:
        index_html = await _fetch_text(client, IO_MON_NVO_INDEX_URL)
        dataset_links = parse_index_dataset_links(index_html)
        all_resources: list[NvoResource] = []

        for exam_type in selected_exam_types:
            dataset_url = dataset_links[exam_type]
            dataset_html = await _fetch_text(client, dataset_url)
            resources = parse_dataset_resources(exam_type, dataset_url, dataset_html)
            if year is not None:
                resources = [resource for resource in resources if resource.year == year]
            else:
                resources = resources[:history_years]

            all_resources.extend(resources)

        return sorted(all_resources, key=lambda item: (item.year, item.exam_type), reverse=True)
    finally:
        if owns_client:
            await client.aclose()


async def import_nvo_results(
    db: AsyncSession,
    *,
    country_code: str = "bg",
    city: Optional[str] = "sofia",
    year: Optional[int] = None,
    history_years: int = DEFAULT_HISTORY_YEARS,
    exam_types: Optional[Iterable[str]] = None,
    school_ids: Optional[Iterable[int]] = None,
    client: Optional[httpx.AsyncClient] = None,
) -> dict:
    started_at = _utcnow()
    selected_exam_types = normalize_exam_types(exam_types)
    requested_school_ids = sorted({school_id for school_id in (school_ids or [])})
    effective_city_filter = None if requested_school_ids else city

    summary = {
        "source_url": IO_MON_NVO_INDEX_URL,
        "resource_urls": [],
        "country_code": country_code,
        "city": city,
        "requested_year": year,
        "history_years": history_years,
        "exam_types": selected_exam_types,
        "years_imported": [],
        "matched_schools": 0,
        "created_rows": 0,
        "updated_rows": 0,
        "skipped_rows": 0,
        "unmatched_rows": 0,
        "slice_failures": [],
        "errors": [],
    }

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=60.0, follow_redirects=True)

    try:
        resources = await discover_nvo_resources(
            selected_exam_types,
            year=year,
            history_years=history_years,
            client=client,
        )
        found_exam_types = {resource.exam_type for resource in resources}
        for exam_type in selected_exam_types:
            if exam_type in found_exam_types:
                continue
            message = (
                f"{exam_type}"
                + (f" {year}" if year is not None else "")
                + ": no official resources found"
            )
            summary["slice_failures"].append(
                {
                    "exam_type": exam_type,
                    "year": year,
                    "resource_url": None,
                    "error": "no official resources found",
                }
            )
            summary["errors"].append(message)

        if not resources:
            raise NvoImportError("No official NVO resources found for the requested scope")

        school_index = await _load_school_match_index(
            db,
            country_code=country_code,
            city=effective_city_filter,
            school_ids=requested_school_ids or None,
        )
        matched_school_ids: set[int] = set()

        for resource in resources:
            summary["resource_urls"].append(resource.resource_view_url)
            try:
                csv_text = await _download_resource_csv(resource, client=client)
                parsed_rows, parse_stats = parse_nvo_csv(resource, csv_text)
                resource_summary = await _upsert_resource_rows(
                    db,
                    resource=resource,
                    parsed_rows=parsed_rows,
                    school_index=school_index,
                    city_filter=effective_city_filter,
                    allowed_school_ids=set(requested_school_ids) if requested_school_ids else None,
                    touched_school_ids=matched_school_ids,
                )
            except (httpx.HTTPError, NvoSliceError) as exc:
                message = f"{resource.exam_type} {resource.year}: {exc}"
                logger.warning("NVO slice failed: %s", message)
                summary["slice_failures"].append(
                    {
                        "exam_type": resource.exam_type,
                        "year": resource.year,
                        "resource_url": resource.resource_view_url,
                        "error": str(exc),
                    }
                )
                summary["errors"].append(message)
                continue

            summary["created_rows"] += resource_summary["created_rows"]
            summary["updated_rows"] += resource_summary["updated_rows"]
            summary["skipped_rows"] += resource_summary["skipped_rows"] + parse_stats["empty_rows"]
            summary["unmatched_rows"] += resource_summary["unmatched_rows"]
            if resource_summary["imported_rows"] > 0:
                summary["years_imported"].append(resource.year)
            if parse_stats["missing_subject_values"]:
                summary["skipped_rows"] += parse_stats["missing_subject_values"]

        summary["matched_schools"] = len(matched_school_ids)
        summary["years_imported"] = sorted(set(summary["years_imported"]), reverse=True)

        await _record_nvo_scrape_log(
            db,
            status=ScrapeStatus.SUCCESS,
            started_at=started_at,
            summary=summary,
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
                    source_url=IO_MON_NVO_INDEX_URL,
                    error_message=str(exc),
                    duration_ms=int((_utcnow() - started_at).total_seconds() * 1000),
                    scraped_at=_utcnow(),
                )
            )
        raise
    finally:
        if owns_client:
            await client.aclose()


async def _record_nvo_scrape_log(
    db: AsyncSession,
    *,
    status: ScrapeStatus,
    started_at: datetime,
    summary: dict,
) -> None:
    error_message = None
    if summary["errors"]:
        error_message = "; ".join(summary["errors"][:10])

    db.add(
        ScrapeLog(
            school_id=None,
            scrape_type=ScrapeType.NVO,
            status=status,
            source_url=IO_MON_NVO_INDEX_URL,
            error_message=error_message,
            duration_ms=int((_utcnow() - started_at).total_seconds() * 1000),
            scraped_at=_utcnow(),
        )
    )


async def _load_school_match_index(
    db: AsyncSession,
    *,
    country_code: str,
    city: Optional[str],
    school_ids: Optional[list[int]],
) -> SchoolMatchIndex:
    query = select(School.id, School.institutional_id, School.name_i18n, School.city).where(
        School.country_code == country_code
    )

    if city:
        query = query.where(School.city == city)
    if school_ids:
        query = query.where(School.id.in_(school_ids))

    result = await db.execute(query)
    by_institutional_id: dict[str, int] = {}
    by_name_city: dict[tuple[str, str], list[int]] = {}

    for school_id, institutional_id, name_i18n, school_city in result.all():
        normalized_id = _clean_institutional_id(institutional_id)
        if normalized_id:
            by_institutional_id[normalized_id] = school_id

        bg_name = ""
        if isinstance(name_i18n, dict):
            bg_name = name_i18n.get("bg") or name_i18n.get("en") or ""
        name_key = _normalize_name_key(bg_name)
        city_key = _normalize_city_key(school_city)
        if name_key and city_key:
            by_name_city.setdefault((name_key, city_key), []).append(school_id)

    return SchoolMatchIndex(
        by_institutional_id=by_institutional_id,
        by_name_city=by_name_city,
    )


async def _upsert_resource_rows(
    db: AsyncSession,
    *,
    resource: NvoResource,
    parsed_rows: list[ParsedExamResult],
    school_index: SchoolMatchIndex,
    city_filter: Optional[str],
    allowed_school_ids: Optional[set[int]],
    touched_school_ids: set[int],
) -> dict[str, int]:
    matched_entries: dict[tuple[int, int, str, str, str], ParsedExamResult] = {}
    skipped_rows = 0
    unmatched_rows = 0
    normalized_city_filter = _normalize_city_key(city_filter) if city_filter else None

    for entry in parsed_rows:
        if normalized_city_filter and entry.city_key and entry.city_key != normalized_city_filter:
            skipped_rows += 1
            continue

        school_id = _match_school(entry, school_index)
        if school_id is None:
            unmatched_rows += 1
            continue

        if allowed_school_ids is not None and school_id not in allowed_school_ids:
            skipped_rows += 1
            continue

        touched_school_ids.add(school_id)
        key = (school_id, entry.year, entry.exam_type, entry.subject, "average_score")
        matched_entries[key] = entry

    if not matched_entries:
        return {
            "created_rows": 0,
            "updated_rows": 0,
            "skipped_rows": skipped_rows,
            "unmatched_rows": unmatched_rows,
            "imported_rows": 0,
        }

    school_ids = sorted({school_id for school_id, _, _, _, _ in matched_entries})
    existing_result = await db.execute(
        select(ExamResult).where(
            ExamResult.school_id.in_(school_ids),
            ExamResult.year == resource.year,
            ExamResult.exam_type == resource.exam_type,
            ExamResult.metric == "average_score",
        )
    )
    existing_map = {
        (row.school_id, row.year, row.exam_type, row.subject, row.metric): row
        for row in existing_result.scalars().all()
    }

    created_rows = 0
    updated_rows = 0
    timestamp = _utcnow()

    for key, entry in matched_entries.items():
        existing = existing_map.get(key)
        if existing is None:
            db.add(
                ExamResult(
                    school_id=key[0],
                    year=entry.year,
                    exam_type=entry.exam_type,
                    subject=entry.subject,
                    metric="average_score",
                    value=entry.value,
                    source_url=entry.source_url,
                    scraped_at=timestamp,
                )
            )
            created_rows += 1
            continue

        existing.value = entry.value
        existing.source_url = entry.source_url
        existing.scraped_at = timestamp
        updated_rows += 1

    return {
        "created_rows": created_rows,
        "updated_rows": updated_rows,
        "skipped_rows": skipped_rows,
        "unmatched_rows": unmatched_rows,
        "imported_rows": len(matched_entries),
    }


def _match_school(entry: ParsedExamResult, school_index: SchoolMatchIndex) -> Optional[int]:
    if entry.institutional_id:
        school_id = school_index.by_institutional_id.get(entry.institutional_id)
        if school_id is not None:
            return school_id

    name_key = _normalize_name_key(entry.school_name)
    if not name_key or not entry.city_key:
        return None

    matches = school_index.by_name_city.get((name_key, entry.city_key), [])
    if len(matches) == 1:
        return matches[0]
    return None


async def _download_resource_csv(
    resource: NvoResource,
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> str:
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=60.0, follow_redirects=True)

    try:
        return await _fetch_text(client, resource.download_url)
    finally:
        if owns_client:
            await client.aclose()


async def _fetch_text(client: httpx.AsyncClient, url: str) -> str:
    response = await client.get(url)
    response.raise_for_status()
    return response.content.decode("utf-8-sig")


def _parse_resource_year(text: str) -> Optional[int]:
    match = _RESOURCE_YEAR_RE.search(text)
    if match:
        return int(match.group(2))

    standalone_years = re.findall(r"20\d{2}", text)
    if standalone_years:
        return int(standalone_years[-1])
    return None


def _normalize_header(value: str) -> str:
    cleaned = value.replace('"', "").replace("\ufeff", "")
    return _collapse_ws(cleaned).lower()


def _find_first_header(header_map: dict[str, str], candidates: tuple[str, ...]) -> Optional[str]:
    for candidate in candidates:
        value = header_map.get(candidate)
        if value is not None:
            return value
    return None


def _find_subject_column(
    header_map: dict[str, str], subject_prefix: str, label: str = "ср. успех в точки"
) -> Optional[str]:
    for normalized, original in header_map.items():
        if subject_prefix in normalized and label in normalized:
            return original
    return None


def _normalize_name_key(value: Optional[str]) -> str:
    if not value:
        return ""
    transliterated = transliterate_bulgarian(value) if re.search(r"[А-Яа-я]", value) else value
    return _normalize_match_key(transliterated)


def _normalize_city_key(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None

    text = _collapse_ws(str(value))
    if not text:
        return None

    cleaned = _SOURCE_CITY_PREFIX_RE.sub("", text).strip()
    transliterated = transliterate_bulgarian(cleaned) if re.search(r"[А-Яа-я]", cleaned) else cleaned
    key = _normalize_match_key(transliterated)
    return key or None


def _normalize_match_key(value: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    cleaned = _NON_ALNUM_RE.sub(" ", ascii_text.lower())
    return _collapse_ws(cleaned)


def _parse_float(value: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    text = str(value).strip().replace(",", ".")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _collapse_ws(value: str) -> str:
    return _WHITESPACE_RE.sub(" ", value or "").strip()


def _clean_institutional_id(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    cleaned = re.sub(r"\D+", "", str(value))
    return cleaned or None
