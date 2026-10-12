"""Turn the model's copied fee lines into price rows, and check model prices against the page text."""

from __future__ import annotations

import datetime
import re
from typing import Any

from app.models.school import School
from app.models.source_page import SourcePage
from app.schemas.extraction import (
    ExtractedPrice,
    FeeLine,
)
from app.scrapers.extraction.price_lines import (
    _is_penalty_price_line,
    _normalize_academic_year,
    _pricing_source_staleness,
)
from app.scrapers.extraction.price_rows import (
    _filter_supported_prices,
)
from app.scrapers.extraction.text_values import (
    _canonical_host,
    _host_matches,
    _sanitize_label,
    _to_optional_float,
)
from app.scrapers.price_evidence import (
    _NUMBER_ONLY_LINE_RE,
    _PRICE_RE,
    _gap,
    _label_key,
    _line_of,
    amount_spans,
    currency_price_starts,
    heading_period,
    label_grades,
    label_names_a_level,
    label_spans,
    normalize_text,
    occurrence_currency,
    period_families,
    period_fits_category,
    period_in_label_parts,
    stated_period,
)

# What a copied fee line's kind and period are in the stored vocabulary. An exam fee is an
# extra; a deposit is paid on registration (Stage 6 keeps it from passing as tuition).
_FEE_KIND_CATEGORY = {
    "tuition": "tuition", "registration": "registration", "deposit": "registration",
    "food": "food", "transport": "transport", "materials": "materials",
    "extended_day": "extended_day", "uniforms": "uniforms", "extracurricular": "extracurricular",
    "camp": "camp", "exam": "extracurricular",
}  # fmt: skip


_FEE_PERIOD = {
    "year": "yearly", "month": "monthly", "term": "term", "semester": "semester",
    "quarter": "quarter", "one_time": "one_time", "unstated": None,
}  # fmt: skip


# The copied lines carry no judgement of their own; the text check below is the gate.
_FEE_LINE_CONFIDENCE = 0.9


_BGN_PER_EUR = 1.95583


def _prices_from_fee_lines(lines: list[FeeLine]) -> list[ExtractedPrice]:
    """Price rows from the lines a model copied off the pages.

    A full price is a row. A payment plan's amount or a discounted price is not a fee of
    its own: it is listed under the full price of the same kind that precedes it. Where a
    kind has no full price at all, a plan's yearly total is the price there is. Sums of
    several fees, penalties, per-day/week/hour amounts and amounts of no fee kind are
    left out. An amount with no currency is in euro (Bulgaria's currency since 2026).

    The labels decide what the model's role leaves open: a price whose label says it is
    paid in several installments is a plan, the slices of a stated total are not fees of
    their own, and of several amounts one table row gives under one label the first is
    the fee.
    """
    lines = _first_amount_per_label_and_line(_without_slices_of_a_total(lines))
    rows: list[ExtractedPrice] = []
    last_full: dict[str, ExtractedPrice] = {}
    unattached: list[tuple[FeeLine, str]] = []
    for line in lines:
        category = _FEE_KIND_CATEGORY.get(line.kind)
        if category is None or line.per not in _FEE_PERIOD or line.role in ("sum", "penalty"):
            continue
        # "Плащане на 2 вноски" copied as a price of its own is a plan of the full price
        # before it, and so is the rate an employer pays (505's "Company < 50 employees"
        # column); with no full price before it, it is the price there is.
        if line.role == "full" and not (
            category in last_full
            and (_names_several_installments(line.label) or _EMPLOYER_RATE_RE.search(line.label))
        ):
            row = _price_from_fee_line(line, category)
            rows.append(row)
            last_full[category] = row
            continue
        target = last_full.get(category)
        if target is None:
            unattached.append((line, category))
            continue
        currency = "EUR" if line.currency == "unstated" else line.currency
        terms = target.discounts if line.role == "discounted" else target.installments
        terms.append(f"{line.label}: {line.amount:g} {currency}")
    # No full price of the kind: the yearly total of a plan stands in. A total whose label
    # says "in 2 installments" is a variant of the one before it (404's "6 750 евро /
    # година" was followed by "Възможности за плащане на 2 вноски – 6 547,5 евро").
    stand_in: dict[str, ExtractedPrice] = {}
    for line, category in unattached:
        if category in last_full or line.role == "discounted" or line.per != "year":
            continue
        whole = stand_in.get(category)
        if (
            whole is not None
            and _names_several_installments(line.label)
            and not _names_several_installments(whole.plan_name)
        ):
            currency = "EUR" if line.currency == "unstated" else line.currency
            whole.installments.append(f"{line.label}: {line.amount:g} {currency}")
            continue
        stand_in[category] = _price_from_fee_line(line, category)
        rows.append(stand_in[category])
    # A page that still shows the lev beside the euro states one fee, not two.
    euro = [(row.category, row.amount) for row in rows if row.currency == "EUR"]
    rows = [
        row
        for row in rows
        if row.currency != "BGN"
        or not any(
            category == row.category and abs(row.amount / _BGN_PER_EUR - amount) <= 0.005 * amount
            for category, amount in euro
        )
    ]
    return _without_restated_fees(rows)


