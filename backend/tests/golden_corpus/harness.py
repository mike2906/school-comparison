"""Deterministic extraction harness for the golden-fixture corpus (P1.5).

This module reproduces the *deterministic* (no-LLM, no-network) portion of the
Stage 5 extraction path so it can be exercised in CI against committed real
pages. It mirrors, step for step:

* pricing  -> ``extractor._extract_prices`` deterministic branch
* general  -> ``extractor._extract_general_info`` deterministic fallback
* display  -> ``extractor._build_display_name_evidence``

Keep this in sync with ``app/scrapers/extractor.py`` when the deterministic
text assembly changes. The referenced source lines are noted inline.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.models.school import School
from app.models.scrape_log import ScrapeType
from app.models.source_page import SourcePage
from app.scrapers import extractor
from app.scrapers import extractor_helpers as helpers


@dataclass
class FixturePage:
    """Lightweight stand-in for a persisted ``SourcePage`` row."""

    id: int
    source_url: str
    page_category: str | None
    raw_markdown: str


def build_school(case: dict[str, Any]) -> School:
    """Build a transient (un-persisted) School from fixture metadata."""
    attributes: dict[str, Any] = {}
    if case.get("name_aliases"):
        attributes["name_aliases"] = list(case["name_aliases"])
    return School(
        id=case["school_id"],
        name_i18n=case.get("name_i18n") or {},
        school_type=case.get("school_type"),
        country_code=case.get("country_code") or "bg",
        website_url=case.get("website_url"),
        attributes=attributes,
    )


def build_pages(pages: list[FixturePage]) -> list[SourcePage]:
    """Build transient SourcePage rows the deterministic helpers can read."""
    built: list[SourcePage] = []
    for page in pages:
        built.append(
            SourcePage(
                id=page.id,
                scrape_type=ScrapeType.WEBSITE,
                source_url=page.source_url,
                content_hash="0" * 64,
                page_category=page.page_category,
                raw_markdown=page.raw_markdown,
                is_valid=True,
            )
        )
    return built


def _run_pricing(school: School, pages: list[SourcePage]) -> list[dict[str, Any]]:
    """Mirror of ``extractor._extract_prices`` deterministic branch."""
    selected_text, _source_urls = helpers._select_pages(
        school=school,
        pages=pages,
        preferred_categories=["pricing", "admission", "contact"],
        use_case="pricing",
    )
    if not selected_text:
        return []

    deterministic = helpers._extract_prices_deterministic(selected_text)
    if not deterministic.prices:
        return []

    supported = helpers._filter_supported_prices(deterministic.prices, selected_text)
    supported = helpers._dedupe_price_rows(supported)

    rows: list[dict[str, Any]] = []
    for extracted in supported:
        category = helpers._coerce_price_category(extracted.category)
        period = helpers._coerce_price_period(extracted.period)
        amount = helpers._to_optional_float(extracted.amount)
        amount_min = helpers._to_optional_float(extracted.amount_min)
        amount_max = helpers._to_optional_float(extracted.amount_max)
        if category is None or period is None:
            continue
        if amount is None and amount_min is None and amount_max is None:
            continue
        rows.append(
            {
                "category": category.value,
                "period": period.value,
                "amount": amount,
                "amount_min": amount_min,
                "amount_max": amount_max,
                "currency": (extracted.currency or "BGN")[:3].upper(),
                "plan_name": helpers._normalize_scalar_text(extracted.plan_name, max_len=100),
                "academic_year": helpers._normalize_scalar_text(extracted.academic_year, max_len=20),
                "age_group": helpers._normalize_scalar_text(extracted.age_group, max_len=50),
            }
        )
    return rows


def _run_general_info(
    school: School, pages: list[SourcePage]
) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, str] | None]:
    """Mirror of ``extractor._extract_general_info`` deterministic fallback."""
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

    extracted: dict[str, Any] = {
        "_schema_version": 1,
        "languages": [entry.model_dump() for entry in normalized.languages],
        "facilities": normalized.facilities,
        "programs": normalized.programs,
        "extracurricular": normalized.extracurricular,
        "class_size": normalized.class_size,
        "founded_year": normalized.founded_year,
        "accreditations": normalized.accreditations,
        "admission": normalized.admission.model_dump(),
        "operations": normalized.operations.model_dump(),
        "services": normalized.services.model_dump(),
        "pricing_terms": normalized.pricing_terms.model_dump(),
        "summary_source": normalized.summary_source.model_dump(),
    }
    contact_info = helpers._extract_contact_info_deterministic(all_page_text)
    if contact_info:
        extracted["contact"] = contact_info

    return extracted, extracted_i18n, display_name_i18n


def run_deterministic_extraction(school: School, pages: list[SourcePage]) -> dict[str, Any]:
    """Run the deterministic extraction path and return a serializable result."""
    extracted, extracted_i18n, display_name_i18n = _run_general_info(school, pages)
    display_name_evidence = extractor._build_display_name_evidence(
        display_name_i18n,
        school=school,
        pages=pages,
    )
    return {
        "pricing": _run_pricing(school, pages),
        "general_info": {
            "extracted": extracted,
            "extracted_i18n": extracted_i18n,
            "display_name_i18n": display_name_i18n,
        },
        "display_name_evidence": display_name_evidence,
    }
