"""Deterministic price extraction: amounts, currencies, categories, periods and age groups read line by line."""

from __future__ import annotations

import datetime
import re
from typing import Any

from app.schemas.extraction import (
    ExtractedPrice,
    PriceExtractionOutput,
)
from app.utils.academic_year import normalize_academic_year

_PRICE_LINE_AMOUNT_RE = re.compile(
    r"(?:(€|eur|euro|евро|лв\.?|лева?|bgn)\s*([\d][\d\s.,]*))|(?:([\d][\d\s.,]*)\s*(€|eur|euro|евро|лв\.?|лева?|bgn))",
    flags=re.IGNORECASE,
)


_PRICE_LINE_AMOUNT_LATIN_RE = re.compile(
    r"(?:(€|eur|euro|лв\.?|bgn)\s*([\d][\d\s.,]*))|(?:([\d][\d\s.,]*)\s*(€|eur|euro|лв\.?|bgn))",
    flags=re.IGNORECASE,
)


_OPTIONAL_PRICING_SECTION_TOKENS = (
    "other charges",
    "other fees",
    "услуги по желание",
    "по желание на родителите",
    "допълнително заплащане",
    "допълнителни услуги",
    "допълнителни такси",
    "additional services",
    "additional fees",
    "additional charges",
    "optional services",
)


def _clean_price_line(raw: str) -> str:
    line = re.sub(r"\[(.*?)\]\([^)]*\)", r"\1", raw or "")
    line = re.sub(r"^[#>*\-\s`_]+", "", line)
    line = line.replace("**", "").replace("__", "")
    line = re.sub(r"\s+", " ", line).strip(" -:\t")
    return line.strip()


def _parse_price_amount_token(raw: str) -> float | None:
    token = re.sub(r"[^0-9,.\s]", "", str(raw or "")).replace("\xa0", " ").strip()
    if not token:
        return None

    token = re.sub(r"\s+", "", token)
    if not token:
        return None

    if "," in token and "." in token:
        if token.rfind(",") > token.rfind("."):
            token = token.replace(".", "").replace(",", ".")
        else:
            token = token.replace(",", "")
    elif token.count(",") > 1:
        token = token.replace(",", "")
    elif token.count(".") > 1:
        token = token.replace(".", "")
    elif "," in token:
        head, tail = token.split(",", 1)
        token = head + tail if len(tail) == 3 else f"{head}.{tail}"
    elif "." in token:
        head, tail = token.split(".", 1)
        token = head + tail if len(tail) == 3 else f"{head}.{tail}"

    try:
        return float(token)
    except ValueError:
        return None


# A full Bulgarian currency word written directly against its amount ("500евро",
# "1200лева"), and not itself the start of a longer word.
_GLUED_BG_CURRENCY_WORD_RE = re.compile(r"(\d)(евро|лева)(?![^\W\d_])", re.IGNORECASE)


def _space_glued_currency_words(text: str) -> str:
    """Separate a glued Bulgarian currency word from its amount, for the LLM prompt only.

    The model reliably misses "500евро" yet reliably reads "500 евро". Only the full words
    are rewritten: abbreviations such as "лв" already work glued, so they are left alone.
    Callers must keep using the original text for deterministic parsing and evidence
    checks, which deliberately do not treat these words as currencies.
    """
    return _GLUED_BG_CURRENCY_WORD_RE.sub(r"\1 \2", text or "")


def _extract_price_amount_currency(
    value: str, *, allow_cyrillic_currency_words: bool = True
) -> tuple[float | None, str | None]:
    pattern = (
        _PRICE_LINE_AMOUNT_RE
        if allow_cyrillic_currency_words
        else _PRICE_LINE_AMOUNT_LATIN_RE
    )
    match = pattern.search(value or "")
    if not match:
        return None, None
    currency_token = match.group(1) or match.group(4) or ""
    amount_token = match.group(2) or match.group(3) or ""
    amount = _parse_price_amount_token(amount_token)
    if amount is None:
        return None, None
    currency_normalized = currency_token.lower().strip(". ")
    if currency_normalized in {"€", "eur", "euro", "евро"}:
        return amount, "EUR"
    if currency_normalized in {"лв", "лев", "лева", "bgn"}:
        return amount, "BGN"
    return amount, None


