"""The fee agent's reader schema, prompt and the code checks on what it returns (R6).

``docs/AGENT_FEE_EXTRACTION.md`` is the plan. The model reads a school's fee pages and
copies each fee together with the page line it came from; code then decides what a fee
may publish with. Pure functions: no database, no network.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

from app.models.pricing import PriceCategory, PricePeriod

PUBLISH_CONFIDENCE = 0.9
HELD_CONFIDENCE = 0.5  # under the 0.7 publish floor (display_gating.PRICING_CONFIDENCE_FLOOR)


# The reader sometimes copies the page's word for the currency: 158 printed "евро", and its
# fees failed the publish gate's three-letter code check.
_CURRENCY_WORDS = {"евро": "EUR", "euro": "EUR", "euros": "EUR", "€": "EUR", "лв": "BGN", "лв.": "BGN", "лева": "BGN"}


def _currency_code(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    text = value.strip()
    return _CURRENCY_WORDS.get(text.casefold(), text.upper())


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
        description="What the fee buys (full day, half day) and on what terms, as the site names it; "
        "never a way of paying in parts"
    )
    plan_name_en: Optional[str] = Field(
        default=None, description="plan_name in plain English for a parent (Half day); null when plan_name is null"
    )
    academic_year: Optional[str] = Field(description="As the site states it, e.g. 2026/2027")
    age_group: Optional[str] = Field(description="Grade or age group, as the site states it")
    age_group_en: Optional[str] = Field(
        default=None, description="age_group in plain English for a parent (Ages 3 to 6); null when age_group is null"
    )
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

    @field_validator("currency")
    @classmethod
    def _currency_code(cls, value: str) -> str:
        return _currency_code(value)


class DiscountText(BaseModel):
    # Two named fields: given a free-form dict, the reader left the text empty (596, 300).
    bg: str = Field(description="One short sentence in Bulgarian stating the discount with its number")
    en: str = Field(description="The same sentence in English, e.g. '5% off tuition for a second child'")


class AgentDiscount(BaseModel):
    """A discount the school offers on its fees, stated with a percentage or an amount."""

    kind: Literal["sibling", "early_payment", "pay_in_full", "other"]
    percent: Optional[float] = Field(default=None, description="The percentage off, if the site states one")
    amount: Optional[float] = Field(
        default=None, description="The amount off, if the site states one; never with percent"
    )
    currency: Optional[str] = Field(default=None, description="EUR or BGN, as printed; only with amount")
    child: Optional[int] = Field(
        default=None, description="For a sibling discount, which child it is for: 2 for the second, 3 for the third"
    )
    applies_to: Optional[str] = Field(
        default=None, description="Which fee it applies to, in the site's words (tuition), or null"
    )
    text_i18n: DiscountText = Field(
        description="The discount stated in bg and en with its number and nothing the evidence does not say"
    )
    source_url: str = Field(description="The URL of the page the discount was read from, copied from its PAGE line")
    evidence: str = Field(description="The page line the discount was read from, copied verbatim")

    @field_validator("currency")
    @classmethod
    def _currency_code(cls, value: Optional[str]) -> Optional[str]:
        return _currency_code(value)


class AgentFees(BaseModel):
    fees: list[AgentFee] = Field(default_factory=list)
    discounts: list[AgentDiscount] = Field(default_factory=list)


READER_PROMPT = """You read a school's own web pages and list the fees it states. Each page starts with a "=== PAGE <url>" line.
Rules:
- Only fees of the named institution. A shared site may also list a sibling school or kindergarten; skip their fees.
- Only the current or upcoming school year. If several appear, list the most recent.
- One entry per fee and group (grade, age group, full or half day). A discounted, prepaid or promotional price the site prints as an amount is its own entry, and its plan_name says on what terms, in the site's words ("Еднократно плащане за 6 месеца", "Промоционална такса за първите 3 месеца").
- Two entries of the same category, group and period never share a plan_name: the site's words that tell them apart (half day, full day, paid for 6 months at once) are their plan_name.
- Paying one fee in parts is not a fee of its own. When the site offers the same fee paid in one go and also in parts (2 instalments, quarterly or monthly instalments, "Plan 1 / Plan 2 / Plan 3", "1 x A + 9 x B"), list one entry: amount is the price paid in one go, and each way of paying in parts goes in payment_options with the numbers the site prints (total, payments, instalment, first_payment). Paying in one go is the entry itself, never an option.
- If the site prints no price for paying in one go, the entry's amount is the total it prints for one plan; with no total printed, list each instalment amount as its own entry with the plan name.
- A monthly fee that is the school's ordinary price (a kindergarten's monthly fee) is the entry itself, not an option.
- List the fees of every page, to the end of the last one: a school often has a fee page per stage (kindergarten, primary, secondary), and each one's fees are listed. Include services billed separately (uniforms, transport, food, extended day, clubs), not only tuition.
- Skip a payment schedule. Never invent an amount, and never compute a total the site does not print.
- A discount the school offers (for siblings, for paying early, for paying the whole year at once, any other stated discount) goes in discounts, never in fees, and only when the page states it with a percentage or an amount. One entry per discount ("5% for a second child, 10% for a third" is two). text_i18n says in bg and en only what the evidence says. A discounted price the site prints as an amount stays a fee entry (see above).
- currency as printed (EUR or BGN); if both are printed, the EUR figure.
- category: the monthly kindergarten fee and school tuition are tuition; an enrolment, exam, application or entry-test fee is registration; a deposit (депозит, капаро) is registration, never tuition; learning support, therapy and similar services are activities; optional classes, clubs and sports charged on top are extracurricular; transport, food, uniforms and extended day (after-school care) are their own categories.
- period only when the page states it for THAT amount (per month, per year, per term...); otherwise null. Never guess.
  - "once per school year" or "еднократна такса" for the school year is yearly; a full year's tuition paid in one payment is yearly too.
  - one_time only for a fee charged once ever: enrolment, exam, application, deposit. Never for tuition or materials.
  - A price per day, per week or per hour (a daily lunch, a weekly camp, an hourly class) is daily, weekly or hourly. How often a class meets ("3 пъти седмично") is not a period.
  - A count of payments ("2 x", "на две вноски") states no period: null.
  - In "1 x A + N x B", A is a first instalment with no period; B has the period the plan states (8 monthly instalments: monthly).
  - A yearly total only if the site prints it.
