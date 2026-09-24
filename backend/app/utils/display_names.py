"""Display-only cleanup of register-formatted school names and addresses (UF34/UF37).

Stored names and addresses stay exactly as the source published them; these helpers
only shape the ``resolved_*`` values the API derives for display:

* ``tidy_bg_name`` — ALL-CAPS words to readable case (acronyms, Roman numerals and
  abbreviations kept), legal-form suffixes and wrapping/stray quotes removed, quotes
  normalised to „…“.
* ``translate_bg_school_name`` — English display name: school-type words translated
  ("Средно училище" → "Secondary School"), ordinal numbers, the proper-name part
  transliterated.
* ``tidy_bg_address`` / ``english_address`` — same cleanup for addresses; the English
  form is a clean transliteration without the redundant "гр. София" / municipality noise.
"""

from __future__ import annotations

import re

from app.utils.transliteration import transliterate_address, transliterate_bulgarian

_QUOTE_CHARS = "\"„“”«»"
_BG_VOWELS = set("аъоуеиюяАЪОУЕИЮЯ")
_ROMAN_RE = re.compile(r"[IVXІ]+")
_WORD_RE = re.compile(r"[А-Яа-яЁёЀ-ӿ]+")

# Upper-case Cyrillic tokens that are real acronyms, kept as written.
_ACRONYMS = frozenset(
    {
        "СУ", "ОУ", "НУ", "ОбУ", "ПГ", "ДГ", "ЦДГ", "ЧДГ", "ЧОУ", "ЧСУ", "ЧПГ", "СДЯ",
        "НПМГ", "СМГ", "ПМГ", "ППМГ", "НГДЕК", "НФСГ", "НТБГ", "НУКК", "НМУ", "НУТИ",
        "ЕГ", "АЕГ", "НЕГ", "ФЕГ", "ПЕГ", "СЕУ", "СОУ", "ВТУ", "ТУ", "МО", "БГ", "СО",
        "БАН", "СУИЧЕ", "ГПЧЕ", "ОДЗ", "ЦПЛР",
    }
)
# Words that stay lower case in a Bulgarian institution name unless they start it.
_LOWER_WORDS = frozenset(
    """
    и в във с със за на по от до към при де дьо ла ди ван фон
    училище училища средно основно начално обединено гимназия лицей колеж комплекс
    професионална професионално профилирана профилирано езикова езиково специализирано
    специално спортно математическа природо детска градина ясла яслени група групи
    частна частно частен държавна държавно национална национално вечерно вечерна
    сменно учебен музикално изкуство изкуства танцово изящни приложни изобразителни
    изучаване преподаване интензивно обучение ранно чуждоезиково чужди чужд езици език
    английски английска немски немска френски френска испански испанска руски руска
    италиански италианска румънски румънска западни източни древни култури култура
    технологии технологично техника икономика туризъм туризма информатика текстилни
    кожени изделия подемна строителна транспортна транспорт енергетика електротехника
    автоматика електроника телекомуникации строителство архитектура геодезия високи
    прецизна оптика финансово стопанска търговско банкова банково дело търговия финанси
    полиграфия фотография облекло дизайн механоелектротехника екология биотехнологии
    хранително вкусови хлебни сладкарски сценични мениджмънт дигитални науки математика
    отбраната ученици увреден слух нарушено зрение разширено хореография сграда
    почасова организация експериментална участието
    """.split()
)
_FUNCTION_WORDS = frozenset({"и", "в", "във", "с", "със", "на", "де", "дьо", "ла", "ди", "ван", "фон"})
_HONORIFICS = (
    (re.compile(r"\bД-Р\b"), "Д-р"),
    (re.compile(r"\bСВ\.\s*СВ\."), "Св. св."),
)
_LEGAL_SUFFIX_RE = re.compile(
    r"(?:\s*,)?\s+(?:ЕООД|ООД|ЕАД|АД|ЕТ|КД|СД)\.?$", flags=re.IGNORECASE
)
_LEGAL_PREFIX_RE = re.compile(r"^(?:сдружение|фондация)\s+(?=[\"„“])", flags=re.IGNORECASE)