def _detect_price_category(value: str, *, allow_generic_heading: bool = False) -> str | None:
    lowered = value.lower()
    if any(token in lowered for token in ("late fee", "late-payment", "late payment", "просроч")):
        return None
    if "обучение по " in lowered:
        return "extracurricular"
    if "обуч" in lowered or "образователни услуги" in lowered or "tuition" in lowered:
        return "tuition"
    if any(
        token in lowered
        for token in ("school bus", "bus service", "автобус", "транспорт", "transport")
    ):
        return "transport"
    if any(
        token in lowered
        for token in (
            "образователни ресурси",
            "учебниц",
            "консуматив",
            "materials",
            "language books",
            "foreign language books",
        )
    ):
        return "materials"
    if any(token in lowered for token in ("храна", "food", "meal", "meals", "lunch", "snack", "закуска", "обяд")):
        return "food"
    if re.search(r"\b(?:месечна|годишна)\s+такса\b", lowered):
        return "tuition"
    if "униформ" in lowered or "uniform" in lowered:
        return "uniforms"
    if any(
        token in lowered
        for token in (
            "регистрац",
            "registration",
            "admission",
            "enrollment",
            "кандидатств",
            "application fee",
            "deposit",
            "депозит",
            "capital fee",
            "новопостъп",
        )
    ):
        return "registration"
    if "лагер" in lowered or "camp" in lowered:
        return "camp"
    if any(
        token in lowered
        for token in (
            "извънклас",
            "extracurricular",
            "допълнителни дейности",
            "additional activities",
            "курс",
            "course",
            "урок",
            "уроци",
            "lesson",
            "lessons",
            "занимания",
            "training",
            "trainings",
            "отбор",
            "teams",
            "музикален инструмент",
            "танц",
            "плуване",
            "тенис",
            "футбол",
            "балет",
            "гимнастика",
            "volleyball",
            "english lessons",
        )
    ):
        return "extracurricular"
    if "удължен" in lowered or "extended day" in lowered:
        return "extended_day"
    if allow_generic_heading and any(token in lowered for token in _OPTIONAL_PRICING_SECTION_TOKENS):
        return None
    if allow_generic_heading and re.search(r"\b(такси|fees?)\b", lowered):
        return "tuition"
    return None


def _is_price_category_heading(value: str) -> bool:
    lowered = value.casefold()
    return not any(
        token in lowered
        for token in (
            "включ",
            "изключ",
            "не е",
            "не e",
            "заплаща",
            "included",
            "excluded",
            "does not include",
        )
    )


def _starts_new_price_section(value: str) -> bool:
    """Identify a new fee section, excluding table labels and explanatory prose."""
    return value.lstrip().startswith("#") or bool(
        re.match(
            r"^(?:tuition|school|food|meal|transport)\s+fees?\s+for\b|"
            r"^такс\w*\s+за\s+(?:обучение|храна|транспорт)\b",
            value,
            flags=re.IGNORECASE,
        )
    )


def _detect_price_age_group(value: str) -> str | None:
    lowered = value.lower()
    if "предучилищ" in lowered:
        return "preschool"
    range_match = re.search(r"(\d+\s*[-–]\s*\d+\s*клас)", value, flags=re.IGNORECASE)
    if range_match:
        return range_match.group(1)
    multi_grade_match = re.search(r"(\d(?:\s*,\s*\d+)+(?:\s*и\s*\d+)?\s*клас)", value, flags=re.IGNORECASE)
    if multi_grade_match:
        digits = [int(part) for part in re.findall(r"\d+", multi_grade_match.group(1))]
        if digits:
            return f"{min(digits)}-{max(digits)} клас"
    single_grade_match = re.search(r"(\d+\s*клас)", value, flags=re.IGNORECASE)
    if single_grade_match:
        return single_grade_match.group(1)
    return None


_INSTALLMENT_MULTIPLIER_RE = re.compile(
    r"\b\d{1,2}\s*[×xXхХ]\s*[\d.,\s]*\s*(?:€|eur|euro|лв|bgn)",
    flags=re.IGNORECASE,
)


_PAYMENT_SCHEDULE_FREQUENCY_RE = re.compile(
    r"\b(?:monthly|quarterly)\s+(?:paid|payment|instalments?|installments?)|"
    r"\b(?:paid|payable)\s+(?:monthly|quarterly)\b|"
    r"\b(?:instalments?|installments?|вноск\w*)\b|"
    r"\bежемесечн\w*\s+(?:плащ\w*|вноск\w*)\b",
    flags=re.IGNORECASE,
)


