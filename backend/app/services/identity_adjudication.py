"""Bounded LLM adjudication of English school identities (advisory, never publishing).

The deterministic resolver in ``app.scrapers.extractor`` requires two independent
signals before an English display name may publish. Some correct identities carry
only page evidence because the official domain does not spell the brand, while
several unsafe labels (network names, sibling institutions sharing one domain,
contact and navigation strings) look identical to the deterministic signals.

This module assembles bounded evidence from **cached, already-valid official
pages only** and asks a model to adjudicate a single question per case: does this
English label name *this* institution on its own official site? The verdict is
advisory. It never writes to the database, never publishes, and every accept is
re-checked by deterministic guards that can only reject. Promotion stays a manual
step through ``app.services.identity_curation``.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Literal, Mapping, Sequence
from urllib.parse import urlparse

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import calculate_cost, create_agent, extract_provider_cost_usd, get_model
from app.models import School, SourcePage
from app.models.scrape_log import ScrapeType
from app.scrapers import extractor
from app.scrapers import extractor_helpers as helpers
from app.scrapers.display_name_audit import _clean_candidate_text, _page_bonus
from app.services.identity_curation import _normalized_host, _source_page_key
from app.services.provider_costs import execute_billable_request


# Bounded evidence budget. These caps exist so a pilot over many schools cannot
# silently turn into a large-context job; they are deliberately small.
MAX_PAGES_PER_CASE = 6
MAX_MATCHED_LINES_PER_PAGE = 4
MAX_CONTEXT_LINES_PER_PAGE = 3
MAX_LINE_CHARS = 200
MAX_PAGE_SCAN_CHARS = 15000
MAX_SIBLINGS = 6
MAX_QUOTES = 4

# Accept thresholds. ``MIN_SUPPORTING_SOURCE_URLS`` mirrors the two same-domain
# source URLs that ``identity_curation`` requires before a human may promote, so
# a recommendation here cannot be weaker than the promotion gate.
MIN_ACCEPT_CONFIDENCE = 0.7
MIN_SUPPORTING_SOURCE_URLS = 2

ADJUDICATION_TIMEOUT_SECONDS = 45.0
ADJUDICATION_TIER = "capable"

_SCHOOL_LEVELS = {"primary", "lower_secondary", "upper_secondary"}
_EARLY_YEARS_LEVELS = {"nursery", "kindergarten"}
_SCHOOL_WORDS = {"school", "gymnasium", "lyceum", "college", "university"}
_EARLY_YEARS_WORDS = {"kindergarten", "nursery", "creche", "preschool"}

_CONTACT_MARKER_RE = re.compile(
    r"(?i)(@|https?://|www\.|\be-?mail\b|\btel\b|\bphone\b|\+\d{3}|\b\d{6,}\b)"
)
_NAVIGATION_RE = re.compile(
    r"(?i)^(visit|apply|book|contact|call|enrol|enroll|join|learn|read|see|"
    r"discover|download|register|explore|find|click|sign)\b"
)


ReasonCode = Literal[
    "own_official_identity",
    "network_or_group_label",
    "different_institution_on_shared_domain",
    "contact_or_navigation_label",
    "marketing_or_prose_fragment",
    "insufficient_official_evidence",
]


class IdentityAdjudicationVerdict(BaseModel):
    """Structured model output for one candidate identity."""

    verdict: Literal["accept", "reject"]
    reason_code: ReasonCode
    confidence: float = Field(ge=0.0, le=1.0)
    quoted_evidence: list[str] = Field(default_factory=list, max_length=MAX_QUOTES)


@dataclass(frozen=True)
class PageExcerpt:
    source_url: str
    page_category: str | None
    is_core_page: bool
    contains_candidate: bool
    lines: tuple[str, ...]


@dataclass(frozen=True)
class SiblingContext:
    school_id: int
    registry_name: str | None
    education_level: str | None
    website_url: str | None


@dataclass(frozen=True)
class IdentityAdjudicationCase:
    school_id: int
    candidate_en: str | None
    registry_name: str | None
    city: str | None
    education_level: str | None
    website_url: str | None
    registrable_domain: str | None
    deterministic_signals: dict[str, bool] = field(default_factory=dict)
    supporting_source_urls: tuple[str, ...] = ()
    excerpts: tuple[PageExcerpt, ...] = ()
    siblings: tuple[SiblingContext, ...] = ()
    blocked_reason: str | None = None

    @property
    def adjudicable(self) -> bool:
        return bool(self.candidate_en) and self.blocked_reason is None


def _normalized_text(value: str | None) -> str:
    """Match the deterministic exact-page comparison used by the resolver.

    The result keeps its leading and trailing space. Both sides of a containment
    check must stay padded: stripping the needle turns the check into an
    arbitrary substring match, where ``Sunny House`` would be "present" in
    ``Sunny Houses``.
    """
    text = re.sub(r"https?://\S+", " ", str(value or ""), flags=re.IGNORECASE)
    return " " + re.sub(r"[^\w]+", " ", text.casefold()).strip() + " "


def _contains_label(haystack: str | None, padded_label: str) -> bool:
    """Token-boundary containment for an already-padded normalized label."""
    return len(padded_label.strip()) > 0 and padded_label in _normalized_text(haystack)


def _registrable_domain(url: str | None) -> str | None:
    host = (urlparse(str(url or "")).netloc or "").casefold().split(":", 1)[0]
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return None
    parts = extractor._HOST_SUFFIX_EXTRACTOR(host)
    registrable = getattr(parts, "top_domain_under_public_suffix", None) or parts.registered_domain
    return registrable or host


def _english_candidate(display_name_i18n: Mapping[str, str] | None) -> str | None:
    """Return a publishable English label, ignoring Bulgarian-only candidates."""
    if not display_name_i18n:
        return None
    value = display_name_i18n.get("en")
    bulgarian = display_name_i18n.get("bg")
    if not value and bulgarian and not re.search(r"[А-Яа-я]", bulgarian):
        value = bulgarian
    if not value or re.search(r"[А-Яа-я]", value):
        return None
    return value


def _looks_like_label_noise(text: str) -> bool:
    """Reject shapes a name never has.

    Deliberately narrower than ``display_name_audit._looks_like_noise``: that
    heuristic mines candidates out of page text and rejects short single-word
    labels, but published identities such as ``Growers`` are exactly that.
    """
    if len(text) > 120 or len(text) < 2:
        return True
    if not re.search(r"[A-Za-zА-Яа-я]", text):
        return True
    if any(marker in text for marker in "[](){}|"):
        return True
    if re.search(r"(?i)\b(?:ltd|llc|inc|eood|ood|ad)\b", text):
        return True
    # Prose, not a name.
    return len(text.split()) > 10


def deterministic_reject_reason(candidate: str, *, education_level: str | None) -> str | None:
    """Reject labels that can never be a school's own English identity.

    Guards only ever reject. They run before and after the model so an accept
    cannot rest on the model alone.
    """
    text = str(candidate or "").strip()
    if not text:
        return "empty_candidate"
    if _CONTACT_MARKER_RE.search(text):
        return "contact_or_navigation_label"
    if _NAVIGATION_RE.match(text):
        return "contact_or_navigation_label"
    if _looks_like_label_noise(text):
        return "noise_label"

    words = set(re.findall(r"[a-z]+", text.casefold()))
    level = str(education_level or "").strip().lower()
    if level in _EARLY_YEARS_LEVELS and words & _SCHOOL_WORDS:
        return "institution_class_conflicts_with_education_level"
    if level in _SCHOOL_LEVELS and words & _EARLY_YEARS_WORDS:
        return "institution_class_conflicts_with_education_level"
    return None


def _excerpt_lines(text: str, candidate_key: str, *, limit: int) -> tuple[list[str], bool]:
    matched: list[str] = []
    context: list[str] = []
    for raw_line in text.splitlines():
        cleaned = _clean_candidate_text(raw_line)
        if not cleaned:
            continue
        if _contains_label(raw_line, candidate_key):
            if len(matched) < limit:
                matched.append(cleaned[:MAX_LINE_CHARS])
        elif len(context) < MAX_CONTEXT_LINES_PER_PAGE:
            context.append(cleaned[:MAX_LINE_CHARS])
    if matched:
        return matched, True
    return context, False


def _is_homepage(page: SourcePage, *, website_url: str | None) -> bool:
    configured = urlparse(str(website_url or ""))
    configured_host = configured.netloc.casefold().removeprefix("www.")
    configured_path = "/" + (configured.path or "").strip("/")
    parsed = urlparse(str(page.source_url or ""))
    page_host = parsed.netloc.casefold().removeprefix("www.")
    page_path = "/" + (parsed.path or "").strip("/")
    if bool(configured_host) and page_host == configured_host and page_path == configured_path:
        return True
    if page_path == "/":
        return True
    return re.fullmatch(r"/[a-z]{2}(?:[-_][a-z]{2})?", page_path, flags=re.IGNORECASE) is not None


def build_case(
    *,
    school: School,
    candidate: Mapping[str, str] | None,
    pages: Sequence[SourcePage],
    siblings: Sequence[SiblingContext] = (),
) -> IdentityAdjudicationCase:
    """Assemble one bounded case from cached pages. Pure, read-only, no I/O."""
    registry_name = (school.name_i18n or {}).get("bg") or (school.name_i18n or {}).get("en")
    normalized = helpers._normalize_display_name_i18n(dict(candidate or {}), school.country_code) or {}
    candidate_en = _english_candidate(normalized)
    website_url = school.website_url
    domain = _registrable_domain(website_url)

    base = IdentityAdjudicationCase(
        school_id=int(school.id),
        candidate_en=candidate_en,
        registry_name=registry_name,
        city=school.city,
        education_level=school.education_level,
        website_url=website_url,
        registrable_domain=domain,
        siblings=tuple(siblings[:MAX_SIBLINGS]),
    )
    if not candidate_en:
        return replace(base, blocked_reason="no_english_candidate")

    evidence_name = {"en": candidate_en}
    signals = {
        "website_domain_alias_match": extractor._display_name_has_domain_alias_match(
            evidence_name, registry_name=registry_name, website_url=website_url
        ),
        "exact_official_page_identity": extractor._display_name_has_exact_official_page_identity(
            evidence_name, website_url=website_url, pages=list(pages)
        ),
        "repeated_on_page_identity": extractor._display_name_has_repeated_page_identity(
            evidence_name, school=school, pages=list(pages)
        ),
    }

    candidate_key = _normalized_text(candidate_en)
    scored: list[tuple[tuple[int, int, int], PageExcerpt, str | None]] = []
    for page in pages:
        text = (page.raw_markdown or "")[:MAX_PAGE_SCAN_CHARS]
        if not text:
            continue
        payload = {"source_url": page.source_url, "page_category": page.page_category}
        is_core = _is_homepage(page, website_url=website_url) or _page_bonus(payload) > 0
        lines, matched = _excerpt_lines(text, candidate_key, limit=MAX_MATCHED_LINES_PER_PAGE)
        if not lines:
            continue
        excerpt = PageExcerpt(
            source_url=str(page.source_url or ""),
            page_category=page.page_category,
            is_core_page=is_core,
            contains_candidate=matched,
            lines=tuple(lines),
        )
        # Matched core pages first, then matched pages, then core context pages.
        rank = (0 if matched else 1, 0 if is_core else 1, int(page.id or 0))
        supporting = excerpt.source_url if matched else None
        scored.append((rank, excerpt, supporting))

    scored.sort(key=lambda row: row[0])
    excerpts = tuple(row[1] for row in scored[:MAX_PAGES_PER_CASE])

    # Supporting URLs are counted over *all* matched cached pages, deduplicated
    # exactly like the promotion gate. The host comparison must be exact rather
    # than registrable-domain: `curated_identity_candidate` rejects any source
    # whose normalized host differs from the configured website host, so a
    # sibling host like `network.example.org` would produce a recommendation
    # that promotion then refuses with `source_domain_mismatch`.
    official_host = _normalized_host(website_url)
    supporting: dict[tuple[str, str], str] = {}
    for _rank, excerpt, url in scored:
        if not url or not official_host or _normalized_host(url) != official_host:
            continue
        key = _source_page_key(url)
        if key is not None:
            supporting.setdefault(key, url)

    return replace(
        base,
        deterministic_signals=signals,
        supporting_source_urls=tuple(supporting.values()),
        excerpts=excerpts,
        blocked_reason=deterministic_reject_reason(
            candidate_en, education_level=school.education_level
        ),
    )


async def build_cases(
    db: AsyncSession,
    requests: Sequence[Mapping[str, Any]],
) -> list[IdentityAdjudicationCase]:
    """Load cached evidence for the requested (school_id, candidate) pairs.

    Reads only already-valid cached website pages. No refresh, provider call,
    geocode, OCR, or write is performed.
    """
    school_ids = sorted({int(request["school_id"]) for request in requests})
    if not school_ids:
        return []

    schools = {
        int(school.id): school
        for school in (
            await db.execute(select(School).where(School.id.in_(school_ids)))
        ).scalars().all()
    }
    missing = sorted(set(school_ids) - set(schools))
    if missing:
        raise ValueError(f"Adjudication schools missing from database: {missing}")

    pages_by_school: dict[int, list[SourcePage]] = {school_id: [] for school_id in school_ids}
    cached_pages = (
        await db.execute(
            select(SourcePage).where(
                SourcePage.school_id.in_(school_ids),
                SourcePage.scrape_type == ScrapeType.WEBSITE,
                SourcePage.is_valid.is_(True),
                SourcePage.raw_markdown.isnot(None),
            )
        )
    ).scalars().all()
    for page in cached_pages:
        if page.school_id is not None and int(page.school_id) in pages_by_school:
            pages_by_school[int(page.school_id)].append(page)

    # Shared-domain siblings: several Sofia institutions publish under one
    # domain, which is exactly how a network or sibling-school label leaks into
    # the wrong school's identity.
    domain_rows = (
        await db.execute(
            select(
                School.id,
                School.name_i18n,
                School.website_url,
                School.education_level,
            ).where(School.website_url.isnot(None))
        )
    ).all()
    siblings_by_domain: dict[str, list[SiblingContext]] = {}
    for row_id, name_i18n, website_url, education_level in domain_rows:
        domain = _registrable_domain(website_url)
        if not domain:
            continue
        siblings_by_domain.setdefault(domain, []).append(
            SiblingContext(
                school_id=int(row_id),
                registry_name=(name_i18n or {}).get("bg") or (name_i18n or {}).get("en"),
                education_level=education_level,
                website_url=website_url,
            )
        )

    cases: list[IdentityAdjudicationCase] = []
    for request in requests:
        school = schools[int(request["school_id"])]
        domain = _registrable_domain(school.website_url)
        siblings = [
            sibling
            for sibling in siblings_by_domain.get(domain or "", [])
            if sibling.school_id != int(school.id)
        ]
        cases.append(
            build_case(
                school=school,
                candidate=request.get("candidate"),
                pages=pages_by_school[int(school.id)],
                siblings=sorted(siblings, key=lambda sibling: sibling.school_id),
            )
        )
    return cases


SYSTEM_PROMPT = (
    "You decide whether one English label is the official English identity of one "
    "specific school, using only the supplied cached text from that school's own "
    "official website.\n"
    "Accept only when the evidence shows the label naming THIS institution as itself.\n"
    "Reject when the label is: the name of a parent network, group, or franchise "
    "rather than this institution; the name of a different institution that shares "
    "the same website domain (check the listed shared-domain institutions and the "
    "registry name and education level of each); a contact string, menu item, "
    "button, or navigation label; marketing prose or a sentence fragment; or when "
    "the evidence is too thin to tell.\n"
    "A translation you invent is never acceptable: the exact English label must "
    "appear in the supplied evidence.\n"
    "Quote at most four short lines, copied verbatim from the supplied evidence, "
    "that justify the verdict. Never quote text that is not present above.\n"
    "When uncertain, reject."
)


def build_user_prompt(case: IdentityAdjudicationCase) -> str:
    lines = [
        f"Candidate English label: {case.candidate_en}",
        f"Registry name (Bulgarian): {case.registry_name or 'unknown'}",
        f"Education level: {case.education_level or 'unknown'}",
        f"City: {case.city or 'unknown'}",
        f"Official website: {case.website_url or 'unknown'}",
        "",
    ]
    if case.siblings:
        lines.append(
            f"Other institutions sharing the domain {case.registrable_domain}: "
            "a label naming one of these is NOT this school's identity."
        )
        for sibling in case.siblings:
            lines.append(
                f"- school {sibling.school_id}: {sibling.registry_name or 'unknown'} "
                f"(level: {sibling.education_level or 'unknown'})"
            )
    else:
        lines.append(f"No other institution shares the domain {case.registrable_domain}.")
    lines.append("")
    lines.append("Cached official-page evidence:")
    for excerpt in case.excerpts:
        marker = "contains the candidate" if excerpt.contains_candidate else "context only"
        lines.append(f"[{excerpt.source_url}] ({marker})")
        for line in excerpt.lines:
            lines.append(f"  {line}")
    lines.append("")
    lines.append(
        "Question: is the candidate label the official English identity of this "
        "institution itself?"
    )
    return "\n".join(lines)


def apply_guards(
    case: IdentityAdjudicationCase,
    verdict: IdentityAdjudicationVerdict,
) -> tuple[str, list[str]]:
    """Re-check an accept deterministically. Returns (decision, guard failures)."""
    failures: list[str] = []
    if verdict.verdict != "accept":
        return "rejected", failures

    # The schema cannot tie the verdict to its reason, so an accept carrying a
    # rejection reason is internally inconsistent and must fail closed.
    if verdict.reason_code != "own_official_identity":
        failures.append("inconsistent_accept_reason")

    reason = deterministic_reject_reason(
        str(case.candidate_en or ""), education_level=case.education_level
    )
    if reason:
        failures.append(reason)

    # An accept must be justified by evidence, so an empty quote list cannot pass
    # verification vacuously.
    quotes = [quote for quote in verdict.quoted_evidence if _normalized_text(quote).strip()]
    if not quotes:
        failures.append("missing_evidence_quote")

    # Each quote must sit inside a single evidence line. Verifying against the
    # concatenated corpus would accept a quote fabricated from the tail of one
    # line and the head of the next — text that exists in no source.
    evidence_lines = [
        _normalized_text(line) for excerpt in case.excerpts for line in excerpt.lines
    ]
    for quote in quotes:
        padded_quote = _normalized_text(quote)
        if not any(padded_quote in line for line in evidence_lines):
            failures.append("unverifiable_quote")
            break

    if not any(excerpt.contains_candidate for excerpt in case.excerpts):
        failures.append("candidate_absent_from_cached_pages")
    if verdict.confidence < MIN_ACCEPT_CONFIDENCE:
        failures.append("confidence_below_floor")

    if failures:
        return "rejected", failures
    if len(case.supporting_source_urls) < MIN_SUPPORTING_SOURCE_URLS:
        return "hold_insufficient_provenance", ["insufficient_source_urls"]
    return "recommend_manual_promotion", failures


def case_row(case: IdentityAdjudicationCase, *, decision: str = "rejected") -> dict[str, Any]:
    """Reportable row for a case. Defaults to the fail-closed outcome."""
    return {
        "school_id": case.school_id,
        "candidate_en": case.candidate_en,
        "registry_name": case.registry_name,
        "education_level": case.education_level,
        "website_url": case.website_url,
        "shared_domain_siblings": [sibling.school_id for sibling in case.siblings],
        "deterministic_signals": dict(case.deterministic_signals),
        "supporting_source_urls": list(case.supporting_source_urls),
        "evidence_pages": len(case.excerpts),
        "llm_called": False,
        "verdict": None,
        "reason_code": None,
        "confidence": None,
        "quoted_evidence": [],
        "guard_failures": [],
        "decision": decision,
        "input_tokens": 0,
        "output_tokens": 0,
        "token_cost_usd": 0.0,
    }


async def adjudicate_case(
    case: IdentityAdjudicationCase,
    *,
    agent_factory: Callable[[], Any] | None = None,
    timeout_seconds: float = ADJUDICATION_TIMEOUT_SECONDS,
    prefilter_rejects: bool = True,
) -> dict[str, Any]:
    """Adjudicate one case. Fail-closed: any error rejects and never publishes."""
    row = case_row(case)

    if not case.candidate_en:
        row["reason_code"] = case.blocked_reason or "no_english_candidate"
        return row
    if case.blocked_reason and prefilter_rejects:
        row["reason_code"] = case.blocked_reason
        row["guard_failures"] = [case.blocked_reason]
        return row
    if not case.excerpts:
        row["reason_code"] = "insufficient_official_evidence"
        row["guard_failures"] = ["no_cached_evidence"]
        return row

    try:
        # Agent construction can fail on its own (no or malformed API key), which
        # must reject this case rather than abort the run. Only after that does a
        # dispatch happen — and a dispatched request may be billed even when it
        # times out, so the flag is set before the await, never before.
        agent = (agent_factory or _default_agent)()
        row["llm_called"] = True
        raw_result = await asyncio.wait_for(
            execute_billable_request(
                lambda: agent.run(build_user_prompt(case)),
                model=get_model(ADJUDICATION_TIER),
                school_id=case.school_id,
                stage="adjudicate-identity",
            ),
            timeout=max(5.0, float(timeout_seconds)),
        )
    except Exception as exc:  # provider/timeout failures must not publish
        row["reason_code"] = "adjudication_failed"
        row["guard_failures"] = [f"error:{type(exc).__name__}"]
        row["error"] = str(exc)
        return row

    input_tokens, output_tokens = _usage(raw_result)
    row["input_tokens"] = input_tokens
    row["output_tokens"] = output_tokens
    row["token_cost_usd"] = round(
        float(
            extract_provider_cost_usd(raw_result)
            or calculate_cost(ADJUDICATION_TIER, input_tokens, output_tokens)
        ),
        6,
    )

    verdict = _parse_verdict(raw_result)
    if verdict is None:
        row["reason_code"] = "adjudication_failed"
        row["guard_failures"] = ["invalid_model_output"]
        return row

    decision, failures = apply_guards(case, verdict)
    row.update(
        {
            "verdict": verdict.verdict,
            "reason_code": verdict.reason_code,
            "confidence": round(float(verdict.confidence), 3),
            "quoted_evidence": list(verdict.quoted_evidence),
            "guard_failures": failures,
            "decision": decision,
        }
    )
    return row


def _default_agent() -> Any:
    # No retries: PydanticAI would issue an extra billable request on output
    # validation failure, so one case could quietly cost two provider calls while
    # the pilot reports one. A malformed response fails closed instead.
    return create_agent(
        tier=ADJUDICATION_TIER,
        system_prompt=SYSTEM_PROMPT,
        result_type=IdentityAdjudicationVerdict,
        retries=0,
        output_retries=0,
    )


def _parse_verdict(raw_result: Any) -> IdentityAdjudicationVerdict | None:
    payload = getattr(raw_result, "output", None)
    if payload is None:
        payload = getattr(raw_result, "data", None)
    if isinstance(payload, IdentityAdjudicationVerdict):
        return payload
    if isinstance(payload, Mapping):
        try:
            return IdentityAdjudicationVerdict.model_validate(dict(payload))
        except Exception:
            return None
    return None


def _usage(raw_result: Any) -> tuple[int, int]:
    from app.scrapers import extractor_helpers as extraction_helpers

    try:
        return extraction_helpers._get_usage(raw_result)
    except Exception:
        return 0, 0


__all__ = [
    "IdentityAdjudicationCase",
    "IdentityAdjudicationVerdict",
    "PageExcerpt",
    "SiblingContext",
    "SYSTEM_PROMPT",
    "adjudicate_case",
    "apply_guards",
    "build_case",
    "build_cases",
    "build_user_prompt",
    "case_row",
    "deterministic_reject_reason",
]
