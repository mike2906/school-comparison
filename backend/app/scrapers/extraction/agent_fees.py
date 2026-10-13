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


class PaymentOption(BaseModel):
    """Another way to pay one fee: in parts, as the site prints them (596's "2 x € 4.575")."""

    label: Optional[str] = Field(default=None, description="The site's name for this way of paying, e.g. Plan 2")
    total: Optional[float] = Field(default=None, description="The total paid this way, only if the site prints it")
    payments: Optional[int] = Field(
        default=None, description="How many equal instalments: 9 in '1 x 2650 + 9 x 750', 2 in '2 x 4575'"
    )
    instalment: Optional[float] = Field(default=None, description="The amount of each of those instalments")
    first_payment: Optional[float] = Field(
        default=None, description="A first payment before the equal instalments: 2650 in '1 x 2650 + 9 x 750'"
    )


class AgentFee(BaseModel):
    category: PriceCategory
    amount: Optional[float] = None
    amount_min: Optional[float] = None
    amount_max: Optional[float] = None
    currency: str = Field(description="EUR or BGN, as printed; the EUR figure when both are printed")
    period: Optional[PricePeriod] = Field(description="null unless the evidence line states it")
    plan_name: Optional[str] = Field(
        description="What the fee buys (full day, half day), as the site names it; never a payment plan"
    )
    academic_year: Optional[str] = Field(description="As the site states it, e.g. 2026/2027")
    age_group: Optional[str] = Field(description="Grade or age group, as the site states it")
    label: str = Field(description="The site's own words for this fee, short")
    notes: Optional[str] = None
    installments: list[str] = Field(default_factory=list)
    payment_options: list[PaymentOption] = Field(
        default_factory=list, description="Other ways the site offers to pay this same fee, in parts"
    )
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
- One entry per fee and group (grade, age group, full or half day). A discounted or early-payment price is its own entry, with a note.
- Paying one fee in parts is not a fee of its own. When the site offers the same fee paid in one go and also in parts (2 instalments, quarterly or monthly instalments, "Plan 1 / Plan 2 / Plan 3", "1 x A + 9 x B"), list one entry: amount is the price paid in one go, and each way of paying in parts goes in payment_options with the numbers the site prints (total, payments, instalment, first_payment). Paying in one go is the entry itself, never an option.
- If the site prints no price for paying in one go, the entry's amount is the total it prints for one plan; with no total printed, list each instalment amount as its own entry with the plan name.
- A monthly fee that is the school's ordinary price (a kindergarten's monthly fee) is the entry itself, not an option.
- List the fees of every page, to the end of the last one: a school often has a fee page per stage (kindergarten, primary, secondary), and each one's fees are listed. Include services billed separately (uniforms, transport, food, extended day, clubs), not only tuition.
- Skip lines without an amount (a percentage discount, a payment schedule). Never invent an amount, and never compute a total the site does not print.
- currency as printed (EUR or BGN); if both are printed, the EUR figure.
- category: the monthly kindergarten fee and school tuition are tuition; an enrolment, exam, application or entry-test fee is registration; a deposit (депозит, капаро) is registration, never tuition; learning support, therapy and similar services are activities; optional classes, clubs and sports charged on top are extracurricular; transport, food, uniforms and extended day (after-school care) are their own categories.
- period only when the page states it for THAT amount (per month, per year, per term...); otherwise null. Never guess.
  - "once per school year" or "еднократна такса" for the school year is yearly; a full year's tuition paid in one payment is yearly too.
  - one_time only for a fee charged once ever: enrolment, exam, application, deposit. Never for tuition or materials.
  - A count of payments ("2 x", "на две вноски") states no period: null.
  - In "1 x A + N x B", A is a first instalment with no period; B has the period the plan states (8 monthly instalments: monthly).
  - A yearly total only if the site prints it.