# "на 2 вноски", "10 installments", "на равни месечни вноски", "разсрочено плащане": the
# price of paying in parts. "1 вноска" and "1 installment" are the fee paid in one go.
_SEVERAL_INSTALLMENTS_RE = re.compile(
    r"(?<![\d.,])(?:[2-9]|1\d)\s*(?:x\s*)?(?:равни\s+)?(?:месечни\s+)?(?:вноски|плащания|instal?lments|payments)"
    r"|\b(?:две|три|четири|пет|шест|девет|десет|two|three|four|nine|ten)\s+(?:равни\s+)?(?:месечни\s+)?"
    r"(?:вноски|плащания|instal?lments|payments)"
    r"|равни\s+(?:месечни\s+)?вноски|разсрочен|\bна\s+вноски\b|\bin\s+instal?lments\b",
    re.IGNORECASE,
)


_EMPLOYER_RATE_RE = re.compile(r"\bcompan(?:y|ies)\b|\bemployers?\b|работодател|\bфирм[аи]\b|entreprise", re.IGNORECASE)


def _names_several_installments(label: str | None) -> bool:
    return bool(_SEVERAL_INSTALLMENTS_RE.search(str(label or "")))


def _without_slices_of_a_total(lines: list[FeeLine]) -> list[FeeLine]:
    """Leave out the parts of a total in which tuition is the smaller half.

    533 and 633 split one yearly charge into "Такса обучение" 2960, a research programme
    2960 and two smaller parts, and give "Общо" 7400 below: the 2960 is not what the
    school costs. A total of tuition and optional extras (food, transport) is mostly
    tuition, and its parts stay. In a table the parts of a total are the lines that share
    its column heading, one " / " part of the label.
    """

    def label_parts(line: FeeLine) -> set[str]:
        return {_label_key(part) for part in line.label.split(" / ")}

    dropped: set[int] = set()
    for total in lines:
        if total.role != "sum" or total.amount <= 0:
            continue
        others = [
            index
            for index, line in enumerate(lines)
            if line.role not in ("sum", "penalty") and line.currency == total.currency
        ]
        columns = label_parts(total) if " / " in total.label else {None}
        for column in columns:
            parts = [index for index in others if column is None or column in label_parts(lines[index])]
            if abs(sum(lines[index].amount for index in parts) - total.amount) > 0.005 * total.amount:
                continue
            tuition = sum(lines[index].amount for index in parts if lines[index].kind == "tuition")
            if 0 < tuition < total.amount / 2:
                dropped.update(parts)
    return [line for index, line in enumerate(lines) if index not in dropped]


def _first_amount_per_label_and_line(lines: list[FeeLine]) -> list[FeeLine]:
    """Of several tuition prices one line of the page gives under one label, the first.

    A table row gives the fee and then its variants in further columns. When the copied
    labels do not tell the columns apart, the later amounts would be stored as the same
    fee at other prices (505's company-paid +5% and +10% rates under the family price's
    label). Other kinds are left alone: "от 10 до 20 EUR" for a club is a range.
    """
    kept: list[FeeLine] = []
    first: dict[tuple[Any, ...], list[FeeLine]] = {}
    for line in lines:
        if line.role != "full" or line.kind != "tuition":
            kept.append(line)
            continue
        earlier = first.setdefault((line.kind, line.currency, line.per, _label_key(line.label)), [])
        if any(
            other.amount != line.amount
            and (
                amount_spans(normalize_text(line.quote), other.amount)
                or amount_spans(normalize_text(other.quote), line.amount)
            )
            for other in earlier
        ):
            continue
        earlier.append(line)
        kept.append(line)
    return kept