def _detect_explicit_price_period(value: str) -> str | None:
    lowered = value.casefold()
    if re.search(
        r"\b(?:one[- ]time|single)\b.{0,30}\b(?:fee|charge|deposit)\b|"
        r"\b(?:fee|charge|deposit)\b.{0,30}\bone[ -]time\b|"
        r"\bеднократн\w*(?:\s+\w+){0,2}\s+такс\w*\b",
        lowered,
    ):
        return "one_time"
    if re.search(
        r"\b(?:per|each)\s+semester\b|\bsemester(?:ly)?\s+(?:fee|tuition)\b|"
        r"\b(?:на|за)\s+(?:един\s+)?семестър\b|\bсеместриална\s+такса\b",
        lowered,
    ):
        return "semester"
    if (
        re.search(r"\b(?:per|each)\s+quarter\b", lowered)
        and not re.search(r"\b(?:paid|payable)\s+(?:per|each)\s+quarter\b", lowered)
    ) or re.search(
        r"\bquarterly\s+(?:(?:tuition|school|meal|food|transport)\s+)?(?:fee|charge)\b|"
        r"\b(?:на|за)\s+(?:едно\s+)?тримесечие\b|\bтримесечна\s+такса\b",
        lowered,
    ):
        return "quarter"
    if re.search(
        r"\b(?:per|each)\s+term\b|\bterm(?:ly)?\s+(?:fee|tuition)\b|"
        r"\b(?:на|за)\s+(?:един\s+)?(?:учебен\s+)?срок\b|\bтакса\s+за\s+срок\b",
        lowered,
    ):
        return "term"
    if (
        re.search(r"\b(?:annual|yearly)\s+(?:(?:tuition|school)\s+)?fee\b", lowered)
        or re.search(r"\bannual\s+tuition\b", lowered)
        or re.search(r"\bгодишн\w*\s+(?:учебн\w*\s+)?такс\w*\b", lowered)
        or re.search(r"\bper\s+(?:(?:academic|school)\s+)?year\b", lowered)
        or re.search(r"\bfor\s+(?:one|the\s+(?:whole|entire))\s+(?:academic|school)\s+year\b", lowered)
        or re.search(r"\bза\s+(?:една|цялата)\s+уч(?:ебна|\.)?\s*година\b", lowered)
        or re.search(r"\bна\s+година\b", lowered)
        or re.search(r"/\s*(?:per\s+)?(?:year|година)\b", lowered)
    ):
        return "yearly"
    if (
        re.search(r"\bmonthly\s+(?:tuition|fee)\b", lowered)
        or re.search(r"\b(?:tuition|fee)\b.{0,30}\bper\s+month\b", lowered)
        or re.search(r"\bмесечн\w*\s+такс\w*\b", lowered)
        or re.search(r"\bмесечно\s*(?:половин|целодневно)", lowered)
        or re.search(r"\bтакс\w*\b(?:(?!плащ|внас).){0,30}\bна\s+месец\b", lowered)
        or re.search(r"\bтакс\w*\s+за\s+месец\b", lowered)
        or re.search(r"/\s*(?:per\s+)?(?:month|месец)\b", lowered)
    ):
        return "monthly"
    if (
        re.search(r"\bсе\s+заплаща\s+еднократно\b", lowered)
        and not re.search(r"\bили\b.{0,40}\bвноск\w*\b", lowered)
        and not re.search(r"\bвсяка\s+учебна\s+година\b", lowered)
    ):
        return "one_time"
    return None


def _detect_price_period(
    value: str,
    default_period: str | None = None,
) -> str | None:
    explicit = _detect_explicit_price_period(value)
    if explicit is not None:
        return explicit
    if _PAYMENT_SCHEDULE_FREQUENCY_RE.search(value):
        if (
            default_period is not None
            and re.search(r"\b(?:paid|payable)\s+(?:monthly|quarterly)\b", value, re.IGNORECASE)
            and not re.search(r"\b(?:instalments?|installments?|payments?|вноск\w*)\b", value, re.IGNORECASE)
        ):
            return default_period
        return None
    # Callers only supply a default for an explicit period heading in the current
    # fee section. Academic years, fee categories, amounts, and installment counts
    # are deliberately not period evidence.
    return default_period


