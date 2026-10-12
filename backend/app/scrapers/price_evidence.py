"""Price-row and display-name evidence rules (UF45).

Pure checks of one stored price row against the text of its linked source page, shared
by Stage 6 validation (which turns them into publish-gate issues) and the read-only
audit ``scripts/audit_price_rows_uf45.py``:

1. amount near its label  - the amount is on the page and, when the row's plan name (or
                            notes) is on the page, in that label's own run of prices;
2. level fits the school  - the label (or, with no label, the amount's label lines)
                            names the other level (``shared_site_check.describes_level``);
3. period from the text   - a null period with one period keyword next to the amount, a
                            keyword that disagrees with the stored period, or a period the
                            schema cannot represent (per day/week/hour);
4. deposit is not tuition - a tuition row whose label/context says deposit; 4b: a
                            non-monthly tuition row whose line says it is one installment;
5. name is not a sibling's - see :func:`shared_names`.

No model is involved: every rule reads the page text.
"""

from __future__ import annotations

import datetime
import decimal
import re
from dataclasses import dataclass
from typing import Any, Iterable, Optional
from urllib.parse import unquote, urlparse

from app.scrapers.shared_site_check import describes_level, registrable_domain, url_names_other_level

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


def currency_price_starts(text: str) -> list[int]:
    """Start offsets of the prices written with a currency (bare numbers do not count)."""
    return [m.start() for m in _PRICE_RE.finditer(text)]


_TABLE_NUMBER_RE = re.compile(r"(?<![\d.,/])\d{1,3}(?:[ \u00a0]?\d{3})+(?![\d.,/])|(?<![\d.,/])\d{3,5}(?![\d.,/])")


def fee_number_starts(text: str) -> list[int]:
    """Start offsets of what reads as prices: currency amounts, and table cells of 3+ digits.

    A fee table often names its currency once, in a heading ("all fees are in euro"),
    and leaves the cells bare.
    """
    starts = currency_price_starts(text)
    offset = 0
    for line in text.splitlines(keepends=True):
        if "|" in line:
            starts.extend(
                offset + m.start()
                for m in _TABLE_NUMBER_RE.finditer(line)
                if not re.fullmatch(r"(?:19|20)\d{2}", m.group(0))  # a year, not a price
            )
        offset += len(line)
    return sorted(set(starts))


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
    """The amount's own line, cut at the neighbouring prices on it.

    The same fee in the other currency right after it is not a neighbour: in "899.68 лв.
    / 460 € / платима до 15 число на предидущия месец" (330) the words after the euro
    figure are the lev figure's too.
    """
    start = text.rfind("\n", 0, span[0]) + 1
    line = _line_of(text, span)
    rel = (span[0] - start, span[1] - start)
    left, right = max(0, rel[0] - CONTEXT_CHARS), min(len(line), rel[1] + CONTEXT_CHARS)
    own_end, own_currency = rel[1], None
    for match in _PRICE_RE.finditer(line):
        if match.start() < rel[1] and rel[0] < match.end():
            own_end, own_currency = match.end(), _price_currency(match.group(0))
        elif match.end() <= rel[0]:
            left = max(left, match.end())
        elif match.start() >= rel[1]:
            twin = (
                own_currency is not None
                and own_end is not None
                and re.fullmatch(r"[\s/|()\-–—]*", line[own_end: match.start()])
                and _price_currency(match.group(0)) not in (None, own_currency)
            )
            if twin:
                own_end = None  # one twin at most; the next price is a neighbour
                clause_end = re.search(r"[,;|]", line[match.end():])
                right = min(len(line), match.end() + CONTEXT_CHARS)
                if clause_end:
                    right = min(right, match.end() + clause_end.start())
                continue
            right = min(right, match.start())
            break
    return line[left:right]


def _price_currency(price: str) -> Optional[str]:
    found = re.search(_CURRENCY_TOKEN, price, re.IGNORECASE)
    return _CURRENCY_CODES.get(found.group(1).casefold()) if found else None


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
    # "label" with its colon starting this line ("Депозит\n: 450 EUR") is this amount's label.
    split_label = text[start:end].lstrip().startswith(":")
    for index, previous in enumerate(reversed(before_lines[-ATTACHED_LINES:])):
        if _is_price_line(previous) or not (
            previous.rstrip().endswith(":") or (index == 0 and split_label)
        ):
            break
        parts.insert(0, previous)
    after_lines = text[end + 1:].split("\n") if end < len(text) else []
    for index, following in enumerate(after_lines[:ATTACHED_LINES]):
        # The next fee's label: "label:", or "label" with its colon starting the next
        # line ("Депозит\n: 450 EUR", school 183).
        next_line = after_lines[index + 1] if index + 1 < len(after_lines) else ""
        if (
            _is_price_line(following)
            or following.rstrip().endswith(":")
            or next_line.lstrip().startswith(":")
        ):
            break
        parts.append(following)
    return "\n".join(parts)


