#!/usr/bin/env python3
"""UF45 step 1: read-only audit of published price rows and display names.

    uv run python scripts/audit_price_rows_uf45.py

Audits exactly what ``GET /schools/{id}`` returns: each school is loaded with the
detail query and projected through ``SchoolDetailResponse`` (the same gates), and every
published price row is checked against the stored text of its linked source page:

1. amount near its label  - the amount is on the page and, when the row's plan name (or
                            notes) is on the page, in that label's own run of prices;
2. level fits the school  - the label (or, with no label, the amount's label lines)
                            names the other level (``shared_site_check.describes_level``);
3. period from the text   - a null period with one period keyword next to the amount, a
                            keyword that disagrees with the stored period, or a period the
                            schema cannot represent (per day/week/hour);
4. deposit is not tuition - a tuition row whose label/context says deposit; 4b: a
                            non-monthly tuition row whose line says it is one installment;
5. name is not a sibling's - a published name equal to another institution's published
                            name in the same site group (``site_group_key``).

Writes ``reports/uf45/<timestamp>/hits.csv`` and ``summary.md``. Nothing is written to
the database: the session only reads and is rolled back.
"""
from __future__ import annotations

import asyncio
import csv
import decimal
import os
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.scrapers.shared_site_check import describes_level, level_family, site_group_key

REPORT_ROOT = Path(os.environ.get("UF45_REPORT_ROOT") or Path(__file__).parent.parent / "reports" / "uf45")

CONTEXT_CHARS = 100  # max characters taken either side of the amount on its own line
ATTACHED_LINES = 2  # label lines before / description lines after an amount line

# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

_CURRENCY = r"(?:€|\$|£|евро|лв\.?|лева|eur|euro|bgn|usd|gbp)"
# A number with a currency next to it: a price on the line.
_PRICE_RE = re.compile(
    rf"(?:{_CURRENCY}\s*\d[\d \u00a0.,]*|\d[\d \u00a0.,]*\s*{_CURRENCY})", re.IGNORECASE
)
_NUMBER_ONLY_LINE_RE = re.compile(r"^[\s€$£]*\d[\d \u00a0.,]*\s*$")


def normalize_text(text: str | None) -> str:
    """Casefolded page text, one space between words, line breaks kept."""
    lines = (re.sub(r"[ \t\u00a0]+", " ", line).strip() for line in str(text or "").splitlines())
    return "\n".join(line for line in lines if line).casefold()


def _label_key(text: str | None) -> str:
    return re.sub(r"[\s:;,.\-–—\"'„“”«»()]+", " ", str(text or "").casefold()).strip()


def amount_pattern(value: Any) -> Optional[re.Pattern[str]]:
    """Regex for a stored amount as a page may write it (6200, 6 200, 6.200, 181,44)."""
    try:
        number = decimal.Decimal(str(value))
    except (decimal.InvalidOperation, ValueError):
        return None
    if not number.is_finite() or number <= 0:
        return None
    number = number.quantize(decimal.Decimal("0.01"))
    whole, cents = f"{number:f}".split(".")
    # Thousands separators at group boundaries; scraped text also splits long numbers
    # at random ("40 0" for 400), so a single space is tolerated between other digits.
    integer = whole[0]
    for index in range(1, len(whole)):
        if (len(whole) - index) % 3 == 0:
            integer += r"[ \u00a0.,']?"
        elif len(whole) >= 3:
            integer += " ?"
        integer += whole[index]
    if cents == "00":
        fraction = r"(?:[.,]00?)?"
    else:
        fraction = rf"[.,]{cents.rstrip('0') if cents.endswith('0') else cents}0?"
    return re.compile(
        rf"(?<![\d])(?<!\d[.,]){integer}{fraction}(?![.,]?\d)(?!\s*(?:%|ч\b|ч\.|часа|h\b))"
    )


def amount_spans(text: str, value: Any) -> list[tuple[int, int]]:
    """Where the amount occurs, not counting part of a larger space-grouped number.

    "1 200 лв." holds neither 200 nor 1. A range or age before it is not a group:
    "група 0-3 970 евро" still holds 970.
    """
    pattern = amount_pattern(value)
    if pattern is None:
        return []
    spans = []
    for m in pattern.finditer(text):
        head_of_group = re.match(r"[  ]\d{3}(?![\d.,]?\d)", text[m.end():])
        tail_of_group = re.fullmatch(r"\d{3}", m.group(0)) and re.search(
            r"(?:^|[^\d\-–.,/:])\d{1,3}[  ]$", text[: m.start()]
        )
        if not (head_of_group or tail_of_group):
            spans.append(m.span())
    return spans


