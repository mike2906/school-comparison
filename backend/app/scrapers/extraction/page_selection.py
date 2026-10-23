"""Choose and trim the pages sent to each extraction call."""

from __future__ import annotations

import re

from app.config import get_settings
from app.models.school import School
from app.models.source_page import SourcePage
from app.scrapers.extraction.summary_sources import (
    _SUMMARY_SOURCE_PAGE_BAD_TEXT_TOKENS,
    _SUMMARY_SOURCE_PAGE_BAD_URL_TOKENS,
    _SUMMARY_SOURCE_PAGE_GOOD_URL_TOKENS,
    _classify_summary_source_page,
    _prepare_summary_source_page_text,
)
from app.scrapers.extraction.text_values import (
    _canonical_host,
    _host_matches,
)

# A queued page at or under this size has room held back for it in full while packing,
# because a small page is the one an oversized page can starve completely. Larger queued
# pages get no reserve: they would be truncated either way, so holding room back for them
# only costs the higher-ranked page content it would have used.
_SMALL_QUEUED_PAGE_CHARS = 2000


# Never hold back more than this share of the budget, so a school whose content genuinely
# is one long page keeps the bulk of it even behind several small pages.
_MAX_RESERVE_FRACTION = 3


# A *truncated* fragment below this size carries nothing usable, so it is skipped.
# A complete page is never measured against this: a short page is whole evidence, not
# a fragment, and dropping it recreates the starvation this packing rule prevents.
_MIN_USABLE_PAGE_CHARS = 500


def _select_pages(
    school: School,
    pages: list[SourcePage],
    preferred_categories: list[str],
    use_case: str = "general_info",
    include_tokens: tuple[str, ...] | None = None,
) -> tuple[str, list[str]]:
    """Build bounded prompt content with simple relevance ranking."""
    settings = get_settings()
    max_chars = max(2000, int(settings.extraction_max_content_chars))
    if use_case == "general_summary_source":
        max_chars = min(max_chars, 6000)
    elif use_case == "general_info" or use_case.startswith("general_"):
        # Field-focused extraction runs multiple passes; keep each prompt compact.
        max_chars = min(max_chars, 8000)

    preferred = {category.lower() for category in preferred_categories}
    school_host = _canonical_host(school.website_url)

    # Keep pages on the same domain when available to avoid polluting extraction with
    # third-party directories/news portals that may have been navigated.
    same_host_pages = [
        page
        for page in pages
        if _host_matches(_canonical_host(page.source_url), school_host)
    ]
    candidate_pages = same_host_pages or pages
    summary_page_classifications = (
        {id(page): _classify_summary_source_page(page) for page in candidate_pages}
        if use_case == "general_summary_source"
        else {}
    )
    if use_case == "general_summary_source":
        narrative_pages = [
            page for page in candidate_pages if summary_page_classifications.get(id(page)) == "narrative"
        ]
        if narrative_pages:
            candidate_pages = narrative_pages
        else:
            non_noise_pages = [
                page for page in candidate_pages if summary_page_classifications.get(id(page)) != "noise"
            ]
            candidate_pages = non_noise_pages or candidate_pages

    def score_page(page: SourcePage) -> int:
        score = 0
        category = (page.page_category or "").lower()
        page_url = (page.source_url or "").lower()
        page_text = (page.raw_markdown or "").lower()[:2500]
        if category in preferred:
            score += 100
        elif category:
            score += 20

        # Home/root URLs are usually useful fallback context.
        if page.source_url and re.search(r"https?://[^/]+/?$", page.source_url):
            score += 40

        # Slightly prefer shorter paths and recent pages.
        path_len = len(page.source_url or "")
        score += max(0, 30 - min(path_len, 30))

        page_host = _canonical_host(page.source_url)
        if _host_matches(page_host, school_host):
            score += 80

        if include_tokens:
            url_hits = sum(1 for token in include_tokens if token in page_url)
            text_hits = sum(1 for token in include_tokens if token in page_text)
            if url_hits:
                score += min(90, 35 * url_hits)
            if text_hits:
                score += min(120, 25 * text_hits)

        if use_case == "general_info":
            if category in {"about", "contact"}:
                score += 40
            if any(
                token in page_url
                for token in (
                    "about",
                    "za-nas",
                    "team",
                    "ekip",
                    "program",
                    "obuchenie",
                    "curriculum",
                    "mission",
                    "vision",
                )
            ):
                score += 60
            if any(
                token in page_url
                for token in (
                    "admission",
                    "priem",
                    "policy",
                    "privacy",
                    "gdpr",
                    "rules",
                    "regulation",
                    "internal",
                    "document",
                    "forms",
                    "application",
                    "terms",
                    "conditions",
                    "legal",
                )
            ):
                score -= 90
            if category in {"admission", "pricing"}:
                score -= 35
        elif use_case == "general_summary_source":
            classification = summary_page_classifications.get(id(page))
            if classification == "narrative":
                score += 140
            elif classification == "operational":
                score -= 80
            elif classification == "noise":
                score -= 220
            if category in {"about", "programs", "facilities"}:
                score += 80
            if category in {"contact", "admission", "pricing", "gallery", "news"}:
                score -= 110
            if any(token in page_url for token in _SUMMARY_SOURCE_PAGE_GOOD_URL_TOKENS):
                score += 90
            if any(token in page_url for token in _SUMMARY_SOURCE_PAGE_BAD_URL_TOKENS):
                score -= 140
            if any(token in page_text for token in _SUMMARY_SOURCE_PAGE_BAD_TEXT_TOKENS):
                score -= 100
        elif use_case.startswith("general_"):
            if category in {"about", "contact"}:
                score += 45
            if any(token in page_url for token in ("admission", "priem", "policy", "gdpr", "terms", "legal")):
                score -= 90
        return score

    sorted_pages = sorted(candidate_pages, key=score_page, reverse=True)

    max_pages = 3 if (use_case == "general_info" or use_case.startswith("general_")) else 4
    if use_case == "general_summary_source":
        max_pages = 2

    # Materialize the pages that will actually be packed, so each one can reserve room
    # for the pages still queued behind it.
    queued: list[tuple[str, str]] = []
    for page in sorted_pages:
        if len(queued) >= max_pages:
            break
        text = (page.raw_markdown or "").strip()
        if not text:
            continue
        if use_case == "general_summary_source":
            text = _prepare_summary_source_page_text(text)
            if not text:
                continue
        queued.append((page.source_url or "", f"--- SOURCE: {page.source_url} ---\n" + text))

    content_parts: list[str] = []
    urls_used: list[str] = []
    current_chars = 0

    for index, (source_url, candidate) in enumerate(queued):
        # One oversized page must not swallow the whole budget and starve a small page.
        reserved = min(
            sum(
                len(later)
                for _, later in queued[index + 1 :]
                if len(later) <= _SMALL_QUEUED_PAGE_CHARS
            ),
            max_chars // _MAX_RESERVE_FRACTION,
        )
        allowance = max_chars - current_chars - reserved

        if len(candidate) > allowance:
            if allowance < _MIN_USABLE_PAGE_CHARS:
                # Only a useless fragment would fit; skip it and keep going, so pages
                # behind it can still use the room reserved for them.
                continue
            candidate = candidate[:allowance]

        content_parts.append(candidate)
        urls_used.append(source_url)
        current_chars += len(candidate)

    return "\n\n".join(content_parts), [url for url in urls_used if url]
