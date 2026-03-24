"""Shared helpers for extracting meaningful school-name tokens."""

from __future__ import annotations

import re

SCHOOL_NAME_STOPWORDS = {
    # Bulgarian
    "училище",
    "uchilishte",
    "детска",
    "detska",
    "градина",
    "gradina",
    "гимназия",
    "gimnaziya",
    "gimnazia",
    "прогимназия",
    "progimnaziya",
    "частна",
    "chastna",
    "държавна",
    "darzhavna",
    "основно",
    "osnovno",
    "средно",
    "sredno",
    "професионална",
    "profesionalna",
    "езикова",
    "ezikova",
    "чдг",
    "дг",
    "оу",
    "су",
    "пг",
    "соу",
    "нпу",
    "по",
    "eood",
    "ood",
    "ead",
    "ad",
    "eoood",
    "sdruzhenie",
    "сдружение",
    "еоод",
    "оод",
    "еад",
    "ад",
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
    "kinder",
    "schools",
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