- Never build a range from two different fees (a full-day and a half-day price are two entries). A range only when the site prints one.
- source_url: the URL of the page the fee is on.
- evidence: the line of the page the amount is on, copied character for character, and the lines of its payment options. For a table row, also copy the heading line that gives its period, plan or group, as a separate line. If the period is stated in a sentence above the table, copy that sentence too.
- label: the site's own wording, short. academic_year, age_group and plan_name as the site states them, or null. plan_name names what the fee buys (full day, half day), not a way of paying: a payment plan's name is its option's label."""


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


def fee_amounts(fee: AgentFee) -> list[Decimal]:
    keys = (fee.amount,) if fee.amount is not None else (fee.amount_min, fee.amount_max)
    return [Decimal(str(key)).normalize() for key in keys if key is not None]


def amount_on_page(fee: AgentFee, numbers: set[Decimal]) -> bool:
    amounts = fee_amounts(fee)
    return bool(amounts) and all(amount in numbers for amount in amounts)


# ---------------------------------------------------------------------------
# What the evidence line names
# ---------------------------------------------------------------------------

_PERIOD_WORDS = {  # Bulgarian, English, and the French and German some schools publish in
    PricePeriod.MONTHLY: r"(?<!три)месеч|(?:на|за|/)\s*месец|\bmonth(?:ly)?\b|mensuel|par mois|monatlich|pro monat",
    PricePeriod.YEARLY: (
        # "За цялата учебна година" (300) and a "(l'année)" column (505) name the year for
        # what they head; a bare "for the school year" heading does not (_YEAR_ON_AMOUNT_LINE_RE).
        r"(?<!полу)(?<!дву)годиш|\b(?:year(?:ly)?|annual)\b|цялата учебна година"
        r"|annuel|\(l'année\)|par an\b|(?<![a-zäöü])jährlich|pro jahr"
    ),
    PricePeriod.QUARTER: r"тримесеч|\bquarter|vierteljährlich",
    PricePeriod.TERM: r"срок|\bterm\b",
    PricePeriod.SEMESTER: r"семест|\bsemester|semestre|halbj[aä]hr",
    PricePeriod.ONE_TIME: r"еднократ|\bonce\b|при записване",
}
# English "one-time" names a period only on the amount's own line: 596's "ANNUAL TUITION FEES"
# heading sits above its "One-time fee for initial enrollment".
_ONE_TIME_EN_RE = re.compile(r"\bone\s*[-–—]?\s*time\b")
# "for the school year" names a period only on the amount's own line: as a heading it often
# covers fees charged per day or per month too (569's "ДОПЪЛНИТЕЛНИ ТАКСИ ЗА УЧЕБНАТА ГОДИНА").
_YEAR_ON_AMOUNT_LINE_RE = re.compile(r"(?:учебн\w*|на|за)\s+година|l'année")
_DEPOSIT_RE = re.compile(r"депозит|капаро|deposit")
# Only a currency and the marker may sit between a range's ends: "550 – 600", "550 EUR to 600".
_RANGE_JOIN_RE = re.compile(r"(?:eur|€|евро|лв\.?|лева|bgn)?\s*(?:[–—-]|до|to)", re.IGNORECASE)
_FIRST_INSTALMENT_BEFORE_RE = re.compile(r"(?<!\d)1\s*[xх×]\s*$")  # latin x, cyrillic х, ×
_PLUS_N_TIMES_AFTER_RE = re.compile(r"\s*(?:eur|€|евро|лв\.?|лева|bgn)?\s*\+\s*\d+\s*[xх×]")


def _normalized(text: str) -> str:
    return " ".join(text.split()).casefold()