# Words that say a label is a fee or how it is paid, and nothing about which fee.
_GENERIC_FEE_LABEL_RE = re.compile(
    r"\b(?:такс\w*|цена\w*|годишн\w*|обучение\w*|учебна\w*|година\w*|плащане\w*|заплащане\w*"
    r"|еднократн\w*|пълна\w*|размер\w*|tuition|fees?|annual|yearly|payment|full|total|price|year"
    r"|for|the|of|на|за|в)\b"
)


def _label_is_generic(label: str | None) -> bool:
    return not _GENERIC_FEE_LABEL_RE.sub(" ", _label_key(label)).strip()


def _label_adds_words(longer: str | None, shorter: str | None) -> bool:
    """The longer label is the shorter one's " / " parts, as written, plus fee wording.

    Shared words are not enough: "Такса за чуждестранни ученици" and "Такса за български
    ученици, идващи от чуждестранни училища" are two fees at one price (301). Nor may
    the added words say which fee: "Grade 1 tuition" and "Grade 1 tuition full day" are
    two plans.
    """
    whole = f" {_label_key(longer)} "
    parts = [_label_key(part) for part in str(shorter or "").split(" / ")]
    if not parts or not all(part and f" {part} " in whole for part in parts):
        return False
    for part in parts:
        whole = whole.replace(f" {part} ", "  ")
    return _label_is_generic(whole.replace("/", " "))


def _without_restated_fees(rows: list[ExtractedPrice]) -> list[ExtractedPrice]:
    """One row for a fee the pages state more than once.

    The same amount of the same kind is one fee when the labels differ only in grammar
    (the page's two language versions, a summary and its table), when one label is the
    other with words added that name no other grades (214: "1. - 4. клас / Плащане на
    пълна такса" and the same under its table's title), or when one of them only says
    "fee" (569: "Годишна такса обучение" beside "1-4 клас / Годишна такса обучение").
    Two bands at one price each name their own level and stay.
    """
    kept: list[ExtractedPrice] = []
    for row in rows:
        stems, generic = _label_stems(row.plan_name), _label_is_generic(row.plan_name)
        same = next(
            (
                index
                for index, other in enumerate(kept)
                if (other.category, other.amount, other.currency) == (row.category, row.amount, row.currency)
                and (
                    generic
                    or _label_is_generic(other.plan_name)
                    or stems == _label_stems(other.plan_name)
                    or (
                        (
                            _label_adds_words(row.plan_name, other.plan_name)
                            or _label_adds_words(other.plan_name, row.plan_name)
                        )
                        and label_grades(row.plan_name) == label_grades(other.plan_name)
                    )
                )
            ),
            None,
        )
        if same is None:
            kept.append(row)
            continue
        other = kept[same]
        # The label that says which fee is the one to keep; between two that do, the shorter.
        if generic:
            prefer_new = False
        elif _label_is_generic(other.plan_name):
            prefer_new = True
        else:
            prefer_new = len(stems) < len(_label_stems(other.plan_name))
        winner, loser = (row, other) if prefer_new else (other, row)
        winner.period = winner.period or loser.period
        winner.academic_year = winner.academic_year or loser.academic_year
        winner.installments = winner.installments or loser.installments
        winner.discounts = winner.discounts or loser.discounts
        kept[same] = winner
    return kept


def _label_stems(label: str | None) -> frozenset[str]:
    """Word stems of a label: "Месечната такса" and "Месечна такса" are one label."""
    return frozenset(word[:5] for word in re.findall(r"[^\W_]+", str(label or "").casefold()))


def _fee_label(text: str | None, max_len: int, *, cut: bool = False) -> str | None:
    """A fee label kept whole; one over the length is cut when asked, else dropped.

    The general text cleaner keeps the first of a comma-separated list, which cut
    "Детска Ясла ,,Йор Кидс“ / Целодневен престой" down to its heading (542).
    """
    label = " ".join(str(text or "").split())
    if cut and len(label) > max_len:
        label = label[:max_len].rsplit(" ", 1)[0].strip(" /")
    return _sanitize_label(label, max_len=max_len)