def _section_period_applies_to_price_line(
    value: str,
    section_heading: str | None,
    payment_plan_heading: bool,
) -> bool:
    if not payment_plan_heading:
        return True
    # A mixed table can explicitly label its first amount as the full fee while
    # also showing installment alternatives. The section's stated period applies
    # to that total, but not to standalone installment amounts.
    return (
        len(_PRICE_LINE_AMOUNT_RE.findall(value)) > 1
        and any(
            token in (section_heading or "").casefold()
            for token in ("full fee", "пълна такса")
        )
    )


def _is_penalty_price_line(value: str) -> bool:
    lowered = (value or "").casefold()
    return any(
        token in lowered
        for token in (
            "late fee",
            "late-payment",
            "late payment",
            "surcharge for any payments made after",
            "такса за просроч",
            "неустойк",
        )
    )


def _period_on_following_line(lines: list[str], line_index: int) -> str | None:
    if line_index + 1 >= len(lines):
        return None
    following = lines[line_index + 1]
    if _extract_price_amount_currency(following)[0] is not None:
        return None
    if _detect_price_category(following, allow_generic_heading=True) is not None:
        return None
    return _detect_explicit_price_period(following)


def _iter_price_line_signals(text: str) -> list[dict[str, Any]]:
    lines = [_clean_price_line(raw) for raw in re.split(r"[\n\r]+", text) if _clean_price_line(raw)]
    if not lines:
        return []

    signals: list[dict[str, Any]] = []
    current_category: str | None = None
    current_age_group: str | None = None
    current_academic_year: str | None = None
    current_period: str | None = None
    current_payment_plan = False
    current_heading: str | None = None
    current_source_url: str | None = None
    source_staleness = _pricing_source_staleness(text)

    for line_index, line in enumerate(lines):
        lowered = line.lower()
        if lowered.startswith("source:"):
            current_source_url = re.sub(
                r"\s*---\s*$", "", line.split(":", 1)[1]
            ).strip() or None
            current_category = None
            current_age_group = None
            current_academic_year = None
            current_period = None
            current_payment_plan = False
            current_heading = None
            continue
        line_amount, line_currency = _extract_price_amount_currency(line)
        academic_year_match = re.search(r"(20\d{2}\s*[-/]\s*20\d{2})", line)
        if academic_year_match:
            current_academic_year = academic_year_match.group(1).replace(" ", "")

        if line_amount is None and any(token in lowered for token in _OPTIONAL_PRICING_SECTION_TOKENS):
            current_category = None
            current_age_group = None
            current_period = None
            current_payment_plan = False
            current_heading = None
            continue

        if line_amount is None and current_period == "monthly" and lowered.startswith("еднократно плащане"):
            current_period = None

        line_category = _detect_price_category(line, allow_generic_heading=True)
        if line_category is not None and line_amount is None and _is_price_category_heading(line):
            if line_category != current_category or _starts_new_price_section(line):
                current_period = None
                current_payment_plan = False
            current_category = line_category
            current_heading = line
            detected_age_group = _detect_price_age_group(line)
            if detected_age_group is not None:
                current_age_group = detected_age_group

        explicit_period = _detect_explicit_price_period(line)
        if explicit_period is not None and line_amount is None:
            previous_amount = (
                _extract_price_amount_currency(lines[line_index - 1])[0]
                if line_index > 0 else None
            )
            if previous_amount is None or line_category is not None:
                current_period = explicit_period
                current_payment_plan = False
        elif (
            line_amount is None
            and line_category is not None
            and _PAYMENT_SCHEDULE_FREQUENCY_RE.search(line)
        ):
            current_payment_plan = True

        installment_match = re.search(r"\b(?:на|за)\s*(\d+)\s*вноск", lowered)
        if installment_match and int(installment_match.group(1)) != 1:
            continue

        if line_amount is None:
            continue

        previous_line = lines[line_index - 1] if line_index > 0 else ""
        previous_amount, _ = _extract_price_amount_currency(previous_line)
        semantic_line = (
            f"{previous_line} {line}"
            if previous_line and previous_amount is None and len(previous_line) <= 120
            else line
        )
        if _is_penalty_price_line(semantic_line):
            continue
        row_category = _detect_price_category(semantic_line) or current_category
        if row_category is None:
            continue

        row_age_group = _detect_price_age_group(line) or current_age_group
        row_year = academic_year_match.group(1).replace(" ", "") if academic_year_match else current_academic_year
        row_period = _detect_price_period(
            line,
            default_period=(
                current_period
                if row_category == current_category
                and _section_period_applies_to_price_line(
                    line, current_heading, current_payment_plan
                )
                and not (
                    _INSTALLMENT_MULTIPLIER_RE.search(line)
                    and len(_PRICE_LINE_AMOUNT_RE.findall(line)) == 1
                )
                else None
            ),
        )
        if row_period is None:
            row_period = _period_on_following_line(lines, line_index)
        evidence_line = " ".join(part for part in (current_heading, semantic_line) if part)
        signals.append(
            {
                "line": evidence_line,
                "amount": line_amount,
                "currency": line_currency or "BGN",
                "category": row_category,
                "period": row_period,
                "academic_year": row_year,
                "age_group": row_age_group,
                "section_heading": current_heading,
                "source_url": current_source_url,
                "source_is_stale": source_staleness.get(
                    current_source_url, source_staleness.get(None, False)
                ),
            }
        )

    return signals


