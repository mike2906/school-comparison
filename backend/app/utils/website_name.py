"""The name a school's own website writes in Latin letters, and when it may be shown.

The website-name stage stores ``attributes["website_name"]`` (UWEKIND for Увекинд, Busy
School for Бизи). Parents search for a brand as the school writes it, and our derived
English name transliterates the register name ("Uvekind", "Bizi"), so the English page
shows the website's own name and search finds it (``app.utils.i18n_resolver``).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Optional
from urllib.parse import urlparse

from app.utils.website_data import SITE_ACCEPTED_STATUSES, website_data_withheld_reasons

SCHEMA_VERSION = 1
ATTRIBUTE_KEY = "website_name"
# Where ``app.utils.school_facts.publication_attributes`` puts a name that may be shown;
# ``attributes_for_publication`` removes both keys from anything else.
PUBLISHED_KEY = "website_name_published"

MAX_LENGTH = 80
# Latin letters (with accents: Lycée), digits and the punctuation names use.
_LATIN_NAME_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9&'’.,\- ]+")
_WORD_RE = re.compile(r"[a-zà-öø-ÿ0-9]+")
# Words any school's name may have: a name sharing only these with its site's address or
# register name is not shown to be this school's.
_GENERIC_WORDS = frozenset(
    {
        "the",
        "of",
        "and",
        "for",
        "de",
        "a",
        "school",
        "schools",
        "kindergarten",
        "kindergartens",
        "nursery",
        "preschool",
        "kita",
        "private",
        "international",
        "academy",
        "college",
        "high",
        "primary",
        "secondary",
        "language",
        "english",
        "german",
        "french",
        "home",
        "center",
        "centre",
        "group",
        "kids",
        "sofia",
        "bulgaria",
        "bg",
        "www",
        "com",
        "org",
        "net",
        "eu",
    }
)


def _site(url: Any) -> str:
    # The same rule as ``app.utils.school_facts._site`` (that module imports this one).
    from app.utils.school_facts import _site as site

    return site(url)


def is_latin_name(name: Any) -> bool:
    """A name in Latin letters with at least two letters, and not too long to be a name."""
    if not isinstance(name, str):
        return False
    name = name.strip()
    return (
        0 < len(name) <= MAX_LENGTH
        and bool(_LATIN_NAME_RE.fullmatch(name))
        and sum(char.isalpha() for char in name) >= 2
    )


def distinctive_words(name: str) -> list[str]:
    """The name's words of three or more characters that are not generic school words."""
    return [word for word in _WORD_RE.findall(name.casefold()) if len(word) >= 3 and word not in _GENERIC_WORDS]


def host(url: Optional[str]) -> str:
    """The URL's host without "www.", lowercase."""
    return (urlparse(url or "").hostname or "").casefold().removeprefix("www.")


def matches_site_or_register(name: str, website_url: Optional[str], register_names: list[str]) -> bool:
    """Whether a distinctive word of ``name`` is in the website's address (busy-school.com
    for Busy School) or matches the register name the way search would (Drujba for Дружба).

    The reader could otherwise take an operator's, a template's or a network's name ("My
    Blog", "Kidscool Site") from the page. A three-letter word must start a part of the
    address ("abc" in abckinder.org, not "art" in smartkids.bg)."""
    from app.utils import school_search

    labels = re.split(r"[^a-z0-9]+", host(website_url).rsplit(".", 1)[0])
    host_letters = "".join(labels)
    words = distinctive_words(name)
    if any(
        word in host_letters if len(word) >= 4 else any(label.startswith(word) for label in labels) for word in words
    ):
        return True
    return any(
        school_search.rank_school([word], registry_name=register, names=[register]) is not None
        for word in words
        if len(word) >= 4
        for register in register_names
        if register
    )


# Common endings of a web address, besides the site's own (".com" in "izgrevou.com").
_TLDS = frozenset({"com", "bg", "eu", "org", "net", "info", "biz", "edu", "academy", "school", "space"})
# Second-level parts of an address ending (wonderhouse-bg.com.bg).
_SECOND_LEVEL = frozenset({"com", "org", "net", "edu", "co"})