def label_spans(text: str, label: str | None) -> list[tuple[int, int]]:
    """Where a label occurs in normalized page text (case, spacing and punctuation-tolerant)."""
    words = _label_key(label).split()
    if not words:
        return []
    pattern = r"[\s:;,.\-–—\"'„“”«»()]+".join(re.escape(word) for word in words)
    return [m.span() for m in re.finditer(pattern, text)]


def _gap(a: tuple[int, int], b: tuple[int, int]) -> int:
    return max(0, max(a[0], b[0]) - min(a[1], b[1]))


def price_spans(text: str) -> list[tuple[int, int]]:
    """Every price on the page: a number with a currency, or a line that is only a number."""
    spans = [m.span() for m in _PRICE_RE.finditer(text)]
    for m in re.finditer(r"(?m)^[\d \u00a0.,]*\d[\d \u00a0.,]*$", text):
        if not any(start <= m.start() < end for start, end in spans):
            spans.append(m.span())
    return sorted(spans)


def _has_words(text: str) -> bool:
    return bool(re.search(r"[^\W\d_]{2,}", re.sub(_CURRENCY, " ", text, flags=re.IGNORECASE)))


def label_is_near(
    text: str, prices: list[tuple[int, int]], amount: tuple[int, int], label: tuple[int, int]
) -> bool:
    """True when the amount is in its label's own run of prices.

    Other prices may sit between the label and the amount only when nothing but prices
    separates them from the amount: the fee in another currency ("\u20ac 530 | 1037 \u043b\u0432."),
    or the monthly and yearly column of one row ("650.00 \u20ac 6 792.00 \u20ac"). A price
    followed by more label words belongs to another fee (630's catering row took the
    \u20ac6,200 printed several fees above its label).
    """
    before = label[0] < amount[0]
    low, high = (label[1], amount[0]) if before else (amount[1], label[0])
    inner = [
        p for p in prices
        if low <= p[0] and p[1] <= high and not (p[0] < amount[1] and amount[0] < p[1])
    ]
    if not inner:
        return True
    stretch = (inner[0][0], amount[0]) if before else (amount[1], inner[-1][1])
    words = text[stretch[0]: stretch[1]]
    for start, end in reversed([p for p in inner if stretch[0] <= p[0] and p[1] <= stretch[1]]):
        words = words[: start - stretch[0]] + " " + words[end - stretch[0]:]
    return not _has_words(words)


def _line_of(text: str, span: tuple[int, int]) -> str:
    start = text.rfind("\n", 0, span[0]) + 1
    end = text.find("\n", span[1])
    return text[start: len(text) if end < 0 else end]


def _is_price_line(line: str) -> bool:
    return bool(_PRICE_RE.search(line) or _NUMBER_ONLY_LINE_RE.match(line))


def amount_segment(text: str, span: tuple[int, int]) -> str:
    """The amount's own line, cut at the neighbouring prices on it."""
    start = text.rfind("\n", 0, span[0]) + 1
    line = _line_of(text, span)
    rel = (span[0] - start, span[1] - start)
    left, right = max(0, rel[0] - CONTEXT_CHARS), min(len(line), rel[1] + CONTEXT_CHARS)
    for match in _PRICE_RE.finditer(line):
        if match.end() <= rel[0]:
            left = max(left, match.end())
        elif match.start() >= rel[1]:
            right = min(right, match.start())
    return line[left:right]


def amount_context(text: str, span: tuple[int, int]) -> str:
    """The words that belong to one amount occurrence.

    Pages write a fee either as ``label:`` followed by the amount, or as the amount
    followed by ``- description``. So the context is: up to two label lines ending in
    ``:`` right before the amount's line, the amount's own segment, and up to two
    description lines after it that are not a price or the next fee's ``label:``.
    """
    start = text.rfind("\n", 0, span[0]) + 1
    end = text.find("\n", span[1])
    end = len(text) if end < 0 else end
    parts = [amount_segment(text, span)]

    before_lines = text[:start].split("\n")[:-1] if start else []
    for previous in reversed(before_lines[-ATTACHED_LINES:]):
        if _is_price_line(previous) or not previous.rstrip().endswith(":"):
            break
        parts.insert(0, previous)
    after_lines = text[end + 1:].split("\n") if end < len(text) else []
    for following in after_lines[:ATTACHED_LINES]:
        if _is_price_line(following) or following.rstrip().endswith(":"):
            break
        parts.append(following)
    return "\n".join(parts)


def _snippet(text: str, span: tuple[int, int], width: int = 90) -> str:
    return re.sub(r"\s+", " ", text[max(0, span[0] - width): span[1] + width]).strip()