_LATIN_TO_CYRILLIC = str.maketrans("AaBEeKkMHOoPpCcTXxy", "АаВЕеКкМНОоРрСсТХху")


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _fix_homoglyphs(text: str) -> str:
    """Latin look-alikes typed inside Cyrillic words ("училищe", "Hикола") → Cyrillic."""
    return re.sub(
        r"[A-Za-zА-Яа-я]+",
        lambda m: m.group(0).translate(_LATIN_TO_CYRILLIC)
        if re.search(r"[А-Яа-я]", m.group(0)) and re.search(r"[A-Za-z]", m.group(0))
        else m.group(0),
        text,
    )


def _normalise_quotes(text: str, *, open_q: str, close_q: str) -> str:
    """Pair quote marks, drop stray/wrapping ones and fix spacing around them."""
    text = text.replace(",,", "\"").replace("''", "\"")
    positions = [i for i, ch in enumerate(text) if ch in _QUOTE_CHARS]
    if len(positions) % 2 == 1:
        if positions[0] == 0:
            text = text[1:]
        elif positions[-1] == len(text) - 1:
            text = text[:-1]
        else:
            # An unterminated trailing quote ("… градина "Детска мечта"): close it.
            text = text + "\""
        text = text.strip()
    positions = [i for i, ch in enumerate(text) if ch in _QUOTE_CHARS]
    # A pair wrapping the whole value adds nothing. With more quotes inside, the outer
    # pair is the registry's wrapper around a nested name ('"ЧСУ "Дружба" - София"'),
    # so left-to-right pairing would turn it inside out.
    if len(positions) >= 2 and positions[0] == 0 and positions[-1] == len(text) - 1:
        return _normalise_quotes(text[1:-1].strip(), open_q=open_q, close_q=close_q)

    out: list[str] = []
    opening = True
    for ch in text:
        if ch in _QUOTE_CHARS:
            out.append("\x01" if opening else "\x02")
            opening = not opening
        else:
            out.append(ch)
    result = "".join(out)
    result = re.sub(r"\x01\s+", "\x01", result)
    result = re.sub(r"\s+\x02", "\x02", result)
    result = re.sub(r"(?<=[^\s(])\x01", " \x01", result)
    result = re.sub(r"\x02(?=[^\s),.;:\x02])", "\x02 ", result)
    return _collapse(result.replace("\x01", open_q).replace("\x02", close_q))


def _is_upper_word(word: str) -> bool:
    return len(word) >= 2 and word.isupper()


def is_register_caps(text: str | None) -> bool:
    """True when a name is (mostly) written in capitals, i.e. register formatting."""
    letters = [ch for ch in text or "" if ch.isalpha()]
    return bool(letters) and sum(ch.isupper() for ch in letters) / len(letters) > 0.6


def _recase_word(
    word: str,
    *,
    first: bool,
    in_quotes: bool,
    followed_by_dot: bool,
    register_caps: bool,
    isolated: bool,
) -> str:
    """Readable case for one ALL-CAPS Cyrillic word; other words are returned unchanged.

    In a name written mostly in capitals every caps word is register formatting. In a
    mixed-case name a lone short caps word ("НЕМО", "ЕСПА") is a deliberate brand and
    is kept; caps runs ("„ВАСИЛ ЛЕВСКИ“") and generic type words are recased.
    """
    if not _is_upper_word(word):
        if first and word[:1].islower() and not followed_by_dot:
            return word[:1].upper() + word[1:]
        return word
    if _ROMAN_RE.fullmatch(word) or word in _ACRONYMS:
        return word
    if not followed_by_dot and not (set(word) & _BG_VOWELS):
        return word  # vowel-less: an acronym (ПГ, НПМГ …)
    lower = word.lower()
    if followed_by_dot:
        return lower.capitalize()  # abbreviation: ПРОФ. → Проф., СВ. → Св.
    if (
        not register_caps
        and isolated
        and len(word) <= 4
        and (in_quotes or lower not in _LOWER_WORDS)
    ):
        return word
    if first:
        return lower.capitalize()
    if lower in _LOWER_WORDS and not (in_quotes and lower not in _FUNCTION_WORDS):
        return lower
    return lower.capitalize()