def _price_from_fee_line(line: FeeLine, category: str) -> ExtractedPrice:
    return ExtractedPrice(
        category=category,
        amount=line.amount,
        currency="EUR" if line.currency == "unstated" else line.currency,
        period=_FEE_PERIOD[line.per],
        plan_name=_fee_label(line.label, 100, cut=True),
        # The part of the label that says for whom, kept whole: the scope rules read
        # it, and a long label is cut before it gets there.
        age_group=_fee_label(
            next((part for part in line.label.split(" / ") if label_names_a_level(part)), None),
            50,
            cut=True,
        ),
        academic_year=line.academic_year,
        confidence=_FEE_LINE_CONFIDENCE,
    )


def _label_is_on_page(label: str | None, page_text: str | None) -> bool:
    """Some part of a copied label ("row / column heading") is written on the page."""
    text = normalize_text(page_text)
    parts = [part.strip() for part in str(label or "").split(" / ") if len(part.strip()) >= 3]
    return any(label_spans(text, part) for part in parts)


def _source_blocks(text: str) -> list[tuple[str | None, str]]:
    """(source URL, text) per ``--- SOURCE ---`` block; one block for unmarked text."""
    headers = list(
        re.finditer(r"^\s*---\s*SOURCE:\s*(.*?)\s*---\s*$", text or "", flags=re.IGNORECASE | re.MULTILINE)
    )
    if not headers:
        return [(None, text or "")]
    blocks = []
    for index, header in enumerate(headers):
        end = headers[index + 1].start() if index + 1 < len(headers) else len(text)
        blocks.append((header.group(1).strip() or None, text[header.end() : end]))
    return blocks


_PAGE_ACADEMIC_YEAR_RE = re.compile(r"20\d{2}\s*[-/–]\s*(?:20)?\d{2}")


_FEE_WORDS = r"такс|\bцен[аи]\b|\bfees?\b|tuition|\bprices?\b"


# An academic year on a line that is about fees ("Такси за учебната 2026/2027 г.").
_FEE_YEAR_LINE_RE = re.compile(
    rf"^.*(?:{_FEE_WORDS}).*{_PAGE_ACADEMIC_YEAR_RE.pattern}"
    rf"|^.*{_PAGE_ACADEMIC_YEAR_RE.pattern}.*(?:{_FEE_WORDS})",
    re.MULTILINE,
)


_CURRENCY_OR_FEE_RE = re.compile(rf"€|\beur|евро|лв|\bbgn|лева|{_FEE_WORDS}")