def _extract_prices_deterministic(text: str) -> PriceExtractionOutput:
    if not text:
        return PriceExtractionOutput()

    lines = [_clean_price_line(raw) for raw in re.split(r"[\n\r]+", text) if _clean_price_line(raw)]
    if not lines:
        return PriceExtractionOutput()

    prices: list[ExtractedPrice] = []
    current_category: str | None = None
    current_age_group: str | None = None
    current_academic_year: str | None = None
    current_period: str | None = None
    current_payment_plan = False
    current_plan_name: str | None = None
    current_heading: str | None = None
    current_includes: list[str] = []
    active_price: ExtractedPrice | None = None
    seen: set[tuple[str, float, str, str | None, str | None]] = set()

    for line_index, line in enumerate(lines):
        lowered = line.lower()
        if lowered.startswith("source:"):
            current_category = None
            current_age_group = None
            current_academic_year = None
            current_period = None
            current_payment_plan = False
            current_plan_name = None
            current_heading = None
            current_includes = []
            active_price = None
            continue
        line_amount, line_currency = _extract_price_amount_currency(
            line, allow_cyrillic_currency_words=False
        )
        academic_year_match = re.search(r"(20\d{2}\s*[-/]\s*20\d{2})", line)
        if academic_year_match:
            current_academic_year = academic_year_match.group(1).replace(" ", "")

        if line_amount is None and any(token in lowered for token in _OPTIONAL_PRICING_SECTION_TOKENS):
            current_category = None
            current_age_group = None
            current_period = None
            current_payment_plan = False
            current_plan_name = None
            current_heading = None
            current_includes = []
            active_price = None
            continue

        if line_amount is None and current_period == "monthly" and lowered.startswith("еднократно плащане"):
            current_period = None

        line_category = _detect_price_category(line, allow_generic_heading=True)
        if line_category is not None and line_amount is None:
            if line_category != current_category or _starts_new_price_section(line):
                current_period = None
                current_payment_plan = False
            current_category = line_category
            current_heading = line
            detected_age_group = _detect_price_age_group(line)
            if detected_age_group is not None:
                current_age_group = detected_age_group
            current_plan_name = "Standard" if "стандарт" in lowered or "standard" in lowered else current_plan_name
            current_includes = []
            active_price = None

        explicit_period = _detect_explicit_price_period(line)
        if explicit_period is not None and line_amount is None:
            previous_amount = (
                _extract_price_amount_currency(lines[line_index - 1])[0]
                if line_index > 0 else None
            )
            if previous_amount is None or line_category is not None:
                current_period = explicit_period
                current_payment_plan = False
        elif (
            line_amount is None
            and line_category is not None
            and _PAYMENT_SCHEDULE_FREQUENCY_RE.search(line)
        ):
            current_payment_plan = True

        if "включва" in lowered and current_category is not None:
            current_includes.append(line)
            if active_price is not None and line not in active_price.includes:
                active_price.includes.append(line)
            continue

        installment_match = re.search(r"\b(?:на|за)\s*(\d+)\s*вноск", lowered)
        if installment_match:
            installment_count = int(installment_match.group(1))
            if installment_count != 1:
                if active_price is not None and line not in active_price.installments:
                    active_price.installments.append(line)
                continue

        if line_amount is None:
            continue

        if _is_penalty_price_line(line):
            continue

        row_category = _detect_price_category(line) or current_category
        if row_category is None:
            continue

        row_age_group = _detect_price_age_group(line) or current_age_group
        row_year = academic_year_match.group(1).replace(" ", "") if academic_year_match else current_academic_year
        row_plan_name = "Standard" if "стандарт" in lowered or "standard" in lowered else current_plan_name
        row_period = _detect_price_period(
            line,
            default_period=(
                current_period
                if row_category == current_category
                and _section_period_applies_to_price_line(
                    line, current_heading, current_payment_plan
                )
                and not (
                    _INSTALLMENT_MULTIPLIER_RE.search(line)
                    and len(_PRICE_LINE_AMOUNT_RE.findall(line)) == 1
                )
                else None
            ),
        )
        if row_period is None:
            row_period = _period_on_following_line(lines, line_index)
        signature = (row_category, line_amount, line_currency or "BGN", row_age_group, row_year)
        if signature in seen:
            continue
        seen.add(signature)

        price = ExtractedPrice(
            category=row_category,
            amount=line_amount,
            currency=line_currency or "BGN",
            period=row_period,
            plan_name=row_plan_name,
            academic_year=row_year,
            age_group=row_age_group,
            includes=list(current_includes),
            confidence=0.7,
        )
        prices.append(price)
        active_price = price

    return PriceExtractionOutput(
        prices=prices,
        has_pricing_info=bool(prices),
        confidence_notes="Deterministic fallback parsed structured pricing lines.",
    )