def _recase(text: str, *, register_caps: bool) -> str:
    for pattern, replacement in _HONORIFICS:
        text = pattern.sub(replacement, text)
    out: list[str] = []
    last = 0
    in_quotes = False
    first = True
    matches = list(_WORD_RE.finditer(text))
    caps = [_is_upper_word(m.group(0)) for m in matches]
    for index, match in enumerate(matches):
        between = text[last:match.start()]
        for ch in between:
            if ch in "„“":
                in_quotes = ch == "„"
                first = first or ch == "„"
        out.append(between)
        word = match.group(0)
        followed_by_dot = text[match.end():match.end() + 1] == "."
        if re.search(r"\d-$", between) and len(word) <= 2:
            # Ordinal suffix of a number ("150-ТО", "49-то"): lower case, not a word.
            out.append(word.lower())
            last = match.end()
            continue
        if len(word) == 1:
            # Single capitals: conjunctions/prepositions go lower, initials stay.
            if register_caps and word in "ИВСК" and not followed_by_dot and not first:
                word = word.lower()
            out.append(word)
        else:
            out.append(
                _recase_word(
                    word,
                    first=first,
                    in_quotes=in_quotes,
                    followed_by_dot=followed_by_dot,
                    register_caps=register_caps,
                    isolated=not (
                        (index > 0 and caps[index - 1])
                        or (index + 1 < len(caps) and caps[index + 1])
                    ),
                )
            )
        first = False
        last = match.end()
    out.append(text[last:])
    return "".join(out)


def tidy_bg_name(name: str | None, *, source: str | None = None) -> str | None:
    """Readable Bulgarian display name from a register-formatted one.

    ``source`` is the full stored name ``name`` was cut from (e.g. the registry name a
    brand core was extracted from); its capitalisation decides whether caps are
    register formatting or a deliberate brand.
    """
    text = _collapse(name or "")
    if not text:
        return None
    text = _fix_homoglyphs(text).replace("ОсновноУчилище", "Основно училище")
    text = _LEGAL_PREFIX_RE.sub("", text)
    text = _LEGAL_SUFFIX_RE.sub("", text).strip()
    text = _normalise_quotes(text, open_q="„", close_q="“")
    # "91.НЕМСКА" / "61Основно" / "16 . ОСНОВНО" → "91. Немска", "61 Основно", "16. Основно"
    text = re.sub(r"^(\d+)\s+\.", r"\1.", text)
    text = re.sub(r"^(\d+\.?)(?=[А-Яа-я])", r"\1 ", text)
    register_caps = is_register_caps(source if source else text)
    return _collapse(_recase(text, register_caps=register_caps)) or None


# --- English --------------------------------------------------------------------

