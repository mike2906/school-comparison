"""Pages that are not a school's own content: a template's sample text, a parked domain."""

from __future__ import annotations

import re

# Words of the Latin sample text site templates ship with, picked so none is an English or
# Bulgarian word ("sit", "ut" and "et" are left out).
_FILLER_WORDS = frozenset(
    """lorem ipsum dolor amet consectetur consectetuer adipiscing adipisicing elit eiusmod tempor
    incididunt labore dolore aliqua veniam nostrud exercitation ullamco laboris nisi aliquip
    commodo consequat duis aute irure reprehenderit voluptate velit esse cillum fugiat nulla
    pariatur excepteur occaecat cupidatat proident officia deserunt mollit pellentesque
    vestibulum scelerisque lobortis malesuada pulvinar praesent pretium platea fusce neque tellus
    luctus ullamcorper mattis dapibus mauris aenean sapien nibh tincidunt egestas ultricies
    tristique viverra faucibus suscipit mollis porttitor sagittis lectus metus nunc""".split()
)
# A page is the template's own when its sample text is this large a share of its words.
# A real page that kept a lorem paragraph, an Elementor "I am text block" or a Bulgarian
# note explaining what Lorem Ipsum is stays well under it.
_FILLER_SHARE = 0.25
_MIN_FILLER_WORDS = 8

# A registrar's or broker's page on a lapsed domain (551's meridian22.com on HugeDomains).
_PARKED_DOMAIN_RE = re.compile(
    r"\bhugedomains\b|\bthis domain (?:name )?(?:is|may be) for sale\b|\bbuy this domain\b"
    r"|\bdomain (?:is )?parked\b|\bparked (?:free|domain)\b|\bsedo(?:parking)?\.com\b|\bdan\.com\b"
    r"|домейнът е за продажба",
    re.IGNORECASE,
)


# Not the page's own words: an image or link's text (506 captions its photos "Lorem Ipsum
# Dolor"; 195 links "Reference site about Lorem Ipsum") and a URL or path (298's carousel
# slug "24-lorem-ipsum").
_NOT_PAGE_TEXT_RE = re.compile(r"!?\[[^\]]*\]\([^)]*\)|\S*[/\\]\S*")
_WORD_RE = re.compile(r"[^\W\d_]+")


def is_template_filler(text: str | None) -> bool:
    """A theme's sample page (590), not a real page that kept some sample text."""
    words = _WORD_RE.findall(_NOT_PAGE_TEXT_RE.sub(" ", text or ""))
    filler = sum(word.casefold() in _FILLER_WORDS for word in words)
    return filler >= _MIN_FILLER_WORDS and filler >= _FILLER_SHARE * len(words)


def is_parked_domain(text: str | None) -> bool:
    return bool(_PARKED_DOMAIN_RE.search(text or ""))
