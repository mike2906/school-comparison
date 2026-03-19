"""Heuristics for auditing likely display-name mismatches."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import re
from typing import Any, Iterable, Mapping

from app.scrapers.school_tokens import extract_school_name_tokens
from app.utils.transliteration import transliterate_bulgarian

_AUDIT_IGNORE_EXACT = {
    "about us",
    "contacts",
    "contact",
    "admission",
    "documents",
    "faq",
    "our mission",
    "partners",
    "testimonials",
    "privacy policy",
    "cookie policy",
    "links",
    "gallery",
    "news",
    "events",
    "за нас",
    "контакти",
    "прием",
    "документи",
}
_AUDIT_IGNORE_SUBSTRINGS = {
    "copyright",
    "all rights reserved",
    "cookie",
    "consent",
    "privacy policy",
    "web design",
    "development",
    "403 forbidden",
    "posts navigation",
    "страницата не може да бъде намерена",
    "правилник",
    "политика за",
    "бюджет",
    "документи",
    "author:",
    "автор:",
    "search for:",
    "manage consent",
    "управление на съгласието",
    "управление на съгласие",
    "skip to content",
    "skip to main content",
    "hit enter to search",
    "close search",
    "menu",
    "{title}",
}
_AUDIT_CORE_CATEGORY_BONUS = {
    "about": 4,
    "contact": 3,
    "admission": 3,
}
_AUDIT_CORE_URL_HINTS = (
    "/about",
    "/about-us",
    "/za-nas",
    "/mission",
    "/contacts",
    "/contact",
    "/admission",
    "/faq",
    "/partners",
    "/testimonials",
)
_AUDIT_SCHOOL_TERM_RE = re.compile(
    r"(?i)\b(?:children(?:'s|’s)?\s+house|house|school|kindergarten|academy|college|"
    r"детска\s+къща|детска\s+градина|училище|гимназия|колеж)\b"
)
_AUDIT_VERB_RE = re.compile(
    r"(?i)\b(?:helps|provides|offers|welcomes|works|supports|is|are|"
    r"помага|осигурява|предлага|приема|работи)\b"
)
_AUDIT_PATTERNS = (
    re.compile(
        r"([A-Z][A-Za-z'’\-]+(?:\s+[A-Z][A-Za-z'’\-]+){0,6}\s+"
        r"(?:Children(?:'s|’s)? House|House|School|Kindergarten|Academy|College))"
    ),
    re.compile(
        r"([А-Я][А-Яа-яA-Za-z'’\"„“\-]+(?:\s+[А-ЯA-Z][А-Яа-яA-Za-z'’\"„“\-]+){0,6}\s+"
        r"(?:детска\s+къща|детска\s+градина|училище|гимназия|колеж))",
        flags=re.IGNORECASE,
    ),
)


@dataclass
class DisplayNameAuditFinding:
    school_id: int
    legal_name: str
    stored_display_name: str
    candidate_name: str
    score: int
    repeated_pages: int
    core_pages: int
    evidence_urls: list[str]
    evidence_snippets: list[str]
    website_url: str | None


def _clean_candidate_text(value: str | None) -> str:
    text = str(value or "")
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = text.replace("**", "")
    text = re.sub(r"^[#*\-\s]+", "", text)
    text = re.sub(r"\{[^}]+\}", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" -|\"'“”„")
    return text


def _candidate_identity_key(value: str | None) -> str:
    text = _clean_candidate_text(value)
    text = text.replace("’", "'").replace("“", '"').replace("”", '"')
    text = re.sub(r"\s+", " ", text).strip()
    return text.casefold()


def _normalize_audit_token(token: str | None) -> str:
    raw_token = (token or "").strip()
    if not raw_token:
        return ""
    normalized = transliterate_bulgarian(raw_token) if re.search(r"[А-Яа-я]", raw_token) else raw_token
    normalized = re.sub(r"[^a-z0-9]+", "", normalized.casefold())
    normalized = re.sub(r"(.)\1+", r"\1", normalized)
    return normalized


def _audit_tokens(value: str | None) -> set[str]:
    normalized_source = transliterate_bulgarian(value or "") if re.search(r"[А-Яа-я]", value or "") else (value or "")
    return {
        normalized
        for token in extract_school_name_tokens(normalized_source, limit=12)
        if (normalized := _normalize_audit_token(token))
    }


def _looks_like_noise(candidate: str) -> bool:
    lowered = candidate.casefold()
    if lowered in _AUDIT_IGNORE_EXACT:
        return True
    if any(marker in lowered for marker in _AUDIT_IGNORE_SUBSTRINGS):
        return True
    if len(candidate) < 6:
        return True
    if any(marker in candidate for marker in "[](){}"):
        return True
    if not re.search(r"[A-Za-zА-Яа-я]", candidate):
        return True
    if re.search(r"(?i)\b(?:ltd|llc|inc|eood|ood|ad)\b", candidate):
        return True
    if "http://" in lowered or "https://" in lowered:
        return True
    if len(re.findall(r"[A-Za-zА-Яа-я]", candidate)) < max(4, len(candidate) // 3):
        return True
    if len(candidate.split()) <= 1 and not _AUDIT_SCHOOL_TERM_RE.search(candidate):
        return True
    return False


def _page_bonus(page: Mapping[str, Any]) -> int:
    bonus = _AUDIT_CORE_CATEGORY_BONUS.get(str(page.get("page_category") or "").strip().lower(), 0)
    source_url = str(page.get("source_url") or "")
    if any(hint in source_url.casefold() for hint in _AUDIT_CORE_URL_HINTS):
        bonus += 2
    return bonus


def _extract_candidates_from_page(page: Mapping[str, Any]) -> list[tuple[str, str]]:
    text = str(page.get("raw_markdown") or "")[:7000]
    if not text:
        return []

    candidates: list[tuple[str, str]] = []
    for pattern in _AUDIT_PATTERNS:
        for match in pattern.finditer(text):
            line_start = text.rfind("\n", 0, match.start()) + 1
            line_end = text.find("\n", match.end())
            if line_end == -1:
                line_end = len(text)
            line_context = text[line_start:line_end]
            lowered_context = line_context.casefold()
            if any(marker in lowered_context for marker in ("copyright", "all rights reserved")):
                continue
            if re.search(r"(?i)\b(?:ltd|llc|inc|eood|ood|ad)\b", line_context):
                continue
            candidate = _clean_candidate_text(match.group(1))
            if _looks_like_noise(candidate):
                continue
            candidates.append((candidate, "pattern"))

    for line in text.splitlines()[:40]:
        raw_line = line.strip()
        if (
            not raw_line
            or raw_line.startswith(("* [", "[", "!["))
            or "](" in raw_line
            or raw_line.startswith("Search for:")
        ):
            continue
        line = _clean_candidate_text(line)
        if _looks_like_noise(line):
            continue
        if _AUDIT_SCHOOL_TERM_RE.search(line) and len(line.split()) >= 2:
            candidates.append((line, "line"))
            continue
        if _AUDIT_VERB_RE.search(line):
            prefix = _clean_candidate_text(line.split(" helps", 1)[0].split(" provides", 1)[0].split(" offers", 1)[0])
            if (
                prefix
                and not _looks_like_noise(prefix)
                and (_AUDIT_SCHOOL_TERM_RE.search(prefix) or len(prefix.split()) >= 3)
            ):
                candidates.append((prefix, "intro"))

    deduped: list[tuple[str, str]] = []
    seen: set[str] = set()
    for candidate, source_kind in candidates:
        key = candidate.casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped.append((candidate, source_kind))
    return deduped


def audit_school_display_name(
    school: Mapping[str, Any],
    pages: Iterable[Mapping[str, Any]],
) -> DisplayNameAuditFinding | None:
    attrs = school.get("attributes") if isinstance(school.get("attributes"), Mapping) else {}
    display_name = attrs.get("display_name_i18n") if isinstance(attrs.get("display_name_i18n"), Mapping) else {}
    stored_display = _clean_candidate_text(
        str(display_name.get("en") or display_name.get("bg") or "")
    )
    legal_name = _clean_candidate_text(
        str((school.get("name_i18n") or {}).get("bg") or (school.get("name_i18n") or {}).get("en") or "")
    )
    stored_tokens = _audit_tokens(stored_display)
    legal_tokens = _audit_tokens(legal_name)

    candidate_scores: dict[str, int] = defaultdict(int)
    candidate_labels: dict[str, str] = {}
    candidate_urls: dict[str, list[str]] = defaultdict(list)
    candidate_snippets: dict[str, list[str]] = defaultdict(list)
    candidate_page_count: dict[str, set[str]] = defaultdict(set)
    candidate_core_page_count: dict[str, set[str]] = defaultdict(set)

    for page in pages:
        page_candidates = _extract_candidates_from_page(page)
        if not page_candidates:
            continue
        page_url = str(page.get("source_url") or "")
        bonus = _page_bonus(page)
        is_core_page = bonus > 0
        for candidate, source_kind in page_candidates:
            candidate_key = _candidate_identity_key(candidate)
            candidate_tokens = _audit_tokens(candidate)
            overlap = len(candidate_tokens & (stored_tokens or legal_tokens))
            if stored_display:
                if len(candidate) < len(stored_display) + 6:
                    continue
                if overlap == 0 and stored_display.casefold() not in candidate.casefold():
                    continue
            else:
                if overlap == 0 and len(candidate_tokens & legal_tokens) == 0:
                    continue

            score = bonus
            score += min(overlap * 2, 6)
            if source_kind == "intro":
                score += 3
            elif source_kind == "pattern":
                score += 2
            elif source_kind == "line":
                score += 1
            if candidate.casefold().endswith(("house", "school", "kindergarten", "academy", "college")):
                score += 1

            candidate_scores[candidate_key] += score
            existing_label = candidate_labels.get(candidate_key)
            if existing_label is None or len(candidate) > len(existing_label):
                candidate_labels[candidate_key] = candidate
            if page_url and page_url not in candidate_urls[candidate_key]:
                candidate_urls[candidate_key].append(page_url)
            if candidate not in candidate_snippets[candidate_key]:
                candidate_snippets[candidate_key].append(candidate)
            if page_url:
                candidate_page_count[candidate_key].add(page_url)
                if is_core_page:
                    candidate_core_page_count[candidate_key].add(page_url)

    best_candidate_key: str | None = None
    best_score = 0
    for candidate_key, score in candidate_scores.items():
        repeated_pages = len(candidate_page_count[candidate_key])
        if repeated_pages > 1:
            score += (repeated_pages - 1) * 3
        if score > best_score:
            best_score = score
            best_candidate_key = candidate_key

    if not best_candidate_key:
        return None

    repeated_pages = len(candidate_page_count[best_candidate_key])
    core_pages = len(candidate_core_page_count[best_candidate_key])
    min_score = 8 if stored_display else 7
    if best_score < min_score:
        return None

    return DisplayNameAuditFinding(
        school_id=int(school.get("id")),
        legal_name=legal_name,
        stored_display_name=stored_display,
        candidate_name=candidate_labels[best_candidate_key],
        score=best_score,
        repeated_pages=repeated_pages,
        core_pages=core_pages,
        evidence_urls=candidate_urls[best_candidate_key][:5],
        evidence_snippets=candidate_snippets[best_candidate_key][:3],
        website_url=str(school.get("website_url") or "") or None,
    )


__all__ = [
    "DisplayNameAuditFinding",
    "audit_school_display_name",
]