def _filter_model_prices(prices: list[ExtractedPrice], text: str) -> list[ExtractedPrice]:
    """Keep a model's price rows that the page text bears out, corrected by that text.

    The model read the whole page, so its category, plan and age group stand. The text
    decides the rest, by the same reading Stage 6 uses to gate publication
    (``price_evidence``): the amount must be on a page in the row's currency; the period
    is whatever is written next to the amount (or in its table's header row), or none;
    the academic year is kept only when that page names it, and taken from the page when
    the model gave none and the page names exactly one, on a line about fees. A yearless
    row needs a page that is not a dated old fee list. Rows for late-payment penalties, a "total fee" sum and per-day/week/hour
    amounts are dropped.

    The keyword extractor's rows go through :func:`_filter_supported_prices` instead:
    they have no reading of their own, so there the line and heading keywords decide.
    """
    if not prices or not text:
        return []
    staleness = _pricing_source_staleness(text)
    blocks = [
        (normalize_text(block), staleness.get(url, staleness.get(None, False)))
        for url, block in _source_blocks(text)
    ]

    refined: list[ExtractedPrice] = []
    for price in prices:
        amount = _to_optional_float(price.amount)
        if amount is None:
            continue
        currency = (price.currency or "BGN")[:3].upper()
        year = _normalize_academic_year(price.academic_year)
        label = price.plan_name or price.age_group or price.notes
        if period_families(f"{price.plan_name or ''} {price.age_group or ''}".casefold()) == (set(), True):
            continue  # a per-day/week/hour fee has no period the schema can hold
        supported: list[tuple[bool, str | None]] = []  # (page names the year, period)
        for block, stale in blocks:
            spans = [
                span
                for span in amount_spans(block, amount)
                if _amount_is_a_fee_in(block, span, currency)
            ]
            if not spans:
                continue
            labels = label_spans(block, label)
            if labels:
                spans = [min(spans, key=lambda span: min(_gap(span, found) for found in labels))]
            stated = [_period_written_for(block, span) for span in spans]
            if all(unrepresentable for _, unrepresentable in stated):
                continue
            families = [found for found, _ in stated]
            agreed = families[0] if all(found == families[0] for found in families) else set()
            period = next(iter(agreed)).lower() if len(agreed) == 1 else None
            if period and not period_fits_category(price.category, period):
                # "Еднократно плащане" under a yearly fee (517, 564): the heading may say.
                headed = {heading_period(block, span, skip=frozenset({period.upper()})) for span in spans}
                period = headed.pop().lower() if len(headed) == 1 and None not in headed else period
            if period is None:
                period = period_in_label_parts(block, label)
            page_years = {
                _normalize_academic_year(found) for found in _PAGE_ACADEMIC_YEAR_RE.findall(block)
            } - {None}
            if year in page_years:
                page_year = price.academic_year
            elif not year and len(page_years) == 1 and _FEE_YEAR_LINE_RE.search(block):
                # The model left the year out; the page names exactly one, with its fees.
                page_year = next(iter(page_years))
            else:
                page_year = None
            if page_year is None and stale:
                continue
            if page_year is not None and _academic_year_is_over(page_year):
                continue  # a fee list of a past year is not this year's price
            supported.append((page_year, period))
        if not supported:
            continue
        # A page that names the year is the better witness.
        page_year, period = max(supported, key=lambda item: item[0] is not None)
        normalized = price.model_copy(deep=True)
        normalized.period = period if period_fits_category(price.category, period) else None
        normalized.academic_year = page_year
        refined.append(normalized)
    return _told_apart_by_heading(refined, text)


def _told_apart_by_heading(rows: list[ExtractedPrice], text: str) -> list[ExtractedPrice]:
    """Rows that share a label at different prices get the heading each one stands under.

    542 lists "Целодневен престой" twice, under "Месечни такси в Частна Детска Ясла" and
    under "Месечни такси в Частна Детска Градина": copied without their headings the
    nursery's prices read as the kindergarten's. The heading says for whom, so it goes
    where the label's own "for whom" part would. The rows are left as they are unless
    each price has a heading of its own that names a level.
    """
    groups: dict[tuple[Any, ...], list[ExtractedPrice]] = {}
    for row in rows:
        if row.plan_name and row.amount is not None:
            groups.setdefault((row.category, row.currency, _label_key(row.plan_name)), []).append(row)
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    for group in groups.values():
        amounts = {row.amount for row in group}
        if len(amounts) < 2 or any(row.age_group for row in group):
            continue
        headings = [_fee_label(_heading_above_labelled_amount(lines, row), 50, cut=True) for row in group]
        if (
            None in headings
            or len(set(headings)) < len(amounts)
            or not all(label_names_a_level(heading) for heading in headings)
        ):
            continue
        for row, heading in zip(group, headings):
            row.age_group = heading
    return rows


def _heading_above_labelled_amount(lines: list[str], row: ExtractedPrice) -> str | None:
    """The one line without a price above the lines that hold the row's label and amount."""
    found: set[str] = set()
    for index, line in enumerate(lines):
        normalized = normalize_text(line)
        if not (amount_spans(normalized, row.amount) and label_spans(normalized, row.plan_name)):
            continue
        heading = next(
            (
                above
                for above in reversed(lines[:index])
                if not above.startswith("---") and not _PRICE_RE.search(above)
                and not _NUMBER_ONLY_LINE_RE.match(above)
            ),
            None,
        )
        if heading is None:
            return None
        found.add(heading.rstrip(" :"))
    return found.pop() if len(found) == 1 else None


def _table_header(block: str, span: tuple[int, int]) -> str | None:
    """The first row of the pipe table the amount sits in, when that row holds no price."""
    header = None
    for line in reversed(block[: span[0]].splitlines()[:-1]):
        if "|" not in line:
            break
        header = line
    return None if header is None or currency_price_starts(header) else header


