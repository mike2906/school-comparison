"""The fee agent's reader schema, prompt and the code checks on what it returns (R6).

``docs/AGENT_FEE_EXTRACTION.md`` is the plan. The model reads a school's fee pages and
copies each fee together with the page line it came from; code then decides what a fee
may publish with. Pure functions: no database, no network.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Optional

from pydantic import BaseModel, Field

from app.models.pricing import PriceCategory, PricePeriod

PUBLISH_CONFIDENCE = 0.9
HELD_CONFIDENCE = 0.5  # under the 0.7 publish floor (display_gating.PRICING_CONFIDENCE_FLOOR)


class AgentFee(BaseModel):
    category: PriceCategory
    amount: Optional[float] = None
    amount_min: Optional[float] = None
    amount_max: Optional[float] = None
    currency: str = Field(description="EUR or BGN, as printed; the EUR figure when both are printed")
    period: Optional[PricePeriod] = Field(description="null unless the evidence line states it")
    plan_name: Optional[str] = Field(description="Payment plan or option, as the site names it")
    academic_year: Optional[str] = Field(description="As the site states it, e.g. 2026/2027")
    age_group: Optional[str] = Field(description="Grade or age group, as the site states it")
    label: str = Field(description="The site's own words for this fee, short")
    notes: Optional[str] = None
    installments: list[str] = Field(default_factory=list)
    source_url: str = Field(description="The URL of the page the fee was read from, copied from its PAGE line")
    evidence: str = Field(
        description="The page line the amount was read from, copied verbatim. In a table, the row "
        "and, on a line of its own, the heading that gives its period or plan."
    )


class AgentFees(BaseModel):
    fees: list[AgentFee] = Field(default_factory=list)


READER_PROMPT = """You read a school's own web pages and list the fees it states. Each page starts with a "=== PAGE <url>" line.
Rules:
- Only fees of the named institution. A shared site may also list a sibling school or kindergarten; skip their fees.
- Only the current or upcoming school year. If several appear, list the most recent.
- One entry per amount the site prints. Each printed instalment is its own fee, with the plan name. A discounted or early-payment price is its own entry, with a note.
- Skip lines without an amount (a percentage discount, a payment schedule). Never invent an amount, and never compute a total the site does not print.
- currency as printed (EUR or BGN); if both are printed, the EUR figure.
- category: the monthly kindergarten fee and school tuition are tuition; an enrolment, exam, application or entry-test fee is registration; a deposit (депозит, капаро) is registration, never tuition; learning support, therapy and similar services are activities; optional classes, clubs and sports charged on top are extracurricular.
- period only when the page states it for THAT amount (per month, per year, per term...); otherwise null. Never guess.
  - "once per school year" or "еднократна такса" for the school year is yearly; a full year's tuition paid in one payment is yearly too.
  - one_time only for a fee charged once ever: enrolment, exam, application, deposit. Never for tuition or materials.
  - A count of payments ("2 x", "на две вноски") states no period: null.
  - In "1 x A + N x B", A is a first instalment with no period; B has the period the plan states (8 monthly instalments: monthly).
  - A yearly total only if the site prints it.
- Never build a range from two different fees (a full-day and a half-day price are two entries). A range only when the site prints one.
- source_url: the URL of the page the fee is on.
- evidence: the line of the page the amount is on, copied character for character. For a table row, also copy the heading line that gives its period, plan or group, as a separate line. If the period is stated in a sentence above the table, copy that sentence too.
- label: the site's own wording, short. academic_year, age_group and plan_name as the site states them, or null."""


# ---------------------------------------------------------------------------
# Numbers on a page
# ---------------------------------------------------------------------------

_NUMBER_RE = re.compile(r"\d[\d  .,]*\d|\d")
_DATE_RE = re.compile(r"\d{1,2}[./]\d{1,2}[./]\d{2,4}")


def _values(token: str) -> set[Decimal]:
    """Every value a printed number can mean: '7 400', '1.200,00', '475,50', '7.400'."""
    token = token.replace(" ", "").replace(" ", "").strip(".,")
    digits = re.sub(r"\D", "", token)
    if not digits or len(digits) > 9 or _DATE_RE.fullmatch(token):  # phone numbers, IDs, dates
        return set()
    candidates = {token.replace(",", "").replace(".", "")}
    for decimal_mark in ".,":
        if decimal_mark in token:
            whole, _, frac = token.rpartition(decimal_mark)
            candidates.add(re.sub(r"\D", "", whole) + "." + frac)
    values = set()
    for candidate in candidates:
        try:
            values.add(Decimal(candidate).normalize())
        except InvalidOperation:
            pass
    return values


def number_spans(text: str) -> list[tuple[int, int, set[Decimal]]]:
    """(start, end, values) of each printed number, with the parts of a space-joined run
    ('580 600' in a table row) as values too."""
    spans = []
    for match in _NUMBER_RE.finditer(text):
        values = _values(match.group())
        for part in re.split(r"[  ]+", match.group()):
            values |= _values(part)
        if values:
            spans.append((match.start(), match.end(), values))
    return spans


def page_numbers(text: str) -> set[Decimal]:
    return {value for _, _, values in number_spans(text) for value in values}


def _amounts(fee: AgentFee) -> list[Decimal]:
    keys = (fee.amount,) if fee.amount is not None else (fee.amount_min, fee.amount_max)
    return [Decimal(str(key)).normalize() for key in keys if key is not None]


def amount_on_page(fee: AgentFee, numbers: set[Decimal]) -> bool:
    amounts = _amounts(fee)
    return bool(amounts) and all(amount in numbers for amount in amounts)


