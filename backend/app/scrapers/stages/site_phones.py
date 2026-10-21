"""Site-phones stage: a school's main location shows the phone its own website gives.

Mike's rule (2026-10-15 for addresses, 2026-10-21 for phones): the school's own website
wins over the register unless it is plainly out of date. The register's phone is often a
number the school no longer publishes (a deputy director's mobile, transposed digits). For
each school with an accepted website, the home and contact pages are read; a phone there is
a Sofia landline or a Bulgarian mobile, not one labelled as a fax. When the register's
phone is among them it stays. Otherwise the school's primary location takes the site's
first two phones: those listed right after the site names that location's address, else the
page's (a deputy's or the accountant's only when there is no other). It keeps the one it
replaced (``PHONE_WAS_PREFIX``) so a wrong-website undo restores it. Held back when the site
gives no phone, when a page-level first phone also appears on another school's site (a web
designer's, or an umbrella organisation's), or when several of our schools share the website
and it lists no number under our address (whose phone is it?). No model calls. Default is a
dry run.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from collections.abc import Sequence
from types import SimpleNamespace
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import School, SchoolLocation
from app.scrapers.fee_pages import fee_http_client
from app.scrapers.stages.social_links import Fetch, _http_fetch
from app.scrapers.stages.website_pins import read_site
from app.utils.school_facts import SITE_ACCEPTED_STATUSES

logger = logging.getLogger(__name__)

PHONE_TAG = "phone_source=school_site"
PHONE_WAS_PREFIX = "phone_was="
MAX_PHONES = 2

_SOFIA_LANDLINE = re.compile(r"2\d{7}")
_MOBILE = re.compile(r"(?:8[7-9]|9[89])\d{7}")
_FAX_BEFORE = re.compile(r"(?:факс|fax)\W{0,4}$", re.IGNORECASE)
_PHONE_FAX = re.compile(r"тел\W{0,3}(?:факс|fax)|phone\W{0,3}fax", re.IGNORECASE)
_SIDE_ROLE = re.compile(
    r"(?<![а-яa-z])(?:зам\.|заместник|deputy|психолог|счетовод|accountant|медицинск|домакин)", re.IGNORECASE
)
# A Sofia landline or a mobile, digits apart by at most one separator: exactly as many
# digits as the number has, so two numbers in a row stay two.
_PHONE = re.compile(
    r"(?<![\d+])(?:\+359|00359|0)\s?[-./]?\s?(?:2|8[7-9]|9[89])(?:\s?[-./]?\s?\d){7}(?!\d)"
)
# Where the next building's address starts on a list of several.
_NEXT_ADDRESS = re.compile(r"(?<![а-яa-z])(?:ул|бул|ж\.?\s?к|кв|пл|с)\.\s?|(?<![а-яa-z])улица\s", re.IGNORECASE)


def nsn(text: str) -> Optional[str]:
    """A number's national significant digits (no 0 or +359), when it is a Sofia landline
    or a Bulgarian mobile; else None."""
    digits = re.sub(r"\D", "", text)
    if match := re.fullmatch(r"(?:00359|359|0)(\d+)", digits):
        digits = match.group(1)
    else:
        return None
    return digits if _SOFIA_LANDLINE.fullmatch(digits) or _MOBILE.fullmatch(digits) else None


def stored_numbers(phone: Optional[str]) -> list[str]:
    """The numbers in a stored phone field ("02/824 11 70; 0888…", "02 987 65 43 / 0888…",
    "02/0887070435": a Sofia code glued onto a mobile is a register artefact)."""
    found = []
    for part in re.split(r"[;,]|\sи\s", phone or ""):
        text = part.strip().lstrip("/").strip()
        while re.match(r"02\s*/\s*[0+]", text):
            text = re.sub(r"^02\s*/\s*", "", text)
        for match in _PHONE.finditer(text):
            if (number := nsn(match.group())) and number not in found:
                found.append(number)
    return found


def page_numbers(text: str) -> list[str]:
    """The school's phones a page gives, in page order: Sofia landlines and mobiles. A fax
    (not a "тел./факс") is left out, and so are a deputy's, the psychologist's or the
    accountant's when the page gives another."""
    main, side = [], []
    last_end, is_side, is_fax = 0, False, False
    for match in _PHONE.finditer(text or ""):
        # The words between the previous number and this one are this number's label; a
        # number after only a comma shares the previous one's ("Зам.: 02 945 2906, 02 845 2682").
        label = text[max(last_end, match.start() - 40): match.start()]
        last_end = match.end()
        if re.search(r"[^\W\d_]", label):
            is_side = bool(_SIDE_ROLE.search(label))
            is_fax = bool(_FAX_BEFORE.search(label)) and not _PHONE_FAX.search(label)
        if is_fax:
            continue
        number = nsn(match.group())
        if not number or number in main or number in side:
            continue
        (side if is_side else main).append(number)
    return main or side


