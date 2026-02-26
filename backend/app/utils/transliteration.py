"""Deterministic transliteration helpers for Bulgarian text."""
from __future__ import annotations

import re

import cyrtranslit

ADDRESS_ABBREVIATIONS = {
    "ул.": "ul.",
    "бул.": "bul.",
    "гр.": "gr.",
    "ж.к.": "zh.k.",
    "жк.": "zh.k.",
    "кв.": "kv.",
    "с.": "s.",
    "в.з.": "v.z.",
    "пл.": "pl.",
    "бл.": "bl.",
    "вх.": "vh.",
}

PREFERRED_EN_TOKENS = {
    # Bulgarian place names with established English exonyms.
    "sofiya": "sofia",
    "balgariya": "bulgaria",
}


def _match_case(template: str, replacement: str) -> str:
    """Apply replacement with case style inferred from template."""
    if template.isupper():
        return replacement.upper()
    if template.islower():
        return replacement.lower()
    if template[:1].isupper() and template[1:].islower():
        return replacement.capitalize()
    return replacement


def _normalize_preferred_english_tokens(text: str) -> str:
    """Normalize transliterated tokens to preferred English forms."""
    result = text
    for source, target in PREFERRED_EN_TOKENS.items():
        pattern = re.compile(rf"\b{re.escape(source)}\b", flags=re.IGNORECASE)
        result = pattern.sub(lambda m: _match_case(m.group(0), target), result)
    return result


def transliterate_bulgarian(text: str) -> str:
    """Transliterate Bulgarian text to Latin characters deterministically."""
    if not text:
        return text

    result = cyrtranslit.to_latin(text, "bg")
    # Project convention: ASCII-only Latin output for EN fallback fields.
    result = result.replace("Ă", "A").replace("ă", "a")

    # Normalize title-case digraphs at word starts for readability:
    # CHastna -> Chastna, TSar -> Tsar, SHTastie -> Shtastie, etc.
    title_digraphs = ["SHT", "TS", "ZH", "CH", "SH", "YU", "YA"]
    for digraph in title_digraphs:
        pattern = rf"\b{digraph}(?=[a-z])"
        result = re.sub(pattern, digraph.title(), result)

    return _normalize_preferred_english_tokens(result)


def transliterate_address(address: str) -> str:
    """Transliterate Bulgarian address while preserving common short forms."""
    if not address:
        return address

    result = address
    for bg_abbr, latin_abbr in ADDRESS_ABBREVIATIONS.items():
        result = re.sub(rf"(?<!\w){re.escape(bg_abbr)}", latin_abbr, result)

    return transliterate_bulgarian(result)