def _names_period(period: PricePeriod, evidence: str, amounts: list[Decimal], category: PriceCategory) -> bool:
    # An enrolment, exam or application fee is charged once by what it is (rules.md), so it
    # needs no word for it; a deposit filed as registration is one too. Not when the line
    # calls it recurring and never one-time: "годишна такса за записване" is paid every year.
    # "срок" is left out: "платима в срок до 15 юни" is a deadline, not a term.
    amount_lines = [line for line in evidence.splitlines() if set(amounts) & page_numbers(line)]
    if period is PricePeriod.ONE_TIME and category is PriceCategory.REGISTRATION:
        if re.search(_PERIOD_WORDS[PricePeriod.ONE_TIME], evidence):
            return True
        recurring = (PricePeriod.MONTHLY, PricePeriod.YEARLY, PricePeriod.QUARTER, PricePeriod.SEMESTER)
        # "One-time fee of € 800" under an "ANNUAL TUITION FEES" heading (596): the amount's own
        # line names it, unless that line also calls the fee recurring ("per term" included).
        if any(
            _ONE_TIME_EN_RE.search(line)
            and not any(re.search(_PERIOD_WORDS[other], line) for other in (*recurring, PricePeriod.TERM))
            for line in amount_lines
        ):
            return True
        return not any(re.search(_PERIOD_WORDS[other], evidence) for other in recurring)
    if re.search(_PERIOD_WORDS[period], evidence):
        return True
    if period is not PricePeriod.YEARLY:
        return False
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
            if a[0] != b[0] and _RANGE_JOIN_RE.fullmatch(between):
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
        page_lines = [_normalized(text) for text in page_text.splitlines() if text.strip()]
        if any(not _line_on_page(line, page, page_lines) for line in lines):
            return _dropped(fee, "dropped: evidence line is not on the page")
    evidence = "\n".join(lines)
    if not amount_on_page(fee, page_numbers(evidence)):
        return _dropped(fee, "dropped: evidence line does not hold the amount")
    if not amount_on_page(fee, page_numbers(page)):
        return _dropped(fee, "dropped: amount is not among the page's numbers")

    # The period and deposit rules read the fee's own lines: "9 x € 750 monthly" names the
    # period of a payment option, not of the price paid in one go above it.
    evidence = _without_option_lines(lines, fee)
    checks: list[str] = []
    period = fee.period
    if period is not None and not _names_period(period, evidence, fee_amounts(fee), fee.category):
        checks.append(f"period {period.value} cleared: the evidence does not name it")
        period = None
    if period is PricePeriod.ONE_TIME and fee.category is not PriceCategory.REGISTRATION:
        checks.append(f"one_time cleared: not valid for {fee.category.value}")
        period = None
    if period is not None and fee.amount is not None and _is_first_instalment(evidence, fee_amounts(fee)[0]):
        checks.append(f"period {period.value} cleared: first instalment of '1 x A + N x B'")
        period = None

    held = []
    if fee.category is not PriceCategory.REGISTRATION and _DEPOSIT_RE.search(f"{evidence} {_normalized(fee.label)}"):
        held.append(f"held: names a deposit but is filed as {fee.category.value}")
    amounts = fee_amounts(fee)
    if fee.amount is None and len(amounts) == 2 and amounts[0] != amounts[1]:
        if not _range_is_joined(evidence, min(amounts), max(amounts)):
            held.append("held: range ends are not joined by a range marker in the evidence")

    options, option_checks = _checked_options(fee, "\n".join(lines))
    confidence = HELD_CONFIDENCE if held else PUBLISH_CONFIDENCE
    update = {"period": period, "payment_options": options}
    return CheckedFee(fee.model_copy(update=update), confidence, True, checks + option_checks + held)


def _without_option_lines(lines: list[str], fee: AgentFee) -> str:
    """The evidence without the lines, or table cells, that hold only a payment option's
    numbers: in "| € 8.950 | 9 x € 750 monthly |" the word monthly is the option's."""
    own, options = set(fee_amounts(fee)), set(option_amounts(fee))

    def option_only(text: str) -> bool:
        numbers = page_numbers(text)
        return bool(numbers & options) and not numbers & own

    kept = []
    for line in lines:
        cells = [cell for cell in line.split("|") if not option_only(cell)]
        if "|".join(cells).strip(" |"):
            kept.append("|".join(cells))
    return "\n".join(kept)


# A count of instalments as the page writes it: "2 x", "9 ×", "10 вноски", "3 installments".
# One word may sit between: "10 месечни вноски", "3 equal instalments"; or "€ 4.575 x 2".
_COUNT_WORD_RE = r"(?:[xх×](?![^\W\d_])|вноск|instal|payments|плащания|versements|rates)"