def is_web_address(name: Any, website_url: Optional[str]) -> bool:
    """A domain ("izgrevou.com", "Eduteh.EU") or the address's handle with a country or city
    tag ("Wonderhouse-bg", "Lincolnsofia"): the reader took the address for the name. A
    brand that is its address ("Kidsville" for kidsville.space) is still a name, and so is
    an abbreviation with a dot ("St.George")."""
    if not isinstance(name, str):
        return False
    name = name.strip()
    site = host(website_url)
    tlds = _TLDS | {site.rsplit(".", 1)[-1]}
    if (match := re.fullmatch(r"[\w-]+((?:\.[a-z]{2,8})+)", name, flags=re.IGNORECASE)) and all(
        part.casefold() in tlds for part in match.group(1).split(".")[1:]
    ):
        return True
    labels = site.split(".")[:-1]
    while len(labels) > 1 and labels[-1] in _SECOND_LEVEL:
        labels.pop()
    handle = re.sub(r"[^a-z0-9]", "", labels[-1]) if labels else ""
    letters = re.sub(r"[^a-z0-9]", "", name.casefold())
    return " " not in name and letters == handle and bool(re.search(r"(?:bg|sofia|eu)$", handle))


_KINDERGARTEN_WORDS = frozenset({"kindergarten", "kindergartens", "nursery", "preschool", "kita", "daycare"})
_SCHOOL_WORDS = frozenset({"school", "schools", "college", "lyceum", "lycee", "lycée", "gymnasium", "high"})


def names_other_level(name: str, family: str) -> bool:
    """Whether ``name`` names only the other level: "Uwekind International School" for the
    kindergarten on uwekind.com. A name with both ("School and Preschool") or neither (the
    brand alone) is not."""
    words = set(_WORD_RE.findall(name.casefold()))
    kindergarten, school = bool(words & _KINDERGARTEN_WORDS), bool(words & _SCHOOL_WORDS)
    return school and not kindergarten if family == "kindergarten" else kindergarten and not school


# Hosts of other people's sites: their address names the platform, not the school.
_PLATFORM_DOMAINS = frozenset(
    {"facebook.com", "instagram.com", "google.com", "wixsite.com", "blogspot.com", "wordpress.com", "weebly.com"}
)


def address_name(website_url: Optional[str], scrape_status: Any) -> Optional[str]:
    """The website's address as words for search ("busy school" for busy-school.com,
    "questjunior" for questjunior.bg), for an accepted website only.

    Many brands are written on the site only in the logo picture, so the address is the
    one place the Latin name is in text. Search matches words inside it ("quest" finds
    "questjunior"). Not shown anywhere."""
    if str(scrape_status or "").strip().lower() not in SITE_ACCEPTED_STATUSES:
        return None
    name = host(website_url)
    if ".".join(name.split(".")[-2:]) in _PLATFORM_DOMAINS:
        return None
    labels = name.split(".")[:-1]  # without the top-level domain
    words = " ".join(re.sub(r"[^a-z0-9]+", " ", label) for label in labels).split()
    return " ".join(words) or None


def shown_website_name(attributes: Any, scrape_status: Any, website_url: Optional[str]) -> Optional[str]:
    """The stored website name, or None.

    Shown under the school-facts rules: no website-data withholding marker, a website URL
    validation accepted, and read from the school's current website."""
    block = attributes.get(ATTRIBUTE_KEY) if isinstance(attributes, Mapping) else None
    if not isinstance(block, Mapping) or block.get("_schema_version") != SCHEMA_VERSION:
        return None
    if "withheld_marker" in website_data_withheld_reasons(attributes, scrape_status):
        return None
    if str(scrape_status or "").strip().lower() not in SITE_ACCEPTED_STATUSES:
        return None
    if not _site(website_url) or _site(block.get("website_url")) != _site(website_url):
        return None
    name = block.get("name")
    return name.strip() if is_latin_name(name) and not is_web_address(name, website_url) else None
