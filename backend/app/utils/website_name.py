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


def matches_site_or_register(name: str, website_url: Optional[str], register_names: list[str]) -> bool:
    """Whether a distinctive word of ``name`` is in the website's address (busy-school.com
    for Busy School) or matches the register name the way search would (Drujba for Дружба).

    The reader could otherwise take an operator's, a template's or a network's name ("My
    Blog", "Kidscool Site") from the page."""
    from app.utils import school_search

    host = (urlparse(website_url or "").hostname or "").casefold().removeprefix("www.")
    host_letters = re.sub(r"[^a-z0-9]", "", host.rsplit(".", 1)[0])
    words = distinctive_words(name)
    if any(word in host_letters for word in words):
        return True
    return any(
        school_search.rank_school([word], registry_name=register, names=[register]) is not None
        for word in words
        for register in register_names
        if register
    )


def address_name(website_url: Optional[str], scrape_status: Any) -> Optional[str]:
    """The website's address as words for search ("busy school" for busy-school.com,
    "questjunior" for questjunior.bg), for an accepted website only.

    Many brands are written on the site only in the logo picture, so the address is the
    one place the Latin name is in text. Search matches words inside it ("quest" finds
    "questjunior"). Not shown anywhere."""
    if str(scrape_status or "").strip().lower() not in SITE_ACCEPTED_STATUSES:
        return None
    host = (urlparse(website_url or "").hostname or "").casefold().removeprefix("www.")
    labels = host.split(".")[:-1]  # without the top-level domain
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
    return name.strip() if is_latin_name(name) else None