_CURRENCY_CODES = {
    "€": "EUR", "eur": "EUR", "euro": "EUR", "евро": "EUR",
    "лв": "BGN", "лв.": "BGN", "лева": "BGN", "bgn": "BGN",
    "$": "USD", "usd": "USD", "£": "GBP", "gbp": "GBP",
}
_CURRENCY_TOKEN = r"(€|\$|£|eur\b|euro\b|евро|лв\.?|лева|bgn|usd|gbp)"
# Right before the amount on its line ("€ 530"), or alone on the line above ("EUR\n6600").
_CURRENCY_BEFORE_RE = re.compile(rf"(?:{_CURRENCY_TOKEN}[ \t]*|\n[ \t]*{_CURRENCY_TOKEN}[ \t]*\n[ \t]*)\Z")
# Right after it ("530 лв."), or alone on the line below ("7050\nевро").
_CURRENCY_AFTER_RE = re.compile(rf"^(?:[ \t]*{_CURRENCY_TOKEN}|[ \t]*\n[ \t]*{_CURRENCY_TOKEN}[ \t]*(?:\n|\Z))")


def occurrence_currency(text: str, span: tuple[int, int]) -> Optional[str]:
    """The currency written next to one amount occurrence (None when none is)."""
    after = _CURRENCY_AFTER_RE.match(text[span[1]: span[1] + 16])
    if after:
        return _CURRENCY_CODES[next(group for group in after.groups() if group)]
    start = max(0, span[0] - 16)
    window = text[start: span[0]]
    if start == 0 or text[start - 1] == "\n":
        window = "\n" + window  # the window starts a line
    before = _CURRENCY_BEFORE_RE.search(window)
    if before:
        return _CURRENCY_CODES[next(group for group in before.groups() if group)]
    return None


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
    # "платима до 15 число на предходния месец" (330) is paid every month.
    "MONTHLY": re.compile(
        r"(?<!три)месеч|\bна месец|/\s*мес|per month|/\s*month|\bmonthly\b|a month\b"
        r"|\b(?:всеки|предходния|предишния|предидущия|текущия|следващия) месец"
    ),
    # "Total tuition fees (year)" heads a yearly table.
    "YEARLY": re.compile(
        r"(?<!полу)годиш|\bна година|/\s*год|per year|/\s*year|\bannual|\byearly\b|a year\b|\(\s*year\s*\)"
    ),
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

