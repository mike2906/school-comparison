"""Deterministic (no-LLM/no-network/no-DB) extraction path.

This is the canonical composition of the deterministic sub-steps that the Stage 5
extractor already relies on as its LLM-free fallback:

* pricing  -> page selection + ``_extract_prices_deterministic`` + support filter/dedupe + row coercion
* general  -> ``_build_deterministic_general_info_output`` + normalization + attribute projection
* display  -> ``_build_display_name_evidence``

Every leaf function used here is the *same* one production runs in
``extractor._extract_prices`` / ``_extract_general_info``, so the golden-fixture
corpus (P1.5) exercises real production logic rather than a hand-mirrored copy —
if a coercion or filter step changes in the extractor, this path changes with it.
"""

from __future__ import annotations

from typing import Any

from app.models.school import School
from app.models.source_page import SourcePage
from app.scrapers import extractor
from app.scrapers import extractor_helpers as helpers


def deterministic_pricing_rows(school: School, pages: list[SourcePage]) -> list[dict[str, Any]]:
    """Deterministic pricing rows as normalized field dicts (enums -> ``.value``)."""
    selected_text, _source_urls = helpers._select_pages(
        school=school,
        pages=pages,
        preferred_categories=["pricing", "admission", "contact"],
        use_case="pricing",
    )
    if not selected_text:
        return []

    deterministic = helpers._extract_prices_deterministic(selected_text)
    supported = extractor._supported_price_rows(deterministic.prices, selected_text)

    rows: list[dict[str, Any]] = []
    for extracted in supported:
        fields = extractor._normalized_price_fields(extracted)
        if fields is None:
            continue
        rows.append(
            {
                **fields,
                "category": fields["category"].value,
                "period": fields["period"].value,
            }
        )
    return rows


def deterministic_general_info(
    school: School, pages: list[SourcePage]
) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, str] | None]:
    """Deterministic ``(extracted, extracted_i18n, display_name_i18n)`` projection."""
    all_page_text = "\n".join((p.raw_markdown or "") for p in pages)
    narrative_text, _ = helpers._select_pages(
        school=school,
        pages=pages,
        preferred_categories=["about", "programs", "facilities"],
        use_case="general_summary_source",
        include_tokens=extractor.GENERAL_INFO_HINT_TOKENS,
    )
    school_name = (school.name_i18n or {}).get("bg") or (school.name_i18n or {}).get("en") or ""
    known_aliases = [
        str(value)
        for value in dict(school.attributes or {}).get("name_aliases", [])
        if str(value or "").strip()
    ]

    parsed = extractor._build_deterministic_general_info_output(
        all_page_text,
        narrative_text=narrative_text,
        registry_name=school_name,
        country_code=school.country_code,
        website_url=school.website_url,
        known_aliases=known_aliases,
    )
    normalized, extracted_i18n, display_name_i18n = helpers._normalize_general_info_output(
        parsed, school.country_code
    )
    contact_info = helpers._extract_contact_info_deterministic(all_page_text)
    extracted = extractor._build_extracted_attributes(normalized, contact_info)
    return extracted, extracted_i18n, display_name_i18n


def run_deterministic_extraction(school: School, pages: list[SourcePage]) -> dict[str, Any]:
    """Run the full deterministic extraction path, returning a serializable result."""
    extracted, extracted_i18n, display_name_i18n = deterministic_general_info(school, pages)
    display_name_evidence = extractor._build_display_name_evidence(
        display_name_i18n,
        school=school,
        pages=pages,
    )
    return {
        "pricing": deterministic_pricing_rows(school, pages),
        "general_info": {
            "extracted": extracted,
            "extracted_i18n": extracted_i18n,
            "display_name_i18n": display_name_i18n,
        },
        "display_name_evidence": display_name_evidence,
    }