_ORDINAL_PREFIX_RE = re.compile(r"^(\d+)\s*(?:\.|-[а-я]{1,2})?\s+(?=\S)", flags=re.IGNORECASE)
_BG_TYPE_PHRASES: tuple[tuple[str, str], ...] = (
    # Longest phrases first; matched case-insensitively on the tidied Bulgarian name.
    ("частна детска градина", "Private Kindergarten"),
    ("частна немска детска градина", "Private German Kindergarten"),
    ("частна монтесори детска градина", "Private Montessori Kindergarten"),
    ("частно основно училище", "Private Primary School"),
    ("частно начално училище", "Private Primary School"),
    ("частно езиково средно училище", "Private Language School"),
    ("частно средно езиково училище", "Private Language School"),
    ("частно средно училище", "Private Secondary School"),
    ("частна езикова гимназия", "Private Language High School"),
    ("частна профилирана гимназия", "Private Profiled High School"),
    ("частна професионална гимназия", "Private Vocational High School"),
    ("частна немска гимназия", "Private German High School"),
    ("частна математическа гимназия", "Private High School of Mathematics"),
    ("държавна детска градина", "State Kindergarten"),
    ("детска градина", "Kindergarten"),
    ("вечерно средно училище", "Evening Secondary School"),
    ("средно езиково училище", "Language School"),
    ("средно училище", "Secondary School"),
    ("основно училище", "Primary School"),
    ("начално училище", "Primary School"),
    ("обединено училище", "Unified School"),
    ("специализирано спортно училище", "Sports School"),
    ("спортно училище", "Sports School"),
    ("иновативно основно училище", "Innovative Primary School"),
    ("иновативно средно училище", "Innovative Secondary School"),
    ("специално училище", "Special School"),
    ("технологично училище", "Technology School"),
    ("духовно училище", "Theological School"),
    ("духовна семинария", "Theological Seminary"),
    ("национално средно училище", "National Secondary School"),
    ("национално музикално училище", "National School of Music"),
    ("национално училище за танцово изкуство", "National School of Dance Arts"),
    ("национално училище за изящни изкуства", "National School of Fine Arts"),
    ("национален учебен комплекс по култура", "National Educational Complex for Culture"),
    ("национална гимназия за приложни изкуства", "National High School of Applied Arts"),
    ("национална гимназия за древни езици и култури", "National High School for Ancient Languages and Cultures"),
    ("национална природо-математическа гимназия", "National High School of Mathematics and Natural Sciences"),
    ("национална финансово-стопанска гимназия", "National High School of Finance and Business"),
    ("национална търговско - банкова гимназия", "National High School of Commerce and Banking"),
    ("национална търговско-банкова гимназия", "National High School of Commerce and Banking"),
    ("национална професионална гимназия", "National Vocational High School"),
    ("софийска математическа гимназия", "Sofia High School of Mathematics"),
    ("софийска професионална гимназия", "Sofia Vocational High School"),
    ("софийска гимназия", "Sofia High School"),
    ("английска езикова гимназия", "English Language High School"),
    ("немска езикова гимназия", "German Language High School"),
    ("френска езикова гимназия", "French Language High School"),
    ("испанска езикова гимназия", "Spanish Language High School"),
    ("профилирана езикова гимназия", "Language High School"),
    ("езикова гимназия", "Language High School"),
    ("профилирана гимназия", "Profiled High School"),
    ("професионална гимназия", "Vocational High School"),
    ("сменно-вечерна гимназия", "Evening High School"),
    ("математическа гимназия", "High School of Mathematics"),
    ("гимназия", "High School"),
    ("американски колеж", "American College"),
    ("англо-американско училище", "Anglo-American School"),
    ("френско училище", "French School"),
    ("софийска духовна семинария", "Sofia Theological Seminary"),
    # Descriptive tails.
    ("с яслени групи", "with nursery groups"),
    ("с изучаване на чужди езици", "with foreign languages"),
    ("с преподаване на чужди езици", "with foreign languages"),
    ("с ранно чуждоезиково обучение", "with early foreign-language teaching"),
    ("с изучаване на английски език", "with English"),
    ("с изучаване на чужд език", "with a foreign language"),
    ("с чуждоезиково обучение", "with foreign-language teaching"),
    ("с ранно чуждоезиково обучение по френски и немски език", "with early French and German teaching"),
    ("с разширено изучаване на хореография", "with extended choreography"),
    ("за ученици с увреден слух", "for Students with Hearing Impairments"),
    ("за ученици с нарушено зрение", "for Students with Visual Impairments"),
    ("с лицей за изучаване на италиански език и култура с участието на Република Италия",
     "with a Lyceum of Italian Language and Culture"),
    ("към технически университет - софия", "(Technical University of Sofia)"),
    ("с преподаване на испански език", "with Spanish"),
    ("с интензивно изучаване на румънски език", "with Romanian"),
    ("за чужди езици и математика", "of Foreign Languages and Mathematics"),
    ("за западни и източни езици", "of Western and Eastern Languages"),
    ("за изобразителни изкуства", "of Fine Arts"),
    ("по туризъм", "of Tourism"),
    ("по транспорт и енергетика", "of Transport and Energy"),
    ("по транспорт", "of Transport"),
    ("по електротехника и автоматика", "of Electrical Engineering and Automation"),
    ("по електроника", "of Electronics"),
    ("по телекомуникации", "of Telecommunications"),
    ("по облекло", "of Clothing"),
    ("по дизайн", "of Design"),
    ("по механоелектротехника", "of Mechanical and Electrical Engineering"),
    ("по екология и биотехнологии", "of Ecology and Biotechnology"),
    ("по хранително-вкусови технологии", "of Food Technology"),
    ("по текстилни и кожени изделия", "of Textile and Leather Products"),
    ("по подемна, строителна и транспортна техника", "of Lifting, Construction and Transport Machinery"),
    ("по прецизна техника и оптика", "of Precision Engineering and Optics"),
    ("по строителство, архитектура и геодезия", "of Construction, Architecture and Geodesy"),
    ("по хлебни и сладкарски технологии", "of Bread and Confectionery Technology"),
    ("по високи технологии", "of High Technology"),
    ("по полиграфия и фотография", "of Printing and Photography"),
    ("по икономика, туризъм и информатика", "of Economics, Tourism and Informatics"),
    ("по банково дело, търговия и финанси", "of Banking, Commerce and Finance"),
    ("по дигитални науки", "of Digital Sciences"),
    ("по сценични изкуства и мениджмънт", "of Performing Arts and Management"),
    ("сграда", "building"),
    ("почасова организация", "hourly groups"),
    ("към министерство на отбраната", "(Ministry of Defence)"),
    ("към МО", "(Ministry of Defence)"),
    ("в София", "in Sofia"),
)
_BG_TYPE_ABBREVIATIONS = (
    ("ДГ", "Kindergarten"),
    ("СДЯ", "Nursery"),
    ("ЧДГ", "Private Kindergarten"),
    ("ЧОУ", "Private Primary School"),
    ("ЧСУ", "Private Secondary School"),
    ("СУ", "Secondary School"),
    ("ОУ", "Primary School"),
    ("НУ", "Primary School"),
    ("ПГ", "Vocational High School"),
)
_BG_ORDINAL_WORDS = (
    ("първа", "First"), ("първо", "First"), ("втора", "Second"), ("второ", "Second"),
    ("трета", "Third"), ("трето", "Third"),
)
_EN_NAME_REPLACEMENTS = (
    (re.compile(r"\bSv\.\s*[Ss]v\.\s*"), "Sts. "),
    (re.compile(r"\bSveti,? sveti\b", flags=re.IGNORECASE), "Sts."),
    (re.compile(r"\bD-r\b", flags=re.IGNORECASE), "Dr."),
    (re.compile(r"\bSv\.\s*", flags=re.IGNORECASE), "St. "),
    (re.compile(r"\bAkad\.", flags=re.IGNORECASE), "Acad."),
    (re.compile(r"\bAkademik\b"), "Acad."),
    (re.compile(r"\bProfesor\b"), "Prof."),
    (re.compile(r"\bSvet[ia](?= [A-Z])"), "St."),
    (re.compile(r"\bKiril i Metodiy\b"), "Cyril and Methodius"),
)
_PHRASE_PATTERNS = tuple(
    (re.compile(rf"(?<![\w-]){re.escape(bg)}(?![\w-])", flags=re.IGNORECASE), en)
    for bg, en in sorted(_BG_TYPE_PHRASES, key=lambda item: -len(item[0]))
)
_ABBREVIATION_PATTERNS = tuple(
    (re.compile(rf"(?<![\w-]){bg}(?![\w-])"), en) for bg, en in _BG_TYPE_ABBREVIATIONS
)
_ORDINAL_WORD_PATTERNS = tuple(
    (re.compile(rf"^{bg}(?![\w-])", flags=re.IGNORECASE), en) for bg, en in _BG_ORDINAL_WORDS
)


