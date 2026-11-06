"""Find-websites stage: search for the website of a school that has none.

The register import brings a school in with the register's website, and for a private
school that is usually blank. Nothing searched for one afterwards: the monthly import
runs no website stage, and a school discovery once marked ``no_official_website`` never
entered a batch again. On 2026-10-23, 33 of the 174 private institutions had no site,
19 of them with one a search finds (534 Веда's wedaschule.com, 553's janbibijan.com), so
the fee and facts readers never ran for them. State schools and municipal kindergartens
joined on 2026-11-02: 198 of 228 kindergartens and 37 of 251 state schools showed no site,
because the one website search for kindergartens covered only the first 32 numbers.

Each school is searched (website discovery, SearXNG: free) and its best candidates go
through URL validation without the model fallback, so a run costs nothing: an ambiguous
site stays unstored. Only a site validation accepts is stored, the way ``check-website``
stores one (:func:`app.scrapers.stages.website.store_given_site`). A school searched in
the last ``retry_days`` is skipped, so the monthly run does not repeat a search that found
nothing until sites may have changed. The default is a dry run that writes nothing.

Validation counts a school's profile words as its name ("програмиране", "роботика"), so
a search for 150 ЧПГ по програмиране и роботика "Стив Джобс" found SoftUni Будител's site
valid. A run with nobody watching it also needs the school's own name, a part of its
register name in quotes ("Стив Джобс"), on the page (:func:`brand_named`); a name with
no quoted part is not checked. Since 2026-11-06 this holds for state schools too (a
Kazanlak kindergarten's site had every other word of 304 Държавна ДГ "Младост" към МО),
and a private school's page must also name its town or one of the city's own towns
(``site_city.OWN_TOWNS``): montesorigradina.bg, in Plovdiv, passed for 626 on Montessori
words alone.

A state school's or kindergarten's site is held to more (:func:`state_site_problem`):
its candidates are tried at their home page, so a directory's page about the school
(obrazovatelen-register.bg/dg-13-kalinka-sofiya/) or the district's list of its
kindergartens never counts, and that home page must name the school's own number with
its kind ("ДГ №136", not 136 СУ) and one of the words of its own name ("Славия").
Municipal kindergartens come from kg.sofia.bg with no ministry code to check against,
and many share a number with a school or a nursery, so a wrong site is the main risk.

A stored site's fees and facts come with the next scheduled reads (monthly fees, the
twice-yearly facts) or a thread's run for the schools found.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import School
from app.scrapers.school_tokens import extract_school_name_tokens
from app.scrapers.site_city import _own_numbers, numbered_schools_in
from app.utils.transliteration import transliterate_bulgarian

logger = logging.getLogger(__name__)

RETRY_DAYS = 90
# Candidates validated per school: search often ranks a directory or a news page first.
MAX_CANDIDATES = 3
SEARCHED_AT_KEY = "website_search_checked_at"
# Schools in a row that web search gave no answer for before a run stops: search is down,
# and every school searched now would only be an error.
MAX_UNANSWERED = 5


_QUOTES_RE = re.compile(r'["„“”«»]')
_LEGAL_FORM_RE = re.compile(r"^(?:е?оод|ад|еад|кд|сд|ет|ltd|llc)?$", re.IGNORECASE)
# A part that is only the school's kind or owner: "ЧДГ", "Частна детска градина",
# "Държавна ДГ", "към МО" (304 Държавна ДГ "Младост" към МО).
_TYPE_RE = re.compile(
    r"^(?:ч?(?:дг|оу|су|ну|пг|пгдн|сои|ег)|(?:частн|държавн|общинск)\w*\s.*|към\s+(?:мо|мон|министерств\w*|общин\w*)\b.*)$",
    re.IGNORECASE,
)


def brands(name: Optional[str]) -> list[str]:
    """The quoted parts of a register name that name the school itself: "Стив Джобс" in
    ЧПГ ... "СТИВ ДЖОБС" ЕООД, "Ян Бибиян" and "Мега-Х" in "ЧДГ "ЯН БИБИЯН" - МЕГА-Х" ООД.
    Empty when the name has no part besides its type, its legal form or one long name."""
    parts = [" ".join(part.split()).strip(" -,") for part in _QUOTES_RE.split(name or "")]
    parts = [part for part in parts if part and not _LEGAL_FORM_RE.match(part)]
    if len(parts) < 2:
        return []
    # A part with a number ("ДГ №5") or in brackets ("(с яслени групи)") is no name either.
    return [
        part for part in parts
        if not _TYPE_RE.match(part) and len(part.split()) <= 4 and not re.search(r"\d|^\(", part)
    ]


def brand_named(name: Optional[str], page_text: str) -> bool:
    """True when the page names one of the school's quoted names (either script), or it has none."""
    own = brands(name)
    if not own:
        return True
    text = (page_text or "").casefold()
    for spelling in {variant for part in own for variant in (part, transliterate_bulgarian(part))}:
        words = [word for word in re.findall(r"[^\W\d_]+|\d+", spelling.casefold()) if len(word) >= 3]
        if words and all(re.search(rf"(?<![^\W\d_]){re.escape(word)}(?![^\W\d_])", text) for word in words):
            return True
    return False