# ---------------------------------------------------------------------------
# What the evidence line names
# ---------------------------------------------------------------------------

_PERIOD_WORDS = {
    PricePeriod.MONTHLY: r"(?<!три)месеч|(?:на|за|/)\s*месец|\bmonth(?:ly)?\b",
    PricePeriod.YEARLY: r"(?<!полу)(?<!дву)годиш|\b(?:year(?:ly)?|annual)\b",
    PricePeriod.QUARTER: r"тримесеч|\bquarter",
    PricePeriod.TERM: r"срок|\bterm\b",
    PricePeriod.SEMESTER: r"семест|\bsemester",
    PricePeriod.ONE_TIME: r"еднократ|\bonce\b|при записване",
}
# "for the school year" names a period only on the amount's own line: as a heading it often
# covers fees charged per day or per month too (569's "ДОПЪЛНИТЕЛНИ ТАКСИ ЗА УЧЕБНАТА ГОДИНА").
_YEAR_ON_AMOUNT_LINE_RE = re.compile(r"(?:учебн\w*|на|за)\s+година")
_DEPOSIT_RE = re.compile(r"депозит|капаро|deposit")
_RANGE_MARKER_RE = re.compile(r"[–—-]|\bдо\b|\bto\b")
_FIRST_INSTALMENT_BEFORE_RE = re.compile(r"(?<!\d)1\s*[xх×]\s*$")  # latin x, cyrillic х, ×
_PLUS_N_TIMES_AFTER_RE = re.compile(r"\s*(?:eur|€|евро|лв\.?|лева|bgn)?\s*\+\s*\d+\s*[xх×]")
_RANGE_GAP_CHARS = 30


def _normalized(text: str) -> str:
    return " ".join(text.split()).casefold()


def _names_period(period: PricePeriod, evidence: str, amounts: list[Decimal]) -> bool:
    if re.search(_PERIOD_WORDS[period], evidence):
        return True
    if period is not PricePeriod.YEARLY:
        return False
    amount_lines = [line for line in evidence.splitlines() if set(amounts) & page_numbers(line)]
    return any(_YEAR_ON_AMOUNT_LINE_RE.search(line) for line in amount_lines)


def _is_first_instalment(evidence: str, amount: Decimal) -> bool:
    """True when the line prints the amount as 'A' of '1 x A + N x B'."""
    for start, end, values in number_spans(evidence):
        if amount in values and _FIRST_INSTALMENT_BEFORE_RE.search(evidence[:start]):
            if _PLUS_N_TIMES_AFTER_RE.match(evidence[end:]):
                return True
    return False


def _range_is_joined(evidence: str, low: Decimal, high: Decimal) -> bool:
    """True when the line writes the range's two ends next to each other with a range marker."""
    spans = number_spans(evidence)
    for first in (s for s in spans if low in s[2]):
        for second in (s for s in spans if high in s[2]):
            a, b = sorted((first, second), key=lambda span: span[0])
            between = evidence[a[1] : b[0]].strip()
            if a[0] != b[0] and len(between) <= _RANGE_GAP_CHARS and _RANGE_MARKER_RE.search(between):
                return True
    return False


# ---------------------------------------------------------------------------
# The checks
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CheckedFee:
    fee: AgentFee  # the period may be cleared
    confidence: float
    kept: bool  # False: the fee is dropped, nothing is stored
    checks: list[str] = field(default_factory=list)  # what was cleared or held, and why


def _dropped(fee: AgentFee, reason: str) -> CheckedFee:
    return CheckedFee(fee, 0.0, False, [reason])


def check_fee(fee: AgentFee, page_text: str) -> CheckedFee:
    """Apply the plan's rules (step 3 and 4 of "How it works") to one fee read from one page."""
    page = _normalized(page_text)
    lines = [_normalized(line) for line in fee.evidence.splitlines() if line.strip()]
    if not lines:
        return _dropped(fee, "dropped: no evidence line")
    if any(line not in page for line in lines):
        return _dropped(fee, "dropped: evidence line is not on the page")
    evidence = "\n".join(lines)
    if not amount_on_page(fee, page_numbers(evidence)):
        return _dropped(fee, "dropped: evidence line does not hold the amount")
    if not amount_on_page(fee, page_numbers(page)):
        return _dropped(fee, "dropped: amount is not among the page's numbers")

    checks: list[str] = []
    period = fee.period
    if period is not None and not _names_period(period, evidence, _amounts(fee)):
        checks.append(f"period {period.value} cleared: the evidence does not name it")
        period = None
    if period is PricePeriod.ONE_TIME and fee.category is not PriceCategory.REGISTRATION:
        checks.append(f"one_time cleared: not valid for {fee.category.value}")
        period = None
    if period is not None and fee.amount is not None and _is_first_instalment(evidence, _amounts(fee)[0]):
        checks.append(f"period {period.value} cleared: first instalment of '1 x A + N x B'")
        period = None

    held = []
    if fee.category is not PriceCategory.REGISTRATION and _DEPOSIT_RE.search(f"{evidence} {_normalized(fee.label)}"):
        held.append(f"held: names a deposit but is filed as {fee.category.value}")
    amounts = _amounts(fee)
    if fee.amount is None and len(amounts) == 2 and amounts[0] != amounts[1]:
        if not _range_is_joined(evidence, min(amounts), max(amounts)):
            held.append("held: range ends are not joined by a range marker in the evidence")

    confidence = HELD_CONFIDENCE if held else PUBLISH_CONFIDENCE
    return CheckedFee(fee.model_copy(update={"period": period}), confidence, True, checks + held)