def _normalize_academic_year(value: str | None) -> str | None:
    """Delegate to the shared canonical normalizer (single implementation)."""
    return normalize_academic_year(value)


def _yearless_pricing_text_is_stale(text: str, *, current_year: int | None = None) -> bool:
    """Detect clearly dated legacy fee tables without treating general history as pricing dates."""
    current_year = current_year or datetime.datetime.now(datetime.timezone.utc).year
    raw_lines = re.split(r"[\n\r]+", text or "")
    academic_year_ends: list[int] = []
    pricing_tokens = (
        "tuition",
        "fee",
        "fees",
        "price",
        "pricing",
        "такс",
        "цена",
        "цени",
        "плащ",
    )
    for index, raw_line in enumerate(raw_lines):
        year_matches = list(
            re.finditer(r"(20\d{2})\s*[-/]\s*(20\d{2})", raw_line)
        )
        if not year_matches:
            continue
        lowered = raw_line.casefold()
        directly_pricing_related = any(token in lowered for token in pricing_tokens)
        remaining = re.sub(
            r"(20\d{2})\s*[-/]\s*(20\d{2})", "", lowered
        )
        remaining = re.sub(r"[^\w\s]+", " ", remaining).strip()
        is_academic_year_heading = remaining in {
            "",
            "academic year",
            "school year",
            "учебна година",
            "учебната година",
        }
        nearby_lines = (
            raw_lines[max(0, index - 1) : index]
            + raw_lines[index + 1 : index + 2]
        )
        nearby_pricing_related = any(
            any(token in nearby.casefold() for token in pricing_tokens)
            for nearby in nearby_lines
        )
        if directly_pricing_related or (is_academic_year_heading and nearby_pricing_related):
            academic_year_ends.extend(int(match.group(2)) for match in year_matches)
    if academic_year_ends:
        return max(academic_year_ends) < current_year

    deadline_years: list[int] = []
    for raw_line in raw_lines:
        lowered = raw_line.casefold()
        if not any(token in lowered for token in ("deadline", "payment", "плащ", "краен срок")):
            continue
        deadline_years.extend(int(year) for year in re.findall(r"\b(20\d{2})\b", raw_line))
    return bool(deadline_years) and max(deadline_years) <= current_year - 2


def _pricing_source_staleness(text: str) -> dict[str | None, bool]:
    """Return staleness per source block, falling back to the whole text for one page."""
    source_headers = list(
        re.finditer(
            r"^\s*---\s*SOURCE:\s*(.*?)\s*---\s*$",
            text or "",
            flags=re.IGNORECASE | re.MULTILINE,
        )
    )
    if not source_headers:
        return {None: _yearless_pricing_text_is_stale(text)}

    staleness: dict[str | None, bool] = {}
    for index, header in enumerate(source_headers):
        block_end = (
            source_headers[index + 1].start()
            if index + 1 < len(source_headers)
            else len(text)
        )
        source_url = header.group(1).strip() or None
        staleness[source_url] = _yearless_pricing_text_is_stale(
            text[header.end() : block_end]
        )
    return staleness