# A title's kind: a kindergarten's ("ДГ №59", "ОДЗ 29", "Детска градина 18"), a nursery's
# ("СДЯ №69", "Детска ясла 69": Sofia numbers its nurseries apart, so СДЯ №69 is not
# ДГ №69 Жар птица) or a school's ("74 Средно Училище", "119 СУ", "Шесто Основно Училище").
_KIND_RES = {
    "kindergarten": re.compile(
        r"(?<![^\W\d_])(?:ц?дг|одз)(?![^\W\d_])|детск\w*\s+градин|(?<![a-z])(?:dg|cdg|odz)(?![a-z])|detsk\w*\s+gradin|kindergarten",
        re.IGNORECASE,
    ),
    "nursery": re.compile(r"(?<![^\W\d_])с?дя(?![^\W\d_])|ясла|(?<![a-z])s?dya(?![a-z])|yasla|nursery", re.IGNORECASE),
    "school": re.compile(
        r"училищ|гимназ|лицей|(?<![^\W\d_])(?:оу|су|ну|соу|ег|пг|обу|ссу)(?![^\W\d_])|school|gymnas|lyce|(?<![a-z])(?:ou|su|sou|nu)(?![a-z])",
        re.IGNORECASE,
    ),
}


def state_kind(name: Optional[str], education_level: Optional[str]) -> str:
    """"nursery" for a separate nursery (СДЯ), else "kindergarten" or "school"."""
    from app.scrapers.shared_site_check import KINDERGARTEN_LEVELS

    level = str(education_level or "").casefold()
    if level == "nursery" or (
        level in KINDERGARTEN_LEVELS and _KIND_RES["nursery"].search(name or "") and not _KIND_RES["kindergarten"].search(name or "")
    ):
        return "nursery"
    return "kindergarten" if level in KINDERGARTEN_LEVELS else "school"


# What kg.sofia.bg adds after a name: "(с яслени групи)".
_BRACKETS_RE = re.compile(r"\([^)]*\)")
_NAME_FILLER = {"яслени", "групи", "сдя", "цдг", "одз", "ясла", "държавна", "общинска", "към"}


def _own_words(name: str) -> list[str]:
    """The words of a state name besides its kind and number: "Славия" in ДГ №136 Славия."""
    words = extract_school_name_tokens(_BRACKETS_RE.sub(" ", name), limit=12)
    return [word for word in words if not word.isdigit() and word not in _NAME_FILLER and len(word) >= 3]


def _whole_word(word: str, text: str) -> bool:
    return bool(re.search(rf"(?<![^\W\d_]){re.escape(word)}(?![^\W\d_])", text))


def _numbers_own_kind(text: str, own_numbers: set[str], kind: str) -> bool:
    """True when the text gives one of the numbers (as a number or an ordinal word) as a
    school of ``kind``.

    A kindergarten's or nursery's title needs a word of its kind, and a kindergarten's may
    still mention school ("подготовка за училище") unless it numbers a school with our
    number ("59 СУ"). A nursery's may not say "детска градина", a school's may not name a
    kindergarten or nursery. A school needs no word of its kind: schools abbreviate
    themselves too many ways to list ("203. ПЕГ", "138. СУЗИЕ", "122. ИОУ")."""
    if not own_numbers & _own_numbers(text):
        return False
    has = {other: bool(pattern.search(text)) for other, pattern in _KIND_RES.items()}
    if kind == "school":
        return not (has["kindergarten"] or has["nursery"])
    if not has[kind]:
        return False
    if kind == "nursery":
        return not has["kindergarten"]
    return not any(
        number in own_numbers and not _KIND_RES["kindergarten"].search(label) and not _KIND_RES["nursery"].search(label)
        for label, number in numbered_schools_in(text)
    )