def _ordinal(number: int) -> str:
    if 10 <= number % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix}"


def _english_quotes(text: str) -> str:
    return re.sub(r"[„“”«»]", "\"", text)


def translate_bg_school_name(name: str | None, *, source: str | None = None) -> str | None:
    """English display name: translated type words + transliterated proper name."""
    tidy = tidy_bg_name(name, source=source)
    if not tidy:
        return None
    text = tidy
    ordinal_match = _ORDINAL_PREFIX_RE.match(text)
    ordinal = None
    if ordinal_match:
        ordinal = _ordinal(int(ordinal_match.group(1)))
        text = text[ordinal_match.end():]
    for pattern, english in _ORDINAL_WORD_PATTERNS:
        text = pattern.sub(english, text)
    placeholders: list[str] = []

    def _hold(english: str) -> str:
        placeholders.append(english)
        return f"\x00{len(placeholders) - 1}\x00"

    for pattern, english in _PHRASE_PATTERNS:
        text = pattern.sub(lambda _m, en=english: _hold(en), text)
    for pattern, english in _ABBREVIATION_PATTERNS:
        text = pattern.sub(lambda _m, en=english: _hold(en), text)
    text = text.replace("№", "No. ")
    text = transliterate_bulgarian(text).replace("І", "I")
    # A number followed directly by a school type is the school's ordinal ("20. Иновативно
    # основно училище" → "20th Innovative Primary School"); a number in a brand
    # ("101 Защо", "101 Защо частна детска градина") stays as written.
    starts_with_type = text.startswith("\x00")
    text = re.sub(r"\x00(\d+)\x00", lambda m: placeholders[int(m.group(1))], text)
    if ordinal_match:
        # "82. Основно училище" → "82nd Primary School"; "101 Защо" stays "101 Zashto".
        text = f"{ordinal if starts_with_type else ordinal_match.group(0).strip()} {text}"
    for pattern, replacement in _EN_NAME_REPLACEMENTS:
        text = pattern.sub(replacement, text)
    text = _english_quotes(text)
    text = re.sub(r"No\.\s+", "No. ", text)
    text = _collapse(text)
    return text[:1].upper() + text[1:] if text else None


