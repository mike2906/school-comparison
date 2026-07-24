"""Bounded promotion of source-backed, manually curated school identities."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import FieldSource, School, SourceConfidence, SourceType
from app.utils.i18n_resolver import resolve_display_name_i18n


CURATED_IDENTITY_METHOD = "offline_cached_source_review"
CANONICAL_IDENTITY_CURATION_KEY = "canonical_identity_curation"


@dataclass(frozen=True)
class CuratedIdentityCandidate:
    english_name: str | None
    source_urls: tuple[str, ...] = ()
    reviewed_at: datetime | None = None
    reviewed_by: str | None = None
    reason: str | None = None

    @property
    def eligible(self) -> bool:
        return self.reason is None and bool(self.english_name)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _normalized_host(url: str | None) -> str | None:
    try:
        host = (urlparse(str(url or "")).hostname or "").strip().lower()
    except ValueError:
        return None
    if host.startswith("www."):
        host = host[4:]
    return host or None


def _source_page_key(url: str) -> tuple[str, str] | None:
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https"}:
        return None
    host = _normalized_host(url)
    if not host:
        return None
    return host, parsed.path.rstrip("/") or "/"


def _parse_reviewed_at(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def curated_identity_candidate(school: School) -> CuratedIdentityCandidate:
    """Return an eligible manual identity only when its provenance is self-consistent."""
    attrs = _mapping(school.attributes)
    evidence = _mapping(attrs.get("display_name_evidence"))
    curation = _mapping(evidence.get("curation"))

    if evidence.get("status") != "corroborated":
        return CuratedIdentityCandidate(None, reason="identity_not_corroborated")
    if curation.get("method") != CURATED_IDENTITY_METHOD:
        return CuratedIdentityCandidate(None, reason="missing_manual_curation")

    display = resolve_display_name_i18n(attrs)
    english_name = display.get("en")
    if not english_name:
        return CuratedIdentityCandidate(None, reason="missing_publishable_english_candidate")

    raw_urls = curation.get("source_urls")
    if not isinstance(raw_urls, list):
        return CuratedIdentityCandidate(english_name, reason="insufficient_source_urls")
    distinct_pages: dict[tuple[str, str], str] = {}
    for raw_url in raw_urls:
        if not isinstance(raw_url, str) or not raw_url.strip():
            continue
        source_url = raw_url.strip()
        page_key = _source_page_key(source_url)
        if page_key is not None:
            distinct_pages.setdefault(page_key, source_url)
    source_urls = tuple(distinct_pages.values())
    if len(source_urls) < 2:
        return CuratedIdentityCandidate(english_name, source_urls, reason="insufficient_source_urls")

    official_host = _normalized_host(school.website_url)
    if not official_host or any(_normalized_host(url) != official_host for url in source_urls):
        return CuratedIdentityCandidate(english_name, source_urls, reason="source_domain_mismatch")

    reviewed_by = curation.get("reviewed_by")
    reviewed_at = _parse_reviewed_at(curation.get("reviewed_at"))
    if not isinstance(reviewed_by, str) or not reviewed_by.strip() or reviewed_at is None:
        return CuratedIdentityCandidate(
            english_name,
            source_urls,
            reason="incomplete_review_provenance",
        )

    return CuratedIdentityCandidate(
        english_name=english_name,
        source_urls=source_urls,
        reviewed_at=reviewed_at,
        reviewed_by=reviewed_by.strip(),
    )


def merge_authoritative_name_i18n(
    school: School,
    incoming_name_i18n: Mapping[str, str],
) -> dict[str, str]:
    """Keep only explicitly promoted locales omitted by an authoritative source."""
    merged = dict(incoming_name_i18n)
    if "en" in merged:
        return merged

    canonical_en = str(_mapping(school.name_i18n).get("en") or "").strip()
    metadata = _mapping(
        _mapping(_mapping(school.attributes).get(CANONICAL_IDENTITY_CURATION_KEY)).get("en")
    )
    if (
        canonical_en
        and metadata.get("status") == "promoted"
        and metadata.get("value") == canonical_en
    ):
        merged["en"] = canonical_en
    return merged


def _record_canonical_curation(
    school: School,
    *,
    candidate: CuratedIdentityCandidate,
    promoted_by: str,
) -> None:
    attrs = dict(school.attributes or {})
    canonical = dict(_mapping(attrs.get(CANONICAL_IDENTITY_CURATION_KEY)))
    existing = _mapping(canonical.get("en"))
    same_promotion = (
        existing.get("status") == "promoted"
        and existing.get("value") == candidate.english_name
    )
    canonical["en"] = {
        "status": "promoted",
        "value": candidate.english_name,
        "source_urls": list(candidate.source_urls),
        "reviewed_at": candidate.reviewed_at.isoformat() + "Z"
        if candidate.reviewed_at
        else None,
        "reviewed_by": candidate.reviewed_by,
        "promoted_at": existing.get("promoted_at")
        if same_promotion
        else datetime.now(timezone.utc).isoformat(),
        "promoted_by": existing.get("promoted_by") if same_promotion else promoted_by,
    }
    attrs[CANONICAL_IDENTITY_CURATION_KEY] = canonical
    school.attributes = attrs


async def _record_identity_sources(
    db: AsyncSession,
    *,
    school: School,
    candidate: CuratedIdentityCandidate,
    promoted_by: str,
) -> None:
    existing_urls = set(
        (
            await db.execute(
                select(FieldSource.source_url).where(
                    FieldSource.school_id == school.id,
                    FieldSource.field_key == "name_i18n.en",
                    FieldSource.source_url.in_(candidate.source_urls),
                    FieldSource.value_text == candidate.english_name,
                )
            )
        ).scalars()
    )
    for source_url in candidate.source_urls:
        if source_url in existing_urls:
            continue
        db.add(
            FieldSource(
                school_id=school.id,
                category="identity",
                field_key="name_i18n.en",
                field_path="name_i18n.en",
                value_text=candidate.english_name,
                source_type=SourceType.OFFICIAL_WEBSITE,
                source_name="Official school website",
                source_url=source_url,
                last_verified=candidate.reviewed_at,
                confidence=SourceConfidence.HIGH,
                confidence_score=1.0,
                submitted_by=promoted_by,
                verified=True,
                notes=f"Promoted from {CURATED_IDENTITY_METHOD}",
            )
        )


async def promote_curated_identities(
    db: AsyncSession,
    *,
    school_ids: Sequence[int],
    country: str,
    city: str | None,
    promoted_by: str,
    commit: bool = False,
) -> dict[str, Any]:
    """Promote an explicit cohort; dry-run unless ``commit`` is true."""
    requested_ids = sorted(set(int(school_id) for school_id in school_ids))
    if not requested_ids:
        raise ValueError("At least one school ID is required")
    if not promoted_by.strip():
        raise ValueError("promoted_by is required")

    query = select(School).where(
        School.id.in_(requested_ids),
        School.country_code == country,
    )
    if city:
        query = query.where(School.city == city)
    schools = {
        school.id: school
        for school in (await db.execute(query.order_by(School.id))).scalars().all()
    }

    summary: dict[str, Any] = {
        "requested": len(requested_ids),
        "eligible": 0,
        "promoted": 0,
        "already_promoted": 0,
        "conflicts": 0,
        "ineligible": 0,
        "rows": [],
    }
    for school_id in requested_ids:
        school = schools.get(school_id)
        if school is None:
            summary["ineligible"] += 1
            summary["rows"].append(
                {
                    "school_id": school_id,
                    "english_name": None,
                    "decision": "ineligible",
                    "reason": "school_not_found_in_scope",
                }
            )
            continue

        candidate = curated_identity_candidate(school)
        if not candidate.eligible:
            summary["ineligible"] += 1
            summary["rows"].append(
                {
                    "school_id": school_id,
                    "english_name": candidate.english_name,
                    "decision": "ineligible",
                    "reason": candidate.reason,
                }
            )
            continue

        canonical_en = str(_mapping(school.name_i18n).get("en") or "").strip()
        if canonical_en and canonical_en != candidate.english_name:
            summary["conflicts"] += 1
            summary["rows"].append(
                {
                    "school_id": school_id,
                    "english_name": candidate.english_name,
                    "decision": "conflict",
                    "reason": "canonical_english_name_conflict",
                }
            )
            continue

        summary["eligible"] += 1
        if canonical_en == candidate.english_name:
            summary["already_promoted"] += 1
            summary["rows"].append(
                {
                    "school_id": school_id,
                    "english_name": candidate.english_name,
                    "decision": "already_promoted",
                    "reason": None,
                }
            )
            if commit:
                _record_canonical_curation(
                    school,
                    candidate=candidate,
                    promoted_by=promoted_by.strip(),
                )
                await _record_identity_sources(
                    db,
                    school=school,
                    candidate=candidate,
                    promoted_by=promoted_by.strip(),
                )
            continue

        summary["rows"].append(
            {
                "school_id": school_id,
                "english_name": candidate.english_name,
                "decision": "promoted" if commit else "would_promote",
                "reason": None,
            }
        )
        if not commit:
            continue

        school.name_i18n = {**dict(school.name_i18n or {}), "en": candidate.english_name}
        _record_canonical_curation(
            school,
            candidate=candidate,
            promoted_by=promoted_by.strip(),
        )
        await _record_identity_sources(
            db,
            school=school,
            candidate=candidate,
            promoted_by=promoted_by.strip(),
        )
        summary["promoted"] += 1

    if commit:
        await db.commit()
    return summary