def state_site_problem(name: Optional[str], kind: str, html: str) -> Optional[str]:
    """Why a state school's or kindergarten's home page is not its own, or None.

    ``kind`` is :func:`state_kind`'s. A numbered name needs the page's title, one of its
    first headings or its site name to give that number as a school of the same kind
    ("Детска градина №136" for ДГ №136 Славия; "59 СУ" is no kindergarten's title, "СДЯ
    69" no school's). The page must also have two of the name's own words, or its only
    one, in either script ("Детски свят" needs both: "детски" is on every kindergarten's
    page). A name with no number ("Национална финансово-стопанска гимназия") is left to URL
    validation's name checks, and one with no own words ("ДГ №202") to its number."""
    from bs4 import BeautifulSoup

    from app.scrapers.site_city import identity_texts

    name = " ".join(_BRACKETS_RE.sub(" ", name or "").split())
    soup = BeautifulSoup(html or "", "html.parser")
    own_numbers = _own_numbers(name)
    if own_numbers and not any(_numbers_own_kind(text, own_numbers, kind) for text in identity_texts(soup)):
        return f"The home page's title does not name {kind} {' or '.join(sorted(own_numbers))}"
    words = _own_words(name)
    if words:
        text = soup.get_text(" ", strip=True).casefold()
        named = [word for word in words if _whole_word(word, text) or _whole_word(transliterate_bulgarian(word).casefold(), text)]
        if len(named) < min(2, len(words)):
            return f"The home page does not name {' and '.join(words[:2]) if len(words) > 1 else words[0]}"
    return None


def site_problem(name: Optional[str], school: School, html: str) -> Optional[str]:
    """Why a validated page is not the school's own site, or None.

    Every school's page must name its quoted name ("Младост" in Държавна ДГ "Младост" към
    МО: a Kazanlak kindergarten's site has every other word of it). A state school's
    must also pass :func:`state_site_problem`. A private one's must name its town:
    montesorigradina.bg (Plovdiv, Pazardzhik) passed for 626 "Частна детска градина
    Монтесори" on words every Montessori kindergarten uses (2026-11-06)."""
    from bs4 import BeautifulSoup

    from app.scrapers.site_city import OWN_TOWNS

    text = BeautifulSoup(html or "", "html.parser").get_text(" ", strip=True)
    if not brand_named(name, text):
        return f"The page does not name {' or '.join(brands(name))}"
    if school.school_type == "state":
        return state_site_problem(name, state_kind(name, school.education_level), html)
    towns = OWN_TOWNS.get(str(school.city or "").casefold())
    if towns:
        lowered = text.casefold()
        spellings = {variant for town in towns for variant in (town.casefold(), transliterate_bulgarian(town).casefold())}
        if not any(_whole_word(spelling, lowered) for spelling in spellings):
            return f"The page does not name {towns[0]}"
    return None


class SearchUnavailable(RuntimeError):
    """Web search gave no answer for a school (blocked engines, a provider down)."""


def home_page(url: str) -> str:
    """The site's home page: a candidate's host, or its tenant's root on a platform that
    hosts sites by path (sites.google.com/view/dg59elhitsa/)."""
    from urllib.parse import urlsplit

    from app.scrapers.shared_site_check import site_id

    parts = urlsplit(url)
    if not parts.netloc:
        return url
    site = site_id(url)
    root = site if "/" in site else parts.netloc
    return f"{parts.scheme or 'https'}://{root}/"


async def _page_text(url: str) -> str:
    import httpx

    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            return (await client.get(url)).text
    except httpx.HTTPError:
        return ""


def _searched_recently(attributes: Any, now: datetime, retry_days: int) -> bool:
    value = (attributes or {}).get(SEARCHED_AT_KEY) if isinstance(attributes, dict) else None
    try:
        searched_at = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return False
    if searched_at.tzinfo is None:
        searched_at = searched_at.replace(tzinfo=timezone.utc)
    return now - searched_at < timedelta(days=retry_days)


async def _candidates(discoverer, school: School, *, home_pages: bool = False) -> list[str]:
    """The school's best search candidates, best first (no writes). With ``home_pages``,
    each candidate's home page instead, one a site."""

    name = (school.name_i18n or {}).get("bg") or (school.name_i18n or {}).get("en")
    aliases = discoverer._validation_aliases_for_school(school)
    hints = discoverer._discovery_hints_for_school(school)
    discoverer.search_answers.pop(school.id, None)
    pool = [(url, "search") for url in await discoverer._search_candidates(school)]
    discoverer.social_hits.pop(school.id, None)
    if not pool and discoverer.search_answers.get(school.id) == 0:
        # No provider answered (SearXNG's engines all blocked, 2026-11-06): the school was
        # not searched, so it is an error and is not stamped as searched.
        raise SearchUnavailable("no search provider answered")
    picked: list[str] = []
    seen: set[str] = set()
    while len(picked) < MAX_CANDIDATES:
        url, _ = discoverer._pick_best_candidate(
            pool, set(seen), school_name=name, school_aliases=aliases, discovery_hints=hints
        )
        if not url:
            break
        seen.add(url)
        if home_pages:
            url = home_page(url)
            if url in picked:  # the picker already leaves directory hosts out
                continue
        picked.append(url)
    return picked