def numbers_at(text: str, address: str) -> list[str]:
    """The phones a page gives right after it names our address (a site listing several
    buildings, each with its own number); empty when it does not name it."""
    from app.services.geocoding.bg import sofia_address_register as register_lib

    parsed = register_lib.parse(address)
    if not (parsed.street and parsed.number):
        return []
    words = [w for w in parsed.street.split()[1:] if len(w) > 1][-1:]
    if not words:
        return []
    split = re.fullmatch(r"(\d+)(\D*)", parsed.number)
    if split is None:  # the parser gives digits and a letter; anything else as written
        number = re.escape(parsed.number.casefold())
    else:
        digits, letter = split.groups()
        number = re.escape(digits) + (rf"[\s-]?{re.escape(letter.casefold())}" if letter else "")
    pattern = rf"(?<!\w){re.escape(words[0])}\W{{1,6}}(?:№\s*)?{number}(?![\w/-])"
    lowered = (text or "").casefold()
    for found in re.finditer(pattern, lowered):
        following = text[found.end(): found.end() + 160]
        if stop := _NEXT_ADDRESS.search(following):
            following = following[: stop.start()]  # the next building's numbers are not ours
        if numbers := page_numbers(following):
            return numbers
    return []


def _site_key(url: str) -> str:
    """A website's identity, a host of many sites' tenant counted apart (as the shared-site
    check counts them)."""
    from app.scrapers.shared_site_check import site_group_key

    return site_group_key(url) or url


def display(number: str) -> str:
    """"02 987 6543", "0888 123 456"."""
    if number.startswith("2"):
        return f"02 {number[1:4]} {number[4:]}"
    return f"0{number[:3]} {number[3:6]} {number[6:]}"


async def run_site_phones(
    db: AsyncSession,
    *,
    country: str = "bg",
    city: str = "sofia",
    school_ids: Sequence[int] = (),
    limit: Optional[int] = None,
    apply: bool = False,
    fetch: Optional[Fetch] = None,
) -> dict[str, Any]:
    """Run over the city's schools with an accepted website; returns the report."""
    in_city = (School.country_code == country, func.lower(School.city) == city.lower())
    query = (
        select(School.id, School.website_url)
        .where(*in_city, School.website_url.is_not(None), School.scrape_status.in_(SITE_ACCEPTED_STATUSES))
        .order_by(School.id)
    )
    if school_ids:
        query = query.where(School.id.in_(list(school_ids)))
    schools = list((await db.execute(query)).all())[:limit]
    accepted = (
        await db.execute(
            select(School.website_url).where(
                *in_city, School.website_url.is_not(None), School.scrape_status.in_(SITE_ACCEPTED_STATUSES)
            )
        )
    ).scalars()
    sites = Counter(_site_key(url) for url in accepted)
    # Other schools' phones: a site giving one of them is an umbrella body's or a designer's.
    others: dict[str, set[int]] = {}
    in_city_ids = select(School.id).where(*in_city)
    rows = (
        await db.execute(
            select(SchoolLocation.school_id, SchoolLocation.id, SchoolLocation.phone, SchoolLocation.address_i18n, SchoolLocation.is_primary)
            .where(SchoolLocation.school_id.in_(in_city_ids))
            .order_by(SchoolLocation.id)
        )
    ).all()
    primaries: dict[int, list[tuple]] = {}
    for school_id, location_id, phone, address_i18n, is_primary in rows:
        for number in stored_numbers(phone):
            others.setdefault(number, set()).add(school_id)
        if is_primary:
            primaries.setdefault(school_id, []).append((location_id, phone, address_i18n))
    plans = [
        (school_id, website_url, *primaries[school_id][0])
        for school_id, website_url in schools
        if len(primaries.get(school_id, [])) == 1
    ]
    await db.rollback()  # no transaction stays open through the fetches
    limited = bool(school_ids) or limit is not None

    async def _run(fetch: Fetch) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for school_id, website_url, location_id, phone, address_i18n in plans:
            row: dict[str, Any] = {"school_id": school_id, "location_id": location_id, "phone": phone}
            try:
                site = await read_site(SimpleNamespace(website_url=website_url), fetch)
            except Exception:
                logger.exception("site-phones could not read school %s's site", school_id)
                site = None
            if site is None:
                results.append({**row, "outcome": "site_not_read"})
                continue
            numbers = numbers_at(site[1], (address_i18n or {}).get("bg") or "")
            row["at_our_address"] = bool(numbers)
            shared = sites[_site_key(website_url)] > 1
            if not numbers and not shared:
                numbers = page_numbers(site[1])
            row["site_phones"] = [display(n) for n in numbers]
            if not numbers:
                # A site several of our schools share: only a number under our own address
                # is ours.
                outcome = "shared_site" if shared else "no_site_phone"
            elif set(stored_numbers(phone)) & set(numbers):
                outcome = "agrees"
            elif not (own := [n for n in numbers if not others.get(n, set()) - {school_id}]):
                outcome = "another_schools_phone"
            elif limited and not row["at_our_address"]:
                # Whether a page's number is a designer's shows only across the city's sites.
                outcome = "needs_full_run"
                row["new_phone"] = "; ".join(display(n) for n in own[:MAX_PHONES])
            else:
                outcome = "replace"
                row["new_phone"] = "; ".join(display(n) for n in own[:MAX_PHONES])
            results.append({**row, "outcome": outcome})
        # A number first on two schools' sites (of those read in this run) is a designer's
        # or an umbrella body's, not the school's.
        firsts = Counter(
            r["site_phones"][0] for r in results if r.get("site_phones") and not r.get("at_our_address")
        )
        for row in results:
            if row["outcome"] == "replace" and not row["at_our_address"] and firsts[row["site_phones"][0]] > 1:
                row["outcome"] = "phone_on_other_sites"
        return results

    if fetch is None:
        from crawl4ai.utils import RobotsParser  # type: ignore

        async with fee_http_client() as client:
            results = await _run(_http_fetch(client, RobotsParser()))
    else:
        results = await _run(fetch)
    if apply:
        for result in results:
            if result["outcome"] == "replace":
                result["outcome"] = await _write(db, result["location_id"], result["phone"], result["new_phone"])
    outcomes: dict[str, int] = {}
    for result in results:
        outcomes[result["outcome"]] = outcomes.get(result["outcome"], 0) + 1
    return {"apply": apply, "locations": len(results), "outcomes": outcomes, "results": results}