# A price on offer until a date: early enrolment, the first contracts, a dated price
# list ("Цени: 1 050 EUR до 7 февруари 2026 г.", 581; "до 19.12.2025 г. за първите 7
# договора", 171). A payment's due date ("платима до 15.09.") is not an offer's end, nor
# is the end of enrolment alone ("записване до 15.09.") the end of the fee.
_MONTHS = {
    "януари": 1, "февруари": 2, "март": 3, "април": 4, "май": 5, "юни": 6, "юли": 7,
    "август": 8, "септември": 9, "октомври": 10, "ноември": 11, "декември": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7,
    "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}  # fmt: skip
_DEADLINE_RE = re.compile(
    r"(?:\bдо|\buntil|\bby|\bbefore)\s+(?:(\d{1,2})\s*[./]\s*(\d{1,2})\s*[./]\s*(20\d{2})"
    rf"|(\d{{1,2}})\s+({'|'.join(_MONTHS)})\s+(20\d{{2}}))"
)
_OFFER_WORDS_RE = re.compile(
    r"записани|сключ|договор|в срок|ранн|отстъпк|намален|промо|цени\b|цена\b|валидн|важи"
    r"|early|contract|discount|offer|valid|price"
)
_OFFER_WINDOW = 60  # characters before "до <date>" that say what ends then
_DUE_DATE_RE = re.compile(r"платим|плаща|вноск|заплат|падеж|\bpaid\b|\bpay(?:able|ment)?\b|\bdue\b|instal")
# After the date: a later price ("след това 8 500 €"), or a percentage off the price.
_AFTER_RE = re.compile(r"\bслед\b|\bafter\b|thereafter|%")
# ...or before it ("Отстъпка 5% при записване до 31.05.2026"): a discount on the price.
_DISCOUNT_NOTE_RE = re.compile(r"%|процент|percent")
# "Записване до 15.09.2026" closes enrolment; the price stays ("в срок до" sets a tier).
_ENROLMENT_END_RE = re.compile(r"записване\s*$|кандидатстване\s*$|прием\s*$|enrol?lment\s*$|applications?\s*$")


def period_fits_category(category: str | None, period: str | None) -> bool:
    """False for a period the fee category cannot have, whatever stands beside the amount.

    "Еднократно плащане" beside a tuition fee is the fee paid in one instalment (517's
    "ГОДИШНА ТАКСА" came out one_time), and a recurring word beside a registration fee
    belongs to a neighbouring fee on the line (151's came out yearly).
    """
    category, period = str(category or "").lower(), str(period or "").lower() or None
    if category == "tuition" and period == "one_time":
        return False
    return not (category == "registration" and period not in (None, "one_time"))


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


HEADING_LINES = 20  # how far above an amount its table or list heading may stand
HEADING_CHARS = 80  # a longer line is prose, not a heading
_TABLE_RULE_RE = re.compile(r"^[\s|:\-–—=*_]*$")


def heading_period(text: str, span: tuple[int, int]) -> Optional[str]:
    """The one period the heading over an amount's table or list states, if any.

    For an amount with no period of its own: 517's "ГОДИШНА ТАКСА" heads a table of
    grades and prices, and 542's "Месечни такси" a list of programmes. Going up from the
    amount, price lines (other fees) and lines naming no period (row labels, column
    headers) are passed over; the first short line naming a period decides. A line
    followed by a bare price ("Депозит (еднократно):" over "500 €") is that fee's label,
    not a heading, unless the bare price is this amount or the line is a table's header
    row; "- ежемесечно" under a price describes that price. In the header row of the
    amount's own table only the amount's column counts. A line naming no period over a
    fee line with its own words ("Допълнителни услуги" over "Транспорт | 80 лв."), a bold
    line or a markdown heading starts another section and ends the search, and so does a
    fee laid out unlike the amount's own ("1 клас | 7000 €" above "Храна" over a bare
    "150 €"): the two are not one list. Several periods, a per-day/week one, or a long
    line of prose decide nothing.
    """
    line_start = text.rfind("\n", 0, span[0]) + 1
    amount_line, column_at = _line_of(text, span), span[0] - line_start
    lines = text[:line_start].split("\n")[:-1][-HEADING_LINES:]

    def shape(line: str) -> tuple[bool, bool]:
        return "|" in line, _is_bare_price_line(line)

    for index in range(len(lines) - 1, -1, -1):
        line = lines[index]
        markdown_heading = line.lstrip().startswith("#")
        if _is_price_line(line) and not markdown_heading and shape(line) != shape(amount_line):
            return None
        if not line.strip() or _TABLE_RULE_RE.match(line) or (_is_price_line(line) and not markdown_heading):
            continue
        if index and _is_price_line(lines[index - 1]) and re.match(r"\s*[-–—/]", line):
            continue  # "€ 530\n- ежемесечно заплащане": the price above's description
        families, unrepresentable = period_families(line)
        if not (families or unrepresentable):
            following = next((later for later in lines[index + 1:] if later.strip()), amount_line)
            section = "|" not in line and _is_price_line(following) and not _is_bare_price_line(following)
            if markdown_heading or section or re.fullmatch(r"\s*\*\*.+\*\*\s*", line):
                return None  # another section's heading: "Допълнителни услуги" over "Транспорт | 80 лв."
            continue
        if "|" in line and "|" in amount_line:
            # The header row of the amount's table: only the amount's own column speaks.
            cells = line.split("|")
            if len(cells) != amount_line.count("|") + 1:
                return None
            families, unrepresentable = period_families(cells[amount_line[:column_at].count("|")])
        if unrepresentable or len(families) != 1 or len(line.strip(" #*_|")) > HEADING_CHARS:
            return None
        following = next(
            (later for later in lines[index + 1:] if later.strip() and not _TABLE_RULE_RE.match(later)), None
        )
        if following is not None and "|" not in line and _is_bare_price_line(following):
            return None
        return next(iter(families))
    return None


def _is_bare_price_line(line: str) -> bool:
    """A price with no words of its own: the amount of the label line above it."""
    return _is_price_line(line) and not _has_words(_PRICE_RE.sub(" ", line))


# An international school's Pre-K is its own reception class, not a kindergarten.
_PRE_K_RE = re.compile(r"pre[- ]?(?:kindergarten|k\b)")


def names_other_level(text: str, school_family: str) -> bool:
    lowered = (text or "").casefold()
    if school_family == "kindergarten":
        return describes_level(lowered, "school")
    lowered = _PRE_K_RE.sub(" ", lowered)
    return describes_level(lowered, "kindergarten") or bool(_NURSERY_RE.search(lowered))


# ---------------------------------------------------------------------------
# Whose fee: the grades a label names against the grades the institution teaches
# ---------------------------------------------------------------------------

PRESCHOOL_GRADE = 0  # the class before first grade (ПК / ПГ / Pre-K)

_ROMAN_GRADES = {
    "i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7, "viii": 8, "ix": 9, "x": 10,
    "xi": 11, "xii": 12,
}  # fmt: skip
_WORD_GRADES = {
    "първи": 1, "втори": 2, "трети": 3, "четвърти": 4, "пети": 5, "шести": 6, "седми": 7,
    "осми": 8, "девети": 9, "десети": 10, "единадесети": 11, "единайсети": 11,
    "дванадесети": 12, "дванайсети": 12,
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7,
    "eighth": 8, "ninth": 9, "tenth": 10, "eleventh": 11, "twelfth": 12,
}  # fmt: skip
_PRESCHOOL_TOKENS = {"пк", "пг", "пгу", "пук", "pk", "prek"}
# Roman numerals written with Cyrillic look-alikes ("VIІ клас") read as Latin.
_LOOKALIKES = str.maketrans("іхѵ", "ixv")
_GRADE_VALUE = (
    # A roman grade is a whole word: the "I" of "International" is not grade one.
    r"(?:1[0-2]|[0-9]|x{0,1}(?:ix|iv|v?i{0,3})(?![^\W\d_])|"
    + "|".join(sorted(_WORD_GRADES, key=len, reverse=True))
    + "|"
    + "|".join(sorted(_PRESCHOOL_TOKENS, key=len, reverse=True))
    + r")"
)
_GRADE_SUFFIX = r"(?:\s*\.|\s*-?\s*(?:ви|ри|ти|ми|st|nd|rd|th)\b)?"
_GRADE_JOIN = r"\s*(?:-|–|—|,|/|\bдо\b|\bи\b|\bto\b|\band\b)\s*"
_GRADE_RUN = rf"\b{_GRADE_VALUE}{_GRADE_SUFFIX}(?:{_GRADE_JOIN}{_GRADE_VALUE}{_GRADE_SUFFIX})*"
_GRADE_WORD = r"(?:клас(?:ове)?|кл\.|grades?)"
# "5 - 7 клас", "VIII - XII клас", "ПК-4. клас" / "Grade 5", "Grades 4th - 7th".
_GRADES_BEFORE_RE = re.compile(rf"({_GRADE_RUN})\s*{_GRADE_WORD}(?![\w-])")
_GRADES_AFTER_RE = re.compile(rf"\bgrades?\s*({_GRADE_RUN})")
_GRADE_TOKEN_RE = re.compile(rf"{_GRADE_VALUE}|-|–|—|\bдо\b|\bto\b")
# School stages named without a grade, also as a page address spells them.
_STAGE_GRADES: tuple[tuple[re.Pattern[str], range], ...] = (
    (re.compile(r"прогимназ|progimnazi|middle school|coll[eè]ge"), range(5, 8)),
    (
        re.compile(r"начално училище|nachalno uchilishte|начален етап|primary|elementary|[eé]l[eé]mentaire"),
        range(1, 5),
    ),
    (re.compile(r"основно училище|osnovno uchilishte"), range(1, 8)),
    (re.compile(r"(?<!про)гимназ|(?<!pro)gimnazi|high school|lyc[eé]e"), range(8, 13)),
)
_PRESCHOOL_LABEL_RE = re.compile(
    r"подготв|подготов|предучилищ|pre-?school|preparatory|reception|pre[- ]?k(?:indergarten)?\b"
)
# A kindergarten or nursery group: named, or given by the children's age.
_KINDERGARTEN_LABEL_RE = re.compile(
    r"детска\s+градина|\bградина\b|ясл|kindergar[td]en|nursery|toddler|maternelle|ранно детско"
    # An age is one or two digits; "2026/2027 г." is a year, "за 10 месеца" a payment span.
    r"|(?<![\d/.-])\d{1,2}\s*(?:г\.|г\b|годиш|години\b|years?\b|yrs?\b|y\.?o\b)"
)


def _grade_number(token: str) -> Optional[int]:
    if token.isdigit():
        return int(token)
    if token in _PRESCHOOL_TOKENS:
        return PRESCHOOL_GRADE
    return _ROMAN_GRADES.get(token) or _WORD_GRADES.get(token)


def label_grades(label: str | None) -> set[int]:
    """School grades a fee label names (0 for the pre-school class); empty when none.

    "5 - 7 клас" is {5, 6, 7}, "8 клас" {8}, "ПК-4. клас" {0..4}, "Grades 4th - 7th"
    {4..7}, "Начално училище" {1..4}. A number with no grade word is not a grade.
    """
    text = str(label or "").casefold().translate(_LOOKALIKES)
    grades: set[int] = set()
    for run in _GRADES_BEFORE_RE.findall(text) + _GRADES_AFTER_RE.findall(text):
        previous: Optional[int] = None
        spanning = False
        for token in _GRADE_TOKEN_RE.findall(run):
            number = _grade_number(token)
            if number is None:
                spanning = previous is not None
                continue
            if spanning and previous is not None and number > previous:
                grades.update(range(previous, number + 1))
            grades.add(number)
            previous, spanning = number, False
    for pattern, stage in _STAGE_GRADES:
        if pattern.search(text):
            grades.update(stage)
    return grades


def taught_grades(age_groups: Iterable[str | None]) -> set[int]:
    """Grades behind a school's age groups ("grade_5_7" -> 5, 6, 7; "preschool" -> 0)."""
    grades: set[int] = set()
    for group in age_groups:
        match = re.fullmatch(r"grade_(\d+)_(\d+)", str(group or ""))
        if match:
            grades.update(range(int(match.group(1)), int(match.group(2)) + 1))
        elif group == "preschool":
            grades.add(PRESCHOOL_GRADE)
    return grades


def _heading_of(text: str, span: tuple[int, int]) -> str:
    """The amount's own line with the nearest line above it that states no price."""
    start = text.rfind("\n", 0, span[0]) + 1
    for line in reversed(text[:start].splitlines()):
        if line.strip() and not _is_price_line(line):
            return f"{line}\n{_line_of(text, span)}"
    return _line_of(text, span)


def _line_above(text: str, span: tuple[int, int]) -> str:
    """The nearest line above the amount's that states no price ("" when none)."""
    heading = _heading_of(text, span)
    return heading.split("\n", 1)[0] if "\n" in heading else ""


def _own_words(text: str, span: tuple[int, int]) -> str:
    """The amount's segment; with no level named there, the line it stands under too."""
    segment = amount_segment(text, span)
    return segment if label_names_a_level(segment) else f"{_line_above(text, span)}\n{segment}"


def label_names_a_level(label: str | None) -> bool:
    """The label names school grades, a pre-school class or a kindergarten group."""
    text = str(label or "").casefold()
    return bool(
        label_grades(text)
        or _PRESCHOOL_LABEL_RE.search(text)
        or _KINDERGARTEN_LABEL_RE.search(text)
        or re.search(r"училищ|school", text)
    )


def proper_name(registry_name: str | None) -> Optional[str]:
    """The name in quotes inside a registry name: 'ЧАСТНА ГИМНАЗИЯ ... "АСЕН ЙОРДАНОВ" ЕООД'.

    None when the quoted part is the whole name, kind of institution included.
    """
    parts = [part.strip() for part in re.split(r"[\"„“”«»]", str(registry_name or ""))]
    names = [
        part
        for part in parts[1:-1]  # between two quotation marks
        if len(part) >= 4
        and part[0].isalpha()
        and not re.search(r"училищ|гимназ|градин|school|kindergarten", part.casefold())
        and not re.fullmatch(r"е?оо?д|е?ад", part.casefold())
    ]
    return names[-1] if names else None


def page_is_another_institutions(
    url: str | None, grades: Iterable[int], school_family: str = "school", shares_site: bool = False
) -> bool:
    """The page's address names only a stage the school does not teach.

    550, a gymnasium, took its fees from "/chastno-osnovno-uchilishte/.../priem-petoklasnici",
    the basic school's section of the site they share. ``grades`` are the school's
    :func:`taught_grades`: empty, and so no judgement, unless a sibling shares the site.
    A kindergarten that shares its site with a school (``shares_site``) does not take
    fees from a page whose address names a school and no kindergarten (510 read
    "svetlina.net/school/").
    """
    if school_family == "kindergarten":
        # The subdomain counts too ("school.brand.bg"), the shared domain does not.
        return shares_site and url_names_other_level(str(url or ""), "kindergarten", registrable_domain(url))
    words = re.sub(r"[/_\-.+%=&?]+", " ", unquote(urlparse(str(url or "")).path)).casefold()
    grades = set(grades)
    named = {grade for pattern, stage in _STAGE_GRADES if pattern.search(words) for grade in stage}
    return bool(grades and named) and not (named & grades)


# Fees only a school charges: its pupils, its homework club, its diploma programme.
_SCHOOL_ONLY_LABEL_RE = re.compile(
    r"\bученици|\bученик|занималн|\bib\b|international baccalaureate|бакалавр|гимназ|lyc[eé]e"
)
# A school or its pupils named in a kindergarten's fee label. A kindergarten may mean
# school readiness ("подготовка за училище"), a summer school, the school canteen its
# meals come from ("училищния стол") or an English "pupil", so it counts only on a site
# it shares with a school, and only the school itself, not "училищн-" adjectives.
_SCHOOL_WORD_RE = re.compile(
    r"училище\b|училището|училища\b|\bschool\b(?!\s*(?:readiness|ready|prep))|\bpupils?\b|\bч?(?:оу|су)\b|\bчпг\b"
)


def label_is_another_institutions(
    label: str | None,
    school_family: str,
    grades: set[int],
    own_name: str | None = None,
    shares_site: bool = False,
) -> bool:
    """A fee label places the fee with a sibling institution of the same site.

    ``grades`` are the school's :func:`taught_grades` (empty when unknown). A
    kindergarten's fee does not name school grades, beyond readiness for the first
    ("подготовка за 1 клас"); a band that runs from the pre-school class into school
    ("ПК, 1-12 клас", 634) is the school's. Nor is it for pupils, a homework club or the
    IB (526, 587, 634), or, on a site it shares with a school (``shares_site``), for a
    school it names. A school's fee does not name only
    grades the school does not teach (the gymnasium's "8 - 12 клас" on the primary
    school's page), nor only a kindergarten or nursery group. A pre-school class may be
    either's, so a label naming one and no school grade is kept. So is a label that
    names other grades and the school itself (``own_name``, a name no sibling school
    bears): "5-7 клас и ЧГПНП „Асен Йорданов“" is the gymnasium's fee as well as the
    fifth grade's.
    """
    text = str(label or "").casefold()
    named = label_grades(text)
    preschool = bool(_PRESCHOOL_LABEL_RE.search(text)) or PRESCHOOL_GRADE in named
    school_grades = named - {PRESCHOOL_GRADE}
    if school_family == "kindergarten":
        if school_grades and not (preschool and school_grades == {1}):
            return True
        if preschool or _KINDERGARTEN_LABEL_RE.search(text):
            return False
        return bool(_SCHOOL_ONLY_LABEL_RE.search(text) or (shares_site and _SCHOOL_WORD_RE.search(text)))
    if school_grades:
        named_itself = bool(own_name and _label_key(own_name) and _label_key(own_name) in _label_key(label))
        return bool(grades) and not (school_grades & grades) and not named_itself
    return bool(_KINDERGARTEN_LABEL_RE.search(text)) and not preschool


def expired_offer(context: str, today: datetime.date) -> Optional[datetime.date]:
    """The passed end date of the offer an amount's words make, or None.

    Each "до <date>" is read with the words just before it. They must speak of an offer
    ("при записване в срок до", "записани до", contracts, a discount, a dated price list)
    and not of paying: "I вноска до 23.01.2026" or "платима до 15.09.2026" is when a fee
    falls due, not the last day it is charged. A date followed by "след" ("до 31.05.2026,
    след това 8 500 €") ends the offer before the price that replaced it, and one followed
    by a percentage ("до 31.05.2026 г. отстъпка 10%"), or preceded by one ("Отстъпка 5%
    при записване до 31.05.2026"), is a discount on the price shown.
    """
    previous_end = 0
    for match in _DEADLINE_RE.finditer(context):
        before = context[max(previous_end, match.start() - _OFFER_WINDOW): match.start()]
        after = context[match.end(): match.end() + 40]
        previous_end = match.end()
        if (
            _DUE_DATE_RE.search(before)
            or _AFTER_RE.search(after)
            or not _OFFER_WORDS_RE.search(before)
            or (_ENROLMENT_END_RE.search(before) and "в срок" not in before)
            or _DISCOUNT_NOTE_RE.search(before)
        ):
            continue
        day, month, year = (
            (match.group(1), match.group(2), match.group(3))
            if match.group(1)
            else (match.group(4), _MONTHS[match.group(5)], match.group(6))
        )
        try:
            deadline = datetime.date(int(year), int(month), int(day))
        except ValueError:
            continue
        if deadline < today:
            return deadline
    return None


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------

RULE_AMOUNT_NEAR_LABEL = "1_amount_near_label"
RULE_LEVEL = "2_level_fits_school"
RULE_PERIOD_MISSING = "3_period_missing"
RULE_PERIOD_CONFLICT = "3_period_conflict"
RULE_PERIOD_UNREPRESENTABLE = "3_period_unrepresentable"
RULE_DEPOSIT = "4_deposit_not_tuition"
RULE_INSTALLMENT = "4_installment_not_tuition"
RULE_EXPIRED = "7_offer_expired"


@dataclass(frozen=True)
class PriceFinding:
    rule: str
    detail: str
    snippet: str
    # RULE_PERIOD_MISSING / RULE_PERIOD_CONFLICT: the period the text states (upper case).
    period: Optional[str] = None


@dataclass(frozen=True)
class PriceRow:
    category: str
    amount: Any
    amount_min: Any
    amount_max: Any
    period: Optional[str]
    plan_name: Optional[str]
    notes: Optional[str]
    currency: Optional[str] = None
    age_group: Optional[str] = None

    @property
    def label(self) -> Optional[str]:
        return (self.plan_name or self.notes or "").strip() or None

    @property
    def scope_label(self) -> str:
        """What says whom the fee is for: the plan name and the age group together."""
        return " / ".join(part for part in (self.plan_name, self.age_group) if part)

    @classmethod
    def from_pricing(cls, row: Any) -> "PriceRow":
        """From a ``Pricing`` ORM row (or anything with the same attributes)."""
        context = row.pricing_context if isinstance(row.pricing_context, dict) else {}
        notes = context.get("notes")
        return cls(
            category=_enum(row.category),
            amount=row.amount,
            amount_min=row.amount_min,
            amount_max=row.amount_max,
            period=_enum(row.period),
            plan_name=row.plan_name,
            notes=notes if isinstance(notes, str) else None,
            currency=str(row.currency or "").strip().upper() or None,
            age_group=row.age_group,
        )

    @property
    def amounts(self) -> list[Any]:
        if self.amount is not None:
            return [self.amount]
        return [v for v in (self.amount_min, self.amount_max) if v is not None]


def _enum(value: Any) -> Optional[str]:
    value = getattr(value, "value", value)
    return str(value).upper() if value is not None else None


def check_price_row(
    row: PriceRow,
    page_text: str | None,
    school_family: str,
    grades: Iterable[int] = (),
    own_name: str | None = None,
    shares_site: bool = False,
    today: datetime.date | None = None,
) -> list[PriceFinding]:
    """Rule 1-4 findings for one price row against its source page text.

    ``school_family`` is ``shared_site_check.level_family`` of the school's level;
    ``grades`` are the grades it teaches (:func:`taught_grades`), when known, and
    ``own_name`` the name that tells it from its siblings on the site, when it has one.
    ``shares_site`` says an institution of the other family shares its site.
    """
    text = normalize_text(page_text)
    hits: list[PriceFinding] = []

    def hit(rule: str, detail: str, snippet: str, period: Optional[str] = None) -> None:
        hits.append(PriceFinding(rule, detail, snippet, period))

    if not text:
        hit(RULE_AMOUNT_NEAR_LABEL, "no stored page text", "")
        return hits

    # Every amount (both ends of a range) must be on the page, in the row's currency
    # where the page writes one next to it ("€ 530 | 1037 лв." holds 530 EUR, not BGN).
    spans = []
    for value in row.amounts:
        found = amount_spans(text, value)
        if not found:
            hit(RULE_AMOUNT_NEAR_LABEL, f"amount {value} not on the page", text[:160])
            return hits
        in_currency = [
            span for span in found if occurrence_currency(text, span) in (None, row.currency)
        ]
        if row.currency and not in_currency:
            shown = sorted({occurrence_currency(text, span) for span in found})
            hit(
                RULE_AMOUNT_NEAR_LABEL,
                f"amount {value} {row.currency} is shown only in {', '.join(shown)}",
                _snippet(text, found[0]),
            )
            return hits
        spans.extend(in_currency or found)
    labels = label_spans(text, row.label)

    # The amount occurrence this row is about: the one nearest its label, when found.
    # In a markdown table the label is a column header, so the check says nothing there.
    chosen = spans
    if labels:
        prices = price_spans(text)
        best = min(
            (
                ("|" not in _line_of(text, s) and not label_is_near(text, prices, s, l), _gap(s, l), s, l)
                for s in spans
                for l in labels  # noqa: E741
            ),
            key=lambda t: (t[0], t[1]),
        )
        if best[0]:
            hit(
                RULE_AMOUNT_NEAR_LABEL,
                f"label {row.label!r} is separated from the amount by other prices",
                _snippet(text, best[3]),
            )
        chosen = [best[2]]
    contexts = [amount_context(text, span) for span in chosen]
    shown = _snippet(text, chosen[0])

    # Rule 2: the row's own label, or every occurrence's label lines, name the other level.
    other_family = "school" if school_family == "kindergarten" else "kindergarten"
    def foreign(label: str | None) -> bool:
        return label_is_another_institutions(label, school_family, set(grades), own_name, shares_site)

    if foreign(row.scope_label):
        hit(RULE_LEVEL, f"label is a sibling institution's: {row.scope_label!r}", shown)
    elif not label_names_a_level(row.scope_label) and all(foreign(_heading_of(text, span)) for span in chosen):
        # The row's own label says nothing about whom it is for ("такси за нови
        # ученици"); the lines its amount stands under do (565 took "5-7. клас").
        hit(RULE_LEVEL, "amount stands under a sibling institution's label", contexts[0])
    elif shares_site and all(foreign(_own_words(text, span)) for span in chosen):
        # Whatever the row's label says, the amount's own words are the sibling's: 522,
        # a school, stored the kindergarten's "5г. – 6г. 7865 за година" as "1 - 12 клас".
        hit(RULE_LEVEL, "the amount's own line is a sibling institution's", contexts[0])
    elif row.label and names_other_level(row.label, school_family):
        hit(RULE_LEVEL, f"label names {other_family}: {row.label!r}", shown)
    elif not row.label and all(names_other_level(context, school_family) for context in contexts):
        hit(RULE_LEVEL, f"amount's label lines name {other_family}", contexts[0])

    # Rule 3: period. Needs agreement across the occurrences considered.
    found = [stated_period(text, span) for span in chosen]
    if all(unrepresentable for _, unrepresentable in found):
        hit(RULE_PERIOD_UNREPRESENTABLE, "per day/week/hour next to the amount", contexts[0])
    else:
        families = [fams for fams, _ in found]
        single = families[0] if all(f == families[0] for f in families) and len(families[0]) == 1 else None
        stored = _enum(row.period)
        headed: set[Optional[str]] = set()
        if stored is None and (
            not any(families) or (single and not period_fits_category(row.category, next(iter(single))))
        ):
            # Nothing beside the amount, or only a word the fee cannot have (517's
            # "Еднократно плащане: До 09.07.2026" under a yearly fee): the heading of its
            # table or list may say.
            headed = {heading_period(text, span) for span in chosen}
        if len(headed) == 1 and None not in headed and period_fits_category(row.category, next(iter(headed))):
            stated = headed.pop()
            hit(RULE_PERIOD_MISSING, f"null period, heading says {stated}", contexts[0], stated)
        elif single and stored is None:
            stated = next(iter(single))
            hit(RULE_PERIOD_MISSING, f"null period, text says {stated}", contexts[0], stated)
        elif single and stored and stored not in single and {stored} | single != {"ONE_TIME", "YEARLY"}:
            stated = next(iter(single))
            hit(RULE_PERIOD_CONFLICT, f"stored {stored}, text says {stated}", contexts[0], stated)

    # Rule 4: a deposit filed as tuition.
    if _enum(row.category) == "TUITION":
        label_text = (row.label or "").casefold()
        if _DEPOSIT_RE.search(label_text) or all(_DEPOSIT_RE.search(c) for c in contexts):
            hit(RULE_DEPOSIT, "tuition row labelled as a deposit", contexts[0])
        # Same family: one installment published as the whole fee. Only the amount's own
        # line counts; a monthly row is the monthly installment and may stand.
        elif _enum(row.period) != "MONTHLY" and all(
            _INSTALLMENT_RE.search(_line_of(text, span)) for span in chosen
        ):
            hit(RULE_INSTALLMENT, "tuition row is one installment", _line_of(text, chosen[0]))

    # Rule 7: an offer whose last day has passed (an early-enrolment tier, last season's trip).
    # The amount's own words and the line it stands under (171's "При записване в срок
    # до 19.12.2025 г."), not a note below it ("Ранно записване до 31.03. - 10% отстъпка"
    # is a discount on the price above, which stays).
    ended = [
        expired_offer(f"{_line_above(text, span)}\n{amount_segment(text, span)}", today or datetime.date.today())
        for span in chosen
    ]
    if all(ended):
        hit(RULE_EXPIRED, f"offered until {ended[0].isoformat()}", contexts[0])
    return hits


# ---------------------------------------------------------------------------
# Rule 5: names
# ---------------------------------------------------------------------------

def name_key(name: Any) -> str:
    return re.sub(r"[^\w]+", " ", str(name or "").casefold()).strip()


def shared_names(names: Iterable[Any], other_names: Iterable[Any]) -> set[str]:
    """Normalized names two institutions both show (case and punctuation ignored)."""
    own = {name_key(n) for n in names if name_key(n)}
    return own & {name_key(n) for n in other_names}


# ---------------------------------------------------------------------------
# Rule 6: re-extraction regression guard
# ---------------------------------------------------------------------------

def _amount_key(row: PriceRow) -> tuple[str, ...]:
    keys = []
    for value in row.amounts:
        try:
            keys.append(str(decimal.Decimal(str(value)).quantize(decimal.Decimal("0.01"))))
        except (decimal.InvalidOperation, ValueError):
            keys.append(str(value))
    return tuple(keys)


# Rule 3 treats a yearly fee and a one-time fee as the same statement; so does rule 6.
_INTERCHANGEABLE_PERIODS = {"ONE_TIME", "YEARLY"}


def _page_states_period(row: PriceRow, page_text: Optional[str]) -> bool:
    """True when each of the row's amounts has its period beside it and no other period.

    Every occurrence that states a period must state this one: "500 евро месечно" for
    food does not make a 500 tuition monthly when "500 евро годишно" is on the page too.
    """
    text = normalize_text(page_text)
    if not (text and row.period and row.amounts):
        return False
    for value in row.amounts:
        stated = [stated_period(text, span)[0] for span in amount_spans(text, value)]
        stated = [families for families in stated if families]
        if not stated or any(families != {row.period} for families in stated):
            return False
    return True


def replacement_regressions(
    published: Iterable[tuple[PriceRow, Optional[str]]],
    proposed: Iterable[tuple[PriceRow, Optional[str]]],
    school_family: str,
) -> list[str]:
    """Why replacing published rows with a re-extraction's rows would lose evidence.

    Each row comes with the current text of its page. A replacement regresses when:

    * a published amount is missing from the proposed rows although its page still
      shows it (525's re-run dropped its registration rows);
    * a published row has a period and every proposed row with that amount has none,
      unless the page states the period next to it (validation then fills it in), so
      a period a person set from the page is not silently lost;
    * a published row has a period and every proposed row with that amount has another
      one, unless the page states the new period next to the amount (199's re-run turned
      "6792 EUR yearly" into "6792 EUR monthly" from a table whose column headers are
      lines away from the amounts).

    An empty list means the replacement may go ahead.
    """
    by_amount: dict[tuple[str, ...], list[tuple[PriceRow, Optional[str]]]] = {}
    for row, text in proposed:
        by_amount.setdefault(_amount_key(row), []).append((row, text))
    published_by_amount: dict[tuple[str, ...], list[tuple[PriceRow, Optional[str]]]] = {}
    for row, text in published:
        published_by_amount.setdefault(_amount_key(row), []).append((row, text))

    reasons = []
    for key, olds in published_by_amount.items():
        matches = by_amount.get(key, [])
        # Counted, not just matched: a €500 registration and a €500 tuition tier are two
        # fees, and one proposed €500 row keeps only one of them. A category change on
        # the same amount (a deposit refiled from tuition to registration) is not a loss.
        if len(matches) < len(olds):
            old, text = olds[0]
            normalized = normalize_text(text)
            shown = min(len(amount_spans(normalized, value)) for value in old.amounts) if normalized else 0
            if shown >= len(olds):
                dropped = len(olds) - len(matches)
                label = ", ".join(f"{row.category} {', '.join(key)}" for row, _ in olds)
                reasons.append(
                    f"drops {dropped} of {len(olds)} rows at {', '.join(key)} ({label}), "
                    "which the page still shows"
                    if len(olds) > 1
                    else f"drops {label}, which the page still shows"
                )
                continue
            # Fewer rows than before, but no more than the page shows: tiers merged into
            # one row. The rows that remain still have to keep the period.
            if not matches:
                continue
        # A period none of the published rows at this amount had needs the page's word for
        # it, whichever published row it replaces: one row that kept its period does not
        # vouch for a sibling that changed.
        old_periods = {old.period for old, _ in olds if old.period}
        introduced = sorted(
            {
                new.period
                for new, new_text in matches
                if old_periods
                and new.period
                and new.period not in old_periods
                and not any({new.period, period} == _INTERCHANGEABLE_PERIODS for period in old_periods)
                and not _page_states_period(new, new_text)
            }
        )
        if introduced:
            reasons.append(
                f"changes the {', '.join(sorted(old_periods))} period of {olds[0][0].category} "
                f"{', '.join(key)} to {', '.join(introduced)}, which the page does not state next "
                "to the amount"
            )
            continue
        for old, _ in olds:
            if old.period and all(new.period is None for new, _ in matches):
                filled = any(
                    finding.rule == RULE_PERIOD_MISSING
                    for new, new_text in matches
                    for finding in check_price_row(new, new_text, school_family)
                )
                if not filled:
                    reasons.append(f"loses the {old.period} period of {old.category} {', '.join(key)}")
                    break
            elif old.period and not any(
                new.period == old.period
                or {new.period, old.period} == _INTERCHANGEABLE_PERIODS
                or _page_states_period(new, new_text)
                for new, new_text in matches
            ):
                reasons.append(f"loses the {old.period} period of {old.category} {', '.join(key)}")
                break
    return reasons