def _academic_year_is_over(year: str | None, *, today: datetime.date | None = None) -> bool:
    """The academic year ended before the current one began ("2024/2025" in October 2026).

    Last year's list is kept: until a school publishes the new fees it is the best
    there is, and it carries its year.
    """
    normalized = _normalize_academic_year(year)
    if not normalized:
        return False
    today = today or datetime.date.today()
    current_start = today.year if today.month >= 9 else today.year - 1
    return int(normalized.split("/")[1]) < current_start


def _period_written_for(block: str, span: tuple[int, int]) -> tuple[set[str], bool]:
    """Period wording for one amount; with none, its column header's, else its heading's."""
    families, unrepresentable = stated_period(block, span)
    if families or unrepresentable:
        return families, unrepresentable
    header = _table_header(block, span) if "|" in _line_of(block, span) else None
    cell = _header_cell(block, span, header) if header is not None else None
    if cell is not None:
        families, unrepresentable = period_families(cell)
        if families or unrepresentable:
            return families, unrepresentable
    headed = heading_period(block, span)
    return ({headed} if headed else set()), False


def _header_cell(block: str, span: tuple[int, int], header: str) -> str | None:
    """The header cell above the amount's own column, when the two rows line up."""
    line = _line_of(block, span)
    line_start = block.rfind("\n", 0, span[0]) + 1
    cells = header.split("|")
    if len(cells) != line.count("|") + 1:
        return None
    return cells[line[: span[0] - line_start].count("|")]


def _currencies_named(text: str) -> set[str]:
    return {
        code
        for code, pattern in (("EUR", r"€|\beur|евро"), ("BGN", r"лв|\bbgn|лева"))
        if re.search(pattern, text)
    }


def _amount_is_a_fee_in(block: str, span: tuple[int, int], currency: str) -> bool:
    """The occurrence is a price in ``currency`` and not a penalty or a sum of fees."""
    line = _line_of(block, span)
    written = occurrence_currency(block, span)
    bare = written is None and (
        _NUMBER_ONLY_LINE_RE.match(line) is not None
        or ("|" in line and _bare_cell_is_a_price_in(block, span, currency))
    )
    if written != currency and not bare:
        return False
    return not _is_penalty_price_line(line) and "total fee" not in line


def _bare_cell_is_a_price_in(block: str, span: tuple[int, int], currency: str) -> bool:
    """A table cell holding a number with no currency of its own is a price in ``currency``.

    Its column header decides when it names a currency ("Plan | EUR | BGN"). Otherwise
    the table must be about fees and name no currency but the row's: a class-size table
    is not a price list, and a two-currency table with unnamed columns is not evidence
    for either.
    """
    line = _line_of(block, span)
    header = _table_header(block, span) or ""
    cell = _header_cell(block, span, header) if header else None
    in_column = _currencies_named(cell or "")
    if in_column:
        return currency in in_column
    table = f"{line}\n{header}"
    named = _currencies_named(table)
    if not named:
        # A fee table that names no currency is in euro (Bulgaria's currency since 2026).
        return currency == "EUR" and _CURRENCY_OR_FEE_RE.search(table) is not None
    return named == {currency}


def _find_supporting_price_source_url(
    school: School,
    pages: list[SourcePage],
    price: ExtractedPrice,
    *,
    model_rows: bool = False,
) -> str | None:
    amount = _to_optional_float(price.amount)
    if amount is None:
        return None

    school_host = _canonical_host(school.website_url)
    candidate_pages = [
        page
        for page in pages
        if page.raw_markdown and _host_matches(_canonical_host(page.source_url), school_host)
    ] or [page for page in pages if page.raw_markdown]
    category_priority = {"pricing": 0, "admission": 1, "contact": 2}
    candidate_pages.sort(
        key=lambda page: (
            category_priority.get((page.page_category or "").lower(), 9),
            len(page.source_url or ""),
        )
    )

    for page in candidate_pages:
        page_text = page.raw_markdown or ""
        page_context = f"--- SOURCE: {page.source_url} ---\n{page_text}"
        supported = (
            _filter_model_prices([price], page_context)
            if model_rows
            else _filter_supported_prices([price], page_context)
        )
        if any(
            row.category == price.category
            and row.period == price.period
            and (
                not price.academic_year
                or _normalize_academic_year(row.academic_year)
                == _normalize_academic_year(price.academic_year)
            )
            for row in supported
        ):
            return page.source_url
    return None