async def run_find_websites(
    db: AsyncSession,
    *,
    country: str = "bg",
    city: Optional[str] = "sofia",
    school_ids: Sequence[int] = (),
    limit: Optional[int] = None,
    retry_days: int = RETRY_DAYS,
    apply: bool = False,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Search for the sites of the schools with none (the given ids, or every school in
    ``city``); returns the report."""
    from app.scrapers.stages.website import store_given_site
    from app.scrapers.url_validator import URLValidator, ValidationResult, extract_validation_aliases
    from app.scrapers.website_discovery import WebsiteDiscoverer, directory_host

    now = now or datetime.now(timezone.utc)
    query = select(School).options(selectinload(School.locations)).where(
        School.country_code == country, School.website_url.is_(None)
    )
    if school_ids:
        query = query.where(School.id.in_(list(school_ids)))
    if city:
        query = query.where(func.lower(School.city) == city.lower())
    schools = (await db.execute(query.order_by(School.id))).scalars().all()
    due = [school for school in schools if school_ids or not _searched_recently(school.attributes, now, retry_days)]
    if limit is not None:
        due = due[:limit]

    # Ids, not objects: a rollback after one school's failure expires every loaded object.
    due_ids = [school.id for school in due]
    discoverer = WebsiteDiscoverer(country_code=country)
    results: list[dict[str, Any]] = []
    unanswered = 0
    stopped = None
    for school_id in due_ids:
        if unanswered >= MAX_UNANSWERED:
            stopped = f"web search answered none of the last {unanswered} schools; the rest were not searched"
            logger.warning("find-websites stopped: %s", stopped)
            break
        school = (
            await db.execute(
                select(School)
                .options(selectinload(School.locations))
                .where(School.id == school_id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        name = (school.name_i18n or {}).get("bg") or (school.name_i18n or {}).get("en")
        result: dict[str, Any] = {"school_id": school_id, "name_bg": (school.name_i18n or {}).get("bg"), "tried": []}
        try:
            state = school.school_type == "state"
            candidates = await _candidates(discoverer, school, home_pages=state)
            validator = URLValidator(country_code=country, school_id=school_id, school_city=school.city)
            for url in candidates:
                verdict, final_url, reason = await validator.validate_url(
                    url,
                    use_llm_fallback=False,
                    school_name=name,
                    school_aliases=extract_validation_aliases(school.attributes),
                )
                result["tried"].append({"url": url, "result": verdict.value, "reason": reason})
                if verdict is not ValidationResult.VALID:
                    continue
                if directory_host(final_url or url):  # a site that redirects to its directory entry
                    result["tried"][-1].update(result="invalid", reason=f"Redirects to {directory_host(final_url)}, a directory")
                    continue
                page = await _page_text(final_url or url)
                problem = site_problem(name, school, page)
                if problem:
                    result["tried"][-1].update(result="invalid", reason=problem)
                    continue
                result["found"] = final_url or url
                if apply:
                    await store_given_site(db, school, url, final_url, reason, method="search")
                break
        except SearchUnavailable as exc:
            logger.warning("find-websites: no search answer for school %s", school_id)
            result["error"] = f"search unavailable: {exc}"
            unanswered += 1
        except Exception as exc:  # one school's failure must not stop the run
            logger.exception("find-websites could not search for school %s", school_id)
            result["error"] = f"{type(exc).__name__}: {exc}"[:200]
            await db.rollback()
        else:
            unanswered = 0
        if apply and "error" not in result:
            fresh = await db.get(School, school_id)
            attrs = dict(fresh.attributes or {})
            attrs[SEARCHED_AT_KEY] = now.isoformat()
            fresh.attributes = attrs
            await db.commit()
        results.append(result)

    if stopped is None and results and unanswered == len(results):
        stopped = f"web search answered none of the {unanswered} schools searched"
    found = [result for result in results if result.get("found")]
    if apply and found:
        from app.scrapers.stages.website import check_shared_sites

        await check_shared_sites(db, country=country, school_ids=[result["school_id"] for result in found])
    return {
        "schools_without_site": len(schools),
        "searched": len(results),
        "found": len(found),
        "errors": sum("error" in result for result in results),
        "stopped": stopped,
        "applied": apply,
        "schools": results,
    }