# ---------------------------------------------------------------------------
# Rule vocabularies
# ---------------------------------------------------------------------------

# "3 пъти седмично" / "twice a week" is how often a class meets, not how often it is paid.
_FREQUENCY_UNIT = r"(?:седмично|в седмицата|на седмица|a week|per week|weekly|дневно|на ден|a day|per day|daily)"
_FREQUENCY_RE = re.compile(
    rf"\d+\s*(?:пъти|път|times?|x|хранения|meals)\s*{_FREQUENCY_UNIT}"
    rf"|(?:веднъж|двукратно|twice|once)\s*{_FREQUENCY_UNIT}",
    re.IGNORECASE,
)
# An age, not a period: "3 до 7-годишни деца", "6-месечни бебета".
# The hyphen is required: "€ 20\nмесечна такса" is an amount and its period.
_AGE_RE = re.compile(r"\d+ ?- ?(?:годиш|месеч)\w*")
PERIOD_KEYWORDS: dict[str, re.Pattern[str]] = {
    # Stems, so "месечна", "месечен" and "ежемесечно" all count; not "тримесечна".
    "MONTHLY": re.compile(r"(?<!три)месеч|\bна месец|/\s*мес|per month|/\s*month|\bmonthly\b|a month\b"),
    "YEARLY": re.compile(r"(?<!полу)годиш|\bна година|/\s*год|per year|/\s*year|\bannual|\byearly\b|a year\b"),
    "ONE_TIME": re.compile(r"еднократ|one[- ]time|one[- ]off|\bonce\b"),
    "QUARTER": re.compile(r"тримесеч|quarterly|per quarter"),
    "SEMESTER": re.compile(r"семестър|семестриал|полугодиш|per semester"),
    "TERM": re.compile(r"\bна срок\b|per term|termly"),
}
_UNREPRESENTABLE_RE = re.compile(
    r"седмичн|\bна седмица|/\s*седм|\bweekly\b|per week|/\s*week|a week\b"
    r"|(?<![а-я])дневн|\bна ден\b|/\s*ден\b|\bdaily\b|per day|/\s*day\b|a day\b"
    r"|\bна час\b|/\s*час\b|почасов|per hour|/\s*hour|hourly"
)
_DEPOSIT_RE = re.compile(r"депозит|\bdeposit")
# One installment of a fee ("I вноска", "1-ва вноска", "first installment"), not the fee
# itself. A bare number is not an ordinal: "за 1 вноска" is the fee paid in one go.
_INSTALLMENT_RE = re.compile(
    r"\b(?:[ivx]+|\d+\s*-?(?:ва|ра|ма|та)|първа|втора|трета)\s+вноска\b"
    r"|\b(?:first|second|third|\d+(?:st|nd|rd|th))\s+installment\b"
)
# Nursery wording the shared-site vocabulary leaves out ("ясла" alone).
_NURSERY_RE = re.compile(r"\bясл[аи]\b|яслен")


def period_families(context: str) -> tuple[set[str], bool]:
    """(representable period families named, whether an unrepresentable one is named)."""
    cleaned = _AGE_RE.sub(" ", _FREQUENCY_RE.sub(" ", context))
    families = {period for period, pattern in PERIOD_KEYWORDS.items() if pattern.search(cleaned)}
    return families, bool(_UNREPRESENTABLE_RE.search(cleaned))


def stated_period(text: str, span: tuple[int, int]) -> tuple[set[str], bool]:
    """Period wording for one amount: its own line segment first, else its context.

    "181,44 euro/ month" settles the period even when the next line is a table header
    naming both "annual fee" and "monthly fee".
    """
    families, unrepresentable = period_families(amount_segment(text, span))
    if families or unrepresentable:
        return families, unrepresentable
    return period_families(amount_context(text, span))


# An international school's Pre-K is its own reception class, not a kindergarten.
_PRE_K_RE = re.compile(r"pre[- ]?(?:kindergarten|k\b)")


def names_other_level(text: str, school_family: str) -> bool:
    lowered = (text or "").casefold()
    if school_family == "kindergarten":
        return describes_level(lowered, "school")
    lowered = _PRE_K_RE.sub(" ", lowered)
    return describes_level(lowered, "kindergarten") or bool(_NURSERY_RE.search(lowered))


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Hit:
    school_id: int
    row_id: Optional[int]
    rule: str
    detail: str
    snippet: str


