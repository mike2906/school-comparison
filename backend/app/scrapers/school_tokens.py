"""Shared helpers for extracting meaningful school-name tokens."""

from __future__ import annotations

import re

SCHOOL_NAME_STOPWORDS = {
    # Bulgarian
    "училище",
    "детска",
    "градина",
    "гимназия",
    "прогимназия",
    "частна",
    "държавна",
    "основно",
    "средно",
    "професионална",
    "езикова",
    "чдг",
    "дг",
    "оу",
    "су",
    "пг",
    "соу",
    "нпу",
    "по",
    "и",
    "на",
    "св",
    "свети",
    # English
    "school",
    "kindergarten",
    "private",
    "state",
    "primary",
    "secondary",
    "high",
    "middle",
    "the",
    "of",
    "and",
    "for",
    "with",
    "academy",
}


def extract_school_name_tokens(school_name: str | None, limit: int = 8) -> list[str]:
    """Extract distinctive tokens from school name for ownership checks."""
    if not school_name:
        return []

    tokens = re.findall(r"[^\W_]+", school_name.lower(), flags=re.UNICODE)
    deduped: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        if token in SCHOOL_NAME_STOPWORDS:
            continue
        if len(token) == 1 and not token.isdigit():
            continue
        if token in seen:
            continue
        deduped.append(token)
        seen.add(token)
    return deduped[: max(1, limit)]
