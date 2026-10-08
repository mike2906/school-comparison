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

import decimal
import re
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from app.scrapers.shared_site_check import describes_level

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

RULE_AMOUNT_NEAR_LABEL = "1_amount_near_label"
RULE_LEVEL = "2_level_fits_school"
RULE_PERIOD_MISSING = "3_period_missing"
RULE_PERIOD_CONFLICT = "3_period_conflict"
RULE_PERIOD_UNREPRESENTABLE = "3_period_unrepresentable"
RULE_DEPOSIT = "4_deposit_not_tuition"
RULE_INSTALLMENT = "4_installment_not_tuition"


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

    @property
    def label(self) -> Optional[str]:
        return (self.plan_name or self.notes or "").strip() or None

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
        )

    @property
    def amounts(self) -> list[Any]:
        if self.amount is not None:
            return [self.amount]
        return [v for v in (self.amount_min, self.amount_max) if v is not None]


def _enum(value: Any) -> Optional[str]:
    value = getattr(value, "value", value)
    return str(value).upper() if value is not None else None


def check_price_row(row: PriceRow, page_text: str | None, school_family: str) -> list[PriceFinding]:
    """Rule 1-4 findings for one price row against its source page text.

    ``school_family`` is ``shared_site_check.level_family`` of the school's level.
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
    if row.label and names_other_level(row.label, school_family):
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
        if single and stored is None:
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