# --- Addresses ------------------------------------------------------------------

_CITY_PREFIX_RE = re.compile(
    r"^(?:гр\.\s*)?софия(?![а-яa-z])(?:\s+\d{4})?\s*[,;|]?\s*(?=\S)", flags=re.IGNORECASE
)
_MUNICIPALITY_SUFFIX_RE = re.compile(
    r"(?:\s*,\s*\d{4}\s+столична|\s*-\s*СО|\s*,\s*СО)\s*$", flags=re.IGNORECASE
)


def tidy_bg_address(address: str | None) -> str | None:
    """Readable Bulgarian address; the city prefix and municipality code are dropped."""
    text = _fix_homoglyphs(_collapse(address or ""))
    if not text:
        return None
    # Caps words are register formatting: all of them in a caps address, otherwise
    # words of 4+ letters (short caps tokens like "ВТУ" may be acronyms).
    # A short caps word next to another caps word ("ГЕО МИЛЕВ") is part of a caps run.
    min_len = 2 if is_register_caps(text) else 4
    caps_run_re = re.compile(r"[А-Я]{2,}(?:[\s.-]+[А-Я]{2,})+")
    run_spans = [m.span() for m in caps_run_re.finditer(text)]
    text = re.sub(
        r"[А-Я]+",
        lambda m: m.group(0)
        if _ROMAN_RE.fullmatch(m.group(0))
        or len(m.group(0)) < 2
        or (
            len(m.group(0)) < min_len
            and not any(start <= m.start() < end for start, end in run_spans)
        )
        else m.group(0).capitalize(),
        text,
    )
    text = re.sub(
        r"(?<![\w.])(УЛ|Ул|БУЛ|Бул|Ж\.К|Ж\.к|ЖК|Жк|КВ|Кв|ГР|Гр|ПЛ|Пл|БЛ|Бл|ВХ|Вх)\.",
        lambda m: m.group(1).lower() + ".",
        text,
    )
    text = _MUNICIPALITY_SUFFIX_RE.sub("", text).strip(" ,")
    stripped = _CITY_PREFIX_RE.sub("", text)
    if stripped and stripped != text:
        text = stripped
    quote_count = sum(text.count(ch) for ch in _QUOTE_CHARS)
    if quote_count and quote_count % 2 == 0:
        # Only well-formed pairs are rewritten; an odd stray quote is left as published.
        text = _normalise_quotes(text, open_q="„", close_q="“")
    # "ул.Букет" / "ул.\"Букет\"" → "ул. Букет"; "№26" → "№ 26"; "„Звезда“№ 3" → "„Звезда“ № 3".
    text = re.sub(
        r"(?<![\w.])(ул|бул|ж\.\s?к|жк|кв|пл)\.(?=[^\s.,])", r"\1. ", text, flags=re.IGNORECASE
    )
    text = re.sub(r"(?<![\w.])ж\.\s+к\.", "ж.к.", text)
    text = re.sub(r"№\s*(?=\S)", "№ ", text)
    text = re.sub(r"(?<=[^\s(])№", " №", text)
    text = re.sub(r"\s+,", ",", text)
    text = re.sub(r",(?=[^\s\d])", ", ", text)
    return _collapse(text) or None


def english_address(address: str | None) -> str | None:
    """Clean transliteration of a tidied Bulgarian address."""
    tidy = tidy_bg_address(address)
    if not tidy:
        return None
    text = transliterate_address(tidy).replace("І", "I")
    text = _english_quotes(text)
    text = re.sub(r"№\s*", "No. ", text)
    # "rayon Vitosha" / "r-n Vitosha" → "Vitosha district"
    text = re.sub(
        r"(?<![\w-])(?:[Rr]ayon|r-n)\s+\"?([^,\"]+?)\"?(?=,|$)", r"\1 district", text
    )
    return _collapse(text) or None