def _count_is_printed(payments: int, text: str) -> bool:
    count = rf"(?<![\d.,]){payments}(?![\d.,]\d)"
    after = rf"{count}\s*(?:[^\s\d]+\s+)?{_COUNT_WORD_RE}"
    before = rf"(?<![^\W\d_])[xх×]\s*{count}"
    return re.search(rf"{after}|{before}", text, re.IGNORECASE) is not None


_LABEL_LINES_AWAY = 3
# A label names the row; it holds no price or period of its own, so it cannot carry another
# row's amount or "monthly" into the fee's line.
_NOT_A_LABEL_RE = re.compile(rf"[€$£]|\b(?:eur|bgn|лв|лева|евро)\b|{'|'.join(_PERIOD_WORDS.values())}", re.IGNORECASE)


def _line_on_page(line: str, page: str, page_lines: list[str]) -> bool:
    """The evidence line is on the page, or is a row's label joined to the row.

    A PDF table's text can put a row's label on another line than its first amount: 596's
    secondary fees read "Plan 1 – € 9,500" on one line and "Grade 8 – 9 Plan 2 – …" two
    lines down, and the reader quoted the row as "Grade 8 – 9 Plan 1 – € 9,500". The row
    must be a whole line of the page and the label the start of a line at most three lines
    from it, with no price or period word in it, so a label and an amount from unrelated
    places do not make a line.
    """
    if line in page:
        return True
    where = {}
    for index, text in enumerate(page_lines):
        where.setdefault(text, []).append(index)
    for cut in range(1, len(line)):
        if line[cut - 1] != " ":
            continue
        label, row = line[:cut].strip(), line[cut:].strip()
        if not label or row not in where or _NOT_A_LABEL_RE.search(label):
            continue
        for index in where[row]:
            nearby = page_lines[max(0, index - _LABEL_LINES_AWAY) : index + _LABEL_LINES_AWAY + 1]
            if any(text != row and text.startswith(label) for text in nearby):
                return True
    return False


def _decimal(value: float) -> Decimal:
    return Decimal(str(value)).normalize()


def _checked_options(fee: AgentFee, evidence: str) -> tuple[list[PaymentOption], list[str]]:
    """The fee's payment options whose numbers the evidence prints.

    An option with a number the evidence does not hold is dropped; a count of payments the
    evidence does not write as one ("2 x", "10 вноски"; not the 2 of "Grade 2") is cleared. What is left must say what is paid: a total,
    or a count and an instalment. One payment of the whole fee is the fee itself.
    """
    numbers = page_numbers(evidence)
    kept: list[PaymentOption] = []
    checks: list[str] = []
    for option in fee.payment_options:
        printed = [value for value in (option.total, option.instalment, option.first_payment) if value is not None]
        missing = [value for value in printed if _decimal(value) not in numbers]
        if missing:
            checks.append(f"payment option dropped: {missing[0]:g} is not in the evidence")
            continue
        payments = option.payments
        if payments is not None and (payments < 1 or not _count_is_printed(payments, evidence)):
            payments = None
        if payments == 1 and option.first_payment is None:  # "1 x € 8.950": the fee itself
            if option.instalment is not None and fee.amount is not None and option.instalment != fee.amount:
                checks.append(f"payment option dropped: one payment of {option.instalment:g} is not the fee's price")
            continue
        if option.total is None and (payments is None or option.instalment is None):
            checks.append("payment option dropped: it states neither a total nor a count of instalments")
            continue
        if fee.amount is not None and option.total == fee.amount and payments is None and option.instalment is None:
            continue  # the fee's own price again
        kept.append(option.model_copy(update={"payments": payments}))
    return kept, checks


def read_amounts(fee: AgentFee) -> list[Decimal]:
    """The amounts a read gives for the fee: its own and its payment options'."""
    return [*fee_amounts(fee), *option_amounts(fee)]


def option_amounts(fee: AgentFee) -> list[Decimal]:
    """Every amount the fee's payment options print: read, though not stored as fees."""
    return [
        _decimal(value)
        for option in fee.payment_options
        for value in (option.total, option.instalment, option.first_payment)
        if value is not None
    ]