- Never build a range from two different fees (a full-day and a half-day price are two entries). A range only when the site prints one.
- source_url: the URL of the page the fee is on.
- evidence: the line of the page the amount is on, copied character for character, and the lines of its payment options. For a table row, also copy the heading line that gives its period, plan or group, as a separate line. If the period is stated in a sentence above the table, copy that sentence too. A PDF's tables are repeated after its text, one row per line with cells split by " | " and a cell's lines by " / ": when the text above splits a table row over several lines, copy the row and its heading row from there instead of joining lines yourself.
- label: the site's own wording, short. academic_year, age_group and plan_name as the site states them, or null. plan_name names what the fee buys (full day, half day) and any price terms, not a way of paying in parts: a payment plan's name is its option's label.
- plan_name_en and age_group_en: the same words in plain English, as a parent would read them on the English page ("Half day", "Paid for 6 months at once", "Ages 3 to 6"), adding nothing the Bulgarian does not say; null when the Bulgarian field is null."""


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
    # Not "целодневна" (a full-day group).
    PricePeriod.DAILY: r"(?<![а-я])дневн(?![а-я]*\s+(?:форм|режим))|(?:на|за|/)\s*ден\b|\b(?:daily|per day|a day)\b|par jour|pro tag",
    PricePeriod.WEEKLY: r"седмичн|(?:на|за|/)\s*седмица|\b(?:weekly|per week|a week)\b|par semaine|pro woche",
    PricePeriod.HOURLY: r"почасов|(?:на|за|/)\s*час\b|\b(?:hourly|per hour|an hour)\b|par heure|pro stunde",
}
# English "one-time" names a period only on the amount's own line: 596's "ANNUAL TUITION FEES"
# heading sits above its "One-time fee for initial enrollment".
_ONE_TIME_EN_RE = re.compile(r"\bone\s*[-‐‑–—]?\s*time\b")
# "for the school year" names a period only on the amount's own line: as a heading it often
# covers fees charged per day or per month too (569's "ДОПЪЛНИТЕЛНИ ТАКСИ ЗА УЧЕБНАТА ГОДИНА").
_YEAR_ON_AMOUNT_LINE_RE = re.compile(r"(?:учебн\w*|на|за)\s+година|l'année")
# How often a class meets, not how often it is paid: "3 пъти седмично", "twice a week",
# "2 учебни часа седмично", "седмична програма".
_HOW_OFTEN_RE = re.compile(
    r"(?:(?:\d+|един|една|два|две|три|четири|пет|шест|седем|two|three|four|five|six)\s*"
    r"(?:пъти|път|times?|x|хранения|meals)|(?<!до )(?<!\d)\d+\s*(?:учебни\s+)?(?:час|hours?|lessons?|classes)\w*"
    r"|веднъж|двукратно|twice|once)\s*"
    r"(?:седмично|в седмицата|на седмица|a week|per week|weekly|дневно|на ден|a day|per day|daily)"
    r"|седмичн\w*\s+(?:програм|разпис|график)|weekly (?:schedule|timetable)"
)
_DEPOSIT_RE = re.compile(r"депозит|капаро|deposit")
# An age, not a period: "6-годишни деца", "5 годишни", "6-месечни бебета" (564's "такса за
# обследване" for 5-year-olds lost its one-off period to the "годиш" of the age). Like Stage 6's
# (price_evidence._AGE_RE), but spaced only before a child word: "€ 3000 годишно" and
# "€ 3000 – годишно" are yearly prices.
_AGE_RE = re.compile(r"\d+[-–](?:годиш|месеч)\w*|\d+ ?[-–]? ?(?:годиш|месеч)\w* (?:деца|дете|бебета|възраст)")
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
    evidence = _AGE_RE.sub(" ", evidence)
    amount_lines = [_AGE_RE.sub(" ", line) for line in amount_lines]
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
    if period in (PricePeriod.DAILY, PricePeriod.WEEKLY):
        evidence = _HOW_OFTEN_RE.sub(" ", evidence.casefold())
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
        found = [line if _line_on_page(line, page, page_lines) else _page_row(line, page_lines) for line in lines]
        if any(line is None for line in found):
            return _dropped(fee, "dropped: evidence line is not on the page")
        lines = found
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


# A PDF table row as the page text prints it: "Transport | two- / way | € 2,550", cells split
# by " | " and a cell's lines by " / ". A reader can quote the row cleaned, "Transport two-way
# € 2,550" (596 lost every extra to it), so both sides are compared without the joins and with
# a word's hyphen break closed. Words and numbers must still match in order; a join between two
# numbers stays a break, so "2 | 550" cannot pass as a quoted "2 550".
_CELL_JOIN_RE = re.compile(r"\s*(?:\||\s/\s)\s*")
_HYPHEN_BREAK_RE = re.compile(r"(?<=[^\W\d_])- (?=[^\W\d_])")


def _loose(text: str) -> str:
    def join(match: re.Match[str]) -> str:
        between_numbers = re.search(r"\d[.,]?$", text[: match.start()]) and re.match(r"[.,]?\d", text[match.end() :])
        return " ¦ " if between_numbers else " "

    return _HYPHEN_BREAK_RE.sub("-", " ".join(_CELL_JOIN_RE.sub(join, text).split()))


def _page_row(line: str, page_lines: list[str]) -> str | None:
    """The one page line a cleaned quote of a table row came from, cells and all.

    The later checks read that line, not the quote: they need the " | " cells to tell the
    fee's own period from a payment option's ("€ 8.950 | € 750 x 9 monthly").
    """
    quote = _loose(line)
    rows = {text for text in page_lines if quote in _loose(text)}
    return rows.pop() if len(rows) == 1 else None


_LABEL_LINES_AWAY = 3
# A label names the row ("Grade 8 – 9", "Годишна такса"): it holds no price, so it cannot
# carry another row's amount, or that amount's "monthly", into the fee's line. A price is a
# currency or a number of three digits or more.
_NOT_A_LABEL_RE = re.compile(r"[€$£]|\b(?:eur|euros?|usd|bgn|лв|лева|leva|евро)\b|\d{3}|\d[.,\s]\d{3}", re.IGNORECASE)


def _line_on_page(line: str, page: str, page_lines: list[str]) -> bool:
    """The evidence line is on the page, or is a row's label joined to the row.

    A PDF table's text can put a row's label on another line than its first amount: 596's
    secondary fees read "Plan 1 – € 9,500" on one line and "Grade 8 – 9 Plan 2 – …" two
    lines down, and the reader quoted the row as "Grade 8 – 9 Plan 1 – € 9,500". The row
    must be a whole line of the page and the label the start of a line at most three lines
    from it, with no price in it, so a label and an amount from unrelated
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