@dataclass(frozen=True)
class PriceRow:
    id: int
    school_id: int
    category: str
    amount: Any
    amount_min: Any
    amount_max: Any
    period: Optional[str]
    plan_name: Optional[str]
    notes: Optional[str]

    @property
    def label(self) -> Optional[str]:
        return (self.plan_name or self.notes or "").strip() or None

    @property
    def amounts(self) -> list[Any]:
        if self.amount is not None:
            return [self.amount]
        return [v for v in (self.amount_min, self.amount_max) if v is not None]


def _enum(value: Any) -> Optional[str]:
    value = getattr(value, "value", value)
    return str(value).upper() if value is not None else None


def audit_price_row(row: PriceRow, page_text: str | None, school_family: str) -> list[Hit]:
    """Rule 1-4 hits for one published price row against its source page text."""
    text = normalize_text(page_text)
    hits: list[Hit] = []

    def hit(rule: str, detail: str, snippet: str) -> None:
        hits.append(Hit(row.school_id, row.id, rule, detail, snippet))

    if not text:
        hit("1_amount_near_label", "no stored page text", "")
        return hits

    spans = [span for value in row.amounts for span in amount_spans(text, value)]
    labels = label_spans(text, row.label)
    if not spans:
        hit("1_amount_near_label", f"amount {row.amounts} not on the page", text[:160])
        return hits

    # The amount occurrence this row is about: the one nearest its label, when found.
    # In a markdown table the label is a column header, so the check says nothing there.
    chosen = spans
    if labels:
        prices = price_spans(text)
        best = min(
            (
                ("|" not in _line_of(text, s) and not label_is_near(text, prices, s, l), _gap(s, l), s, l)
                for s in spans
                for l in labels
            ),
            key=lambda t: (t[0], t[1]),
        )
        if best[0]:
            hit(
                "1_amount_near_label",
                f"label {row.label!r} is separated from the amount by other prices",
                _snippet(text, best[3]),
            )
        chosen = [best[2]]
    contexts = [amount_context(text, span) for span in chosen]
    shown = _snippet(text, chosen[0])

    # Rule 2: the row's own label, or every occurrence's label lines, name the other level.
    other_family = "school" if school_family == "kindergarten" else "kindergarten"
    if row.label and names_other_level(row.label, school_family):
        hit("2_level_fits_school", f"label names {other_family}: {row.label!r}", shown)
    elif not row.label and all(names_other_level(context, school_family) for context in contexts):
        hit("2_level_fits_school", f"amount's label lines name {other_family}", contexts[0])

    # Rule 3: period. Needs agreement across the occurrences considered.
    found = [stated_period(text, span) for span in chosen]
    if all(unrepresentable for _, unrepresentable in found):
        hit("3_period_unrepresentable", "per day/week/hour next to the amount", contexts[0])
    else:
        families = [fams for fams, _ in found]
        single = families[0] if all(f == families[0] for f in families) and len(families[0]) == 1 else None
        stored = _enum(row.period)
        if single and stored is None:
            hit("3_period_missing", f"null period, text says {next(iter(single))}", contexts[0])
        elif single and stored and stored not in single and {stored} | single != {"ONE_TIME", "YEARLY"}:
            hit("3_period_conflict", f"stored {stored}, text says {next(iter(single))}", contexts[0])

    # Rule 4: a deposit filed as tuition.
    if _enum(row.category) == "TUITION":
        label_text = (row.label or "").casefold()
        if _DEPOSIT_RE.search(label_text) or all(_DEPOSIT_RE.search(c) for c in contexts):
            hit("4_deposit_not_tuition", "tuition row labelled as a deposit", contexts[0])
        # Same family: one installment published as the whole fee. Only the amount's own
        # line counts; a monthly row is the monthly installment and may stand.
        elif _enum(row.period) != "MONTHLY" and all(
            _INSTALLMENT_RE.search(_line_of(text, span)) for span in chosen
        ):
            hit("4_installment_not_tuition", "tuition row is one installment", _line_of(text, chosen[0]))
    return hits


def _name_key(name: Any) -> str:
    return re.sub(r"[^\w]+", " ", str(name or "").casefold()).strip()


@dataclass(frozen=True)
class PublishedName:
    school_id: int
    website_url: Optional[str]
    names: tuple[str, ...]


def audit_names(schools: Iterable[PublishedName]) -> list[Hit]:
    """Rule 5: a published name equal to another institution's in the same site group."""
    groups: dict[str, list[PublishedName]] = defaultdict(list)
    for school in schools:
        key = site_group_key(school.website_url)
        if key:
            groups[key].append(school)
    hits = []
    for key, members in groups.items():
        for school in members:
            own = {_name_key(n) for n in school.names if _name_key(n)}
            for other in members:
                if other.school_id == school.school_id:
                    continue
                shared = own & {_name_key(n) for n in other.names}
                if shared:
                    hits.append(Hit(
                        school.school_id, None, "5_name_not_sibling",
                        f"name {sorted(shared)[0]!r} also published by school {other.school_id}",
                        f"site group {key}",
                    ))
    return hits


