"""Contact details from page text: phones, emails, addresses and coordinates."""

from __future__ import annotations

import re
from typing import Any

from app.scrapers.extraction.section_lines import (
    _is_probable_section_noise_line,
)
from app.scrapers.extraction.text_values import (
    _sanitize_label,
)
from app.scrapers.extraction_rules import base as rules


def _extract_contact_info_deterministic(text: str) -> dict[str, Any] | None:
    """Extract phone numbers, email addresses, and postal addresses from page text.

    This is deterministic (no LLM) and processes all page content. Returns None
    when nothing useful is found.
    """
    if not text:
        return None

    contact: dict[str, Any] = {}

    # Extract phone numbers
    phones: list[str] = []
    seen_phones: set[str] = set()
    for match in rules._PHONE_PATTERN.finditer(text):
        raw = match.group(0).strip()
        # Normalize: collapse internal whitespace and separators
        normalized = re.sub(r"[\s./\-]+", "", raw)
        if normalized in seen_phones:
            continue
        if len(normalized) < 9:  # Too short to be a real phone
            continue
        seen_phones.add(normalized)
        phones.append(raw.strip())
        if len(phones) >= 3:  # Max 3 phone numbers per school
            break

    if phones:
        contact["phones"] = phones

    # Extract email addresses
    emails: list[str] = []
    seen_emails: set[str] = set()
    for match in rules._EMAIL_PATTERN.finditer(text):
        raw = match.group(0).strip()
        # Normalize [at] / [ a t ] → @, collapse spaces around @
        normalized = re.sub(r"\s*\[\s*a\s*t\s*\]\s*", "@", raw, flags=re.IGNORECASE)
        normalized = re.sub(r"\s+", "", normalized).lower()
        # Filter obvious placeholder/noise emails
        if any(noise in normalized for noise in rules._CONTACT_NOISE_TOKENS):
            continue
        if normalized in seen_emails:
            continue
        seen_emails.add(normalized)
        emails.append(normalized)
        if len(emails) >= 3:  # Max 3 email addresses
            break

    if emails:
        contact["emails"] = emails

    addresses = _extract_contact_address_candidates(text)
    if addresses:
        contact["address"] = addresses[0]
        if len(addresses) > 1:
            contact["addresses"] = addresses

    coordinates = _extract_contact_coordinates(text)
    if coordinates:
        contact["coordinates"] = coordinates

    return contact if contact else None


_CONTACT_ADDRESS_LABEL_RE = re.compile(r"(?:^|[\s>*_-])(?:address|адрес)\b[:\s-]*", flags=re.IGNORECASE)


_CONTACT_ADDRESS_STREET_MARKERS = (
    "ул.",
    "бул.",
    "ж.к.",
    "жк.",
    "кв.",
    "пл.",
    "гр.",
    "street",
    "st.",
    "boulevard",
    "blvd",
    "road",
    "rd.",
    "avenue",
    "ave.",
)


_CONTACT_ADDRESS_NOISE_MARKERS = (
    "all rights reserved",
    "cookie",
    "бисквит",
    "privacy",
    "политика",
    "общи условия",
    "designed by",
    "телефон",
    "email",
    "e-mail",
    "турнир",
    "шампион",
    "училища",
)


def _extract_contact_address_candidates(text: str) -> list[str]:
    if not text:
        return []

    lines = [line.strip() for line in text.splitlines()]
    candidates: list[tuple[int, str]] = []

    def add_candidate(raw_value: str, score_bonus: int = 0) -> None:
        normalized = _normalize_contact_address_candidate(raw_value)
        if not _looks_like_contact_address(normalized):
            return
        score = _contact_address_score(normalized) + score_bonus
        candidates.append((score, normalized))

    for idx, line in enumerate(lines):
        if not line:
            continue
        normalized_line = _normalize_contact_address_candidate(line)
        if _looks_like_contact_address(normalized_line):
            add_candidate(normalized_line)

        if not _CONTACT_ADDRESS_LABEL_RE.search(line):
            continue

        inline = _CONTACT_ADDRESS_LABEL_RE.sub("", line).strip(" -:|")
        if inline:
            add_candidate(inline, score_bonus=3)

        trailing_lines: list[str] = []
        for offset in range(1, 4):
            next_idx = idx + offset
            if next_idx >= len(lines):
                break
            next_line = lines[next_idx].strip()
            if not next_line or _is_probable_section_noise_line(next_line):
                continue
            if re.match(r"^#{1,6}\s", next_line):
                break
            if _CONTACT_ADDRESS_LABEL_RE.search(next_line):
                break
            trailing_lines.append(next_line)
            if _looks_like_contact_address(next_line):
                break
        if trailing_lines:
            add_candidate(", ".join(trailing_lines), score_bonus=4)

    unique: list[str] = []
    seen: set[str] = set()
    for _score, candidate in sorted(candidates, key=lambda item: (-item[0], len(item[1]))):
        key = re.sub(r"\s+", " ", candidate.casefold()).strip(" ,")
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
        if len(unique) >= 3:
            break
    return unique


def _normalize_contact_address_candidate(raw_value: str) -> str:
    candidate = _sanitize_label(str(raw_value or "").strip(), max_len=240)
    if not candidate:
        return ""
    candidate = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", candidate)
    candidate = candidate.replace("location_on", " ")
    candidate = re.sub(r"[*_#`]+", " ", candidate)
    candidate = _CONTACT_ADDRESS_LABEL_RE.sub("", candidate).strip(" -:|")
    lowered = candidate.casefold()
    marker_positions = [lowered.find(marker) for marker in _CONTACT_ADDRESS_STREET_MARKERS if marker in lowered]
    if marker_positions:
        first_marker = min(pos for pos in marker_positions if pos >= 0)
        if first_marker > 0:
            candidate = candidate[first_marker:].strip(" ,")
    candidate = re.sub(r"\s+", " ", candidate).strip(" ,")
    return candidate


def _contact_address_score(value: str) -> int:
    lowered = (value or "").casefold()
    if not lowered:
        return 0

    score = 0
    if any(marker in lowered for marker in _CONTACT_ADDRESS_STREET_MARKERS):
        score += 4
    if re.search(r"[№#]\s*\d+", value) or re.search(r"\b\d+[A-Za-zА-Яа-я]?\b", value):
        score += 2
    if any(marker in lowered for marker in ("ет.", "ап.", "офис", "floor", "suite")):
        score += 1
    if _CONTACT_ADDRESS_LABEL_RE.search(value):
        score += 2
    return score


def _looks_like_contact_address(value: str | None) -> bool:
    candidate = (value or "").strip()
    lowered = candidate.casefold()
    if not candidate or len(candidate) < 8:
        return False
    if any(marker in lowered for marker in _CONTACT_ADDRESS_NOISE_MARKERS):
        return False
    if _is_probable_section_noise_line(candidate):
        return False
    if len(candidate.split()) < 2:
        return False
    if len(candidate.split()) > 16:
        return False
    if not any(marker in lowered for marker in _CONTACT_ADDRESS_STREET_MARKERS) and not re.search(r"[№#]\s*\d+", candidate):
        return False
    return _contact_address_score(candidate) >= 4


def _extract_contact_coordinates(text: str) -> dict[str, float] | None:
    if not text:
        return None
    match = re.search(
        r"(?:coordinates|координати)\s*:\s*(-?\d{1,3}\.\d+)\s*,\s*(-?\d{1,3}\.\d+)",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    lat = float(match.group(1))
    lng = float(match.group(2))
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return None
    return {"lat": lat, "lng": lng}