async def _write(db: AsyncSession, location_id: int, read_phone: Optional[str], new_phone: str) -> str:
    """Set the phone under a row lock, unless it changed since it was read."""
    location = await db.get(SchoolLocation, location_id)
    if location is None:
        return "not_found"
    await db.refresh(location, with_for_update=True)
    if location.phone != read_phone:
        await db.rollback()
        return "changed_meanwhile"
    tags = [str(t) for t in location.location_tags or []]
    if PHONE_TAG not in tags:  # the register's phone, kept for an undo
        tags = [t for t in tags if not t.startswith(PHONE_WAS_PREFIX)] + [PHONE_TAG, PHONE_WAS_PREFIX + (location.phone or "")]
    location.phone = new_phone
    location.location_tags = tags
    db.add(location)
    await db.commit()
    return "replaced"


def restore_register_phone(location: SchoolLocation) -> bool:
    """Put back the phone site-phones replaced (a wrong website); whether it did."""
    tags = [str(t) for t in location.location_tags or []]
    if PHONE_TAG not in tags:
        return False
    was = next((t[len(PHONE_WAS_PREFIX):] for t in tags if t.startswith(PHONE_WAS_PREFIX)), "")
    location.phone = was or None
    location.location_tags = [t for t in tags if t != PHONE_TAG and not t.startswith(PHONE_WAS_PREFIX)]
    return True


def note_register_phone(location: SchoolLocation, register_phone: Optional[str]) -> None:
    """A register re-import of a location showing its site's phone: the register's current
    phone becomes the one a wrong-website undo puts back."""
    tags = [str(t) for t in location.location_tags or []]
    if PHONE_TAG in tags and register_phone:
        location.location_tags = [t for t in tags if not t.startswith(PHONE_WAS_PREFIX)] + [PHONE_WAS_PREFIX + register_phone]


def carry_site_phone(phone: Optional[str], tags: Sequence[str], site_phone: Optional[str]) -> tuple[Optional[str], list[str]]:
    """The phone and tags of a location a re-import recreates, when the row it replaces
    showed the site's ``site_phone``."""
    if not site_phone:
        return phone, list(tags)
    return site_phone, [*tags, PHONE_TAG, PHONE_WAS_PREFIX + (phone or "")]