# ---------------------------------------------------------------------------
# Database (read-only)
# ---------------------------------------------------------------------------

async def _collect() -> tuple[list[Hit], dict[str, Any]]:
    from sqlalchemy import or_, select

    from app.database import engine
    from app.models import School, SourcePage
    from app.models.pricing import Pricing
    from app.schemas.school import SchoolDetailResponse
    from app.services.school_service import SchoolService
    from sqlalchemy.ext.asyncio import AsyncSession

    hits: list[Hit] = []
    stats: Counter = Counter()
    published_names: list[PublishedName] = []
    pending: list[tuple[PriceRow, Optional[int], str]] = []
    async with engine.connect() as conn:
        trans = await conn.begin()
        try:
            db = AsyncSession(bind=conn, expire_on_commit=False)
            priced = select(Pricing.school_id).distinct()
            ids = (
                await db.execute(
                    select(School.id)
                    .where(or_(School.id.in_(priced), School.website_url.isnot(None)))
                    .order_by(School.id)
                )
            ).scalars().all()
            service = SchoolService(db)
            for school_id in ids:
                school = await service.get_school_with_details(school_id)
                if school is None:
                    continue
                response = SchoolDetailResponse.model_validate(school)
                published_names.append(PublishedName(
                    school.id, school.website_url, tuple(response.resolved_name_i18n.values())
                ))
                if not response.pricing:
                    continue
                stats["schools_with_published_prices"] += 1
                family = level_family(school.education_level)
                orm_rows = {row.id: row for row in school.pricing}
                for published in response.pricing:
                    stats["published_rows"] += 1
                    orm = orm_rows[published.id]
                    context = published.pricing_context
                    row = PriceRow(
                        id=published.id,
                        school_id=school.id,
                        category=_enum(published.category),
                        amount=published.amount,
                        amount_min=published.amount_min,
                        amount_max=published.amount_max,
                        period=_enum(published.period),
                        plan_name=published.plan_name,
                        notes=context.notes if context else None,
                    )
                    pending.append((row, orm.source_page_id, family))
                db.expunge_all()
            page_ids = {page_id for _, page_id, _ in pending if page_id is not None}
            pages = dict(
                (
                    await db.execute(
                        select(SourcePage.id, SourcePage.raw_markdown).where(SourcePage.id.in_(page_ids))
                    )
                ).all()
            )
        finally:
            await trans.rollback()
    await engine.dispose()
    for row, page_id, family in pending:
        page_text = pages.get(page_id)
        row_hits = audit_price_row(row, page_text, family)
        if row_hits:
            stats["rows_with_hits"] += 1
        if row.label and not label_spans(normalize_text(page_text), row.label):
            stats["rows_label_not_on_page"] += 1
        hits.extend(row_hits)
    stats["schools_audited"] = len(published_names)
    hits.extend(audit_names(published_names))
    return hits, dict(stats)


def _write(hits: list[Hit], stats: dict[str, Any]) -> Path:
    out = REPORT_ROOT / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out.mkdir(parents=True, exist_ok=True)
    with (out / "hits.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["school_id", "row_id", "rule", "detail", "evidence_snippet"])
        for h in sorted(hits, key=lambda h: (h.rule, h.school_id, h.row_id or 0)):
            writer.writerow([h.school_id, h.row_id or "", h.rule, h.detail, h.snippet])
    per_rule = Counter(h.rule for h in hits)
    lines = [
        f"# UF45 audit {out.name}",
        "",
        f"- schools audited (published projection): {stats.get('schools_audited', 0)}",
        f"- schools with published prices: {stats.get('schools_with_published_prices', 0)}",
        f"- published price rows: {stats.get('published_rows', 0)}",
        f"- rows with at least one hit: {stats.get('rows_with_hits', 0)}",
        f"- rows whose label is not on the page (rule 1 distance not checked): "
        f"{stats.get('rows_label_not_on_page', 0)}",
        "",
        "| rule | hits | schools |",
        "|---|---|---|",
    ]
    for rule in sorted(per_rule):
        schools = len({h.school_id for h in hits if h.rule == rule})
        lines.append(f"| {rule} | {per_rule[rule]} | {schools} |")
    (out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def main() -> None:
    hits, stats = asyncio.run(_collect())
    out = _write(hits, stats)
    print((out / "summary.md").read_text(encoding="utf-8"))
    print(f"report: {out}")


if __name__ == "__main__":
    main()