# Which child a sibling discount is for, as pages write it: "второ дете", "2nd child", "the third".
_CHILD_WORDS = {
    # A bare "2" is not enough: in "2% sibling discount" it is the percentage.
    2: r"\b2\s*-?\s*(?:ро|nd|ри)\b|втор|second",
    3: r"\b3\s*-?\s*(?:то|rd|ти)\b|трет|third",
    4: r"\b4\s*-?\s*(?:то|th|ти)\b|четвърт|fourth",
}


def _percent_printed(value: Decimal, text: str) -> bool:
    """The number is printed as a percentage: "10%", "10 %", "10 процента", "10 percent"."""
    return any(
        value in values and re.match(r"\s*(?:%|процент|percent)", text[end:], re.IGNORECASE)
        for _, end, values in number_spans(text)
    )


def check_discount(discount: AgentDiscount, page_text: str) -> Optional[AgentDiscount]:
    """The discount if the page backs it, else None: its evidence is on the page and prints
    its number as a percentage (or holds its amount), a sibling discount's child is named
    there, and both sentences state that number."""
    page = _normalized(page_text)
    lines = [_normalized(line) for line in discount.evidence.splitlines() if line.strip()]
    if not lines:
        return None
    if any(line not in page for line in lines):
        page_lines = [_normalized(text) for text in page_text.splitlines() if text.strip()]
        if any(not _line_on_page(line, page, page_lines) for line in lines):
            return None
    if (discount.percent is None) == (discount.amount is None):
        return None
    number = discount.percent if discount.percent is not None else discount.amount
    if number <= 0 or (discount.percent is not None and number > 100):  # 100%: a free place
        return None
    value = _decimal(number)
    evidence = "\n".join(lines)
    # "до 15 май – 10% отстъпка": 15 is a date, so a percent must be printed as one.
    if not (_percent_printed(value, evidence) if discount.percent is not None else value in page_numbers(evidence)):
        return None
    if discount.child in _CHILD_WORDS and not re.search(_CHILD_WORDS[discount.child], evidence, re.IGNORECASE):
        return None
    texts = [discount.text_i18n.bg, discount.text_i18n.en]
    if any(not text.strip() or value not in page_numbers(text) for text in texts):
        return None
    return discount.model_copy(update={"currency": discount.currency if discount.amount is not None else None})
