"""Pages that are not a school's own content: a template's sample text, a parked domain."""

from __future__ import annotations

import re

# Sample text a site template ships with. Two different words of it make a page filler.
_TEMPLATE_FILLER_RE = re.compile(
    r"\b(?:lorem|ipsum|pellentesque|vestibulum|scelerisque|consectetur|adipiscing|lobortis|malesuada)\b",
    re.IGNORECASE,
)

# A registrar's or broker's page on a lapsed domain (551's meridian22.com on HugeDomains).
_PARKED_DOMAIN_RE = re.compile(
    r"\bhugedomains\b|\bthis domain (?:name )?(?:is|may be) for sale\b|\bbuy this domain\b"
    r"|\bdomain (?:is )?parked\b|\bparked (?:free|domain)\b|\bsedo(?:parking)?\.com\b|\bdan\.com\b"
    r"|домейнът е за продажба",
    re.IGNORECASE,
)


# An image or link's text is not the page's: aas-sofia.org (506) captions its photos
# "Lorem Ipsum Dolor" on real pages.
_MARKUP_TEXT_RE = re.compile(r"!?\[[^\]]*\]\([^)]*\)")


def is_template_filler(text: str | None) -> bool:
    words = _TEMPLATE_FILLER_RE.findall(_MARKUP_TEXT_RE.sub(" ", text or ""))
    return len({word.casefold() for word in words}) >= 2


def is_parked_domain(text: str | None) -> bool:
    return bool(_PARKED_DOMAIN_RE.search(text or ""))
