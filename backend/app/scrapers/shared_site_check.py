"""Shared-site check after website discovery (UF42 step a).

Several institutions can end up with websites on one registrable domain: a
kindergarten and its sibling school, the campuses of one brand, or a brand hub
(``maplebear.bg``) that links campus sites. Page text alone cannot tell a
kindergarten from its sibling school on one domain, so for every group of two or
more institutions sharing a domain, a site is accepted for an institution only
with evidence tying it to that institution:

* the site states the institution's registry address (street + number, or
  quarter + block), and
* the site describes the institution's education level (a kindergarten keeps a
  site that talks about a kindergarten, a school one that talks about a school),
  and the URL's own subdomain/path does not name the other level
  (``sofia-school.<brand>`` is not a kindergarten's site).

A brand hub is never kept: when the domain root links campus subdomains, the
campus whose pages state the institution's address and level replaces the hub
(or the wrong campus), if exactly one does. Otherwise the website is withheld
through the existing URL-validation withholding state (no website beats a
wrong one).

Nothing here is city-specific; the Bulgarian terms cover the country's language
and English covers bilingual sites.
"""

from __future__ import annotations

import datetime
import functools
import logging
import re
import unicodedata
from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import unquote, urljoin, urlparse

from app.services.geocoding.write_gate import OFFICIAL_COORDS_TAG
from app.utils.transliteration import transliterate_bulgarian
from app.utils.website_data import WEBSITE_DATA_WITHHELD_KEY

logger = logging.getLogger(__name__)

KEEP = "keep"
REPLACE = "replace"
WITHHOLD = "withhold"

WEBSITE_CONTACT_ADDRESS_TAG = "address_source=website_contact"
SHARED_SITE_CHECK_KEY = "shared_site_check"

KINDERGARTEN_LEVELS = frozenset({"kindergarten", "nursery"})

# Terms that show a site describes a kindergarten / a school. Bare "school" and
# "училище" are deliberately absent: kindergartens talk about school readiness.
_KINDERGARTEN_TERMS = (
    "детска градина",
    "детската градина",
    "детски градини",
    "детските градини",
    "детска ясла",
    "яслена група",
    "яслени групи",
    "kindergarten",
    "nursery",
)
_SCHOOL_TERM_RE = re.compile(
    r"основно училище|средно училище|начално училище|частно училище|езиково училище"
    r"|гимнази|прогимназ|начален етап"
    # A single grade is kindergarten wording too ("подготовка за 1 клас"); a span of
    # grades ("1 - 7 клас", "Grades 1-12") is what a school says about itself.
    r"|\b(?:[1-9]|1[0-2])\.?\s*(?:-|–|до)\s*(?:[1-9]|1[0-2])\.?\s*клас"
    r"|high school|middle school|primary school|elementary school|secondary school"
    r"|\bgrades?\s*(?:[1-9]|1[0-2])\s*(?:-|–|to)\s*(?:grade\s*)?(?:[1-9]|1[0-2])\b",
    re.IGNORECASE,
)

# Level words in a URL's subdomain labels or path (not in the registrable domain,
# which combined sites such as ``britanica-parkschool.bg`` share).
_URL_SCHOOL_RE = re.compile(r"(?<!pre)(?<!pre-)(?<!pre_)school|uchilishte|uchilishhe|gimnazi|училищ|гимназ")
_URL_KINDERGARTEN_RE = re.compile(r"kindergarten|gradina|nursery|detska|pre-?school|градин|ясла")

_CONTACT_LINK_RE = re.compile(r"contact|kontakt|контакт|за-нас|za-nas|about", re.IGNORECASE)

# Address words that never identify a street on their own.
_ADDRESS_STOP_WORDS = frozenset(
    {
        "ул", "улица", "бул", "булевард", "пл", "площад", "жк", "кв", "квартал", "гр", "град",
        "с", "село", "район", "бл", "блок", "вх", "ет", "ап", "до", "и",
        "проф", "професор", "д", "р", "др", "св", "свети", "ген", "генерал", "майор",
        "акад", "академик", "цар", "хан", "кн", "княз", "инж", "полк",
        "софия", "sofia", "българия", "bulgaria",
    }
)

MAX_CAMPUS_CANDIDATES = 12
MAX_CONTACT_PAGES = 3
NUMBER_WINDOW = 6


# ---------------------------------------------------------------------------
# Text normalisation
# ---------------------------------------------------------------------------

@functools.lru_cache(maxsize=65536)
def _skeleton(word: str) -> str:
    """Script- and spelling-independent key for one word.

    ``Йордан``/``Yordan``/``Jordan`` and ``Стубел``/``Stubel`` must meet, so the
    word is transliterated, reduced to ASCII letters and stripped of vowels and
    the letters transliteration systems disagree on (й/y/j/i).
    """
    text = transliterate_bulgarian(word or "").casefold()
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z]", "", text)
    for source, target in (("dzh", "j"), ("zh", "j"), ("ph", "f"), ("tz", "c"), ("ts", "c"), ("ck", "k"), ("w", "v")):
        text = text.replace(source, target)
    text = re.sub(r"[aeiouyj]", "", text)
    return re.sub(r"(.)\1+", r"\1", text)


def _tokens(text: str) -> list[str]:
    """Word skeletons and bare numbers, in order ("№16а" -> "16")."""
    tokens: list[str] = []
    for raw in re.findall(r"[^\W_]+", text or ""):
        for part in re.findall(r"\d+|[^\W\d_]+", raw):
            if part.isdigit():
                tokens.append(str(int(part)))
            else:
                key = _skeleton(part)
                if key:
                    tokens.append(key)
    return tokens


def _distinctive_words(text: str) -> list[str]:
    words = []
    for raw in re.findall(r"[^\W\d_]+", text or ""):
        if len(raw) < 4 or raw.casefold() in _ADDRESS_STOP_WORDS:
            continue
        key = _skeleton(raw)
        if len(key) >= 2 and key not in words:
            words.append(key)
    return words


# ---------------------------------------------------------------------------
# Registry address -> matchable key
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AddressKey:
    """What a site must state to match one registry address."""

    kind: str  # street_number | street | quarter_block | quarter_number
    words: tuple[str, ...]
    number: Optional[str]
    source: str


_STREET_MARKER_RE = re.compile(
    r"(?:^|[\s,.])(?:улица|ул|булевард|бул|площад|пл)(?![^\W\d_])\s*\.?\s*", re.IGNORECASE
)
_QUARTER_MARKER_RE = re.compile(r"(?:^|[\s,.])(?:ж\s*\.?\s*к|кв)(?![^\W\d_])\s*\.?\s*", re.IGNORECASE)
_BLOCK_RE = re.compile(r"(?:^|[\s,.])бл\s*\.?\s*(\d+)", re.IGNORECASE)
_NUMBER_AFTER_NAME_RE = re.compile(r"[\s,\"'„“”«»]*(?:№|no\.?)?\s*(\d+)", re.IGNORECASE)
_QUOTES = "\"'„“”«»"


def address_key(address: str | None) -> Optional[AddressKey]:
    """Parse a registry address into the part a site must repeat.

    Returns None when the address is too vague to tie a site to it (a quarter
    without a number or block, or no street at all).
    """
    text = " ".join(str(address or "").split())
    if not text:
        return None

    street = _STREET_MARKER_RE.search(text)
    if street:
        rest = text[street.end():]
        stop = re.search(r"[,№]", rest)
        name = rest[: stop.start()] if stop else rest
        remainder = rest[len(name):]
        number_match = _NUMBER_AFTER_NAME_RE.match(remainder)
        number = number_match.group(1) if number_match else None
        name = name.strip(" " + _QUOTES)
        if number is None:
            trailing = re.search(r"\s(\d+)\s*[^\W\d_]?$", name)
            if trailing:
                number = trailing.group(1)
                name = name[: trailing.start()]
        name = name.strip(" " + _QUOTES)
        words = _distinctive_words(name)
        if not words:
            # Numbered streets (ул. "106" № 3) are identified by their number.
            street_number = re.findall(r"\d+", name)
            if street_number and number:
                return AddressKey("street_number", ("#" + str(int(street_number[0])),), str(int(number)), text)
            return None
        if number:
            return AddressKey("street_number", tuple(words), str(int(number)), text)
        return AddressKey("street", tuple(words), None, text)

    quarter = _QUARTER_MARKER_RE.search(text)
    if quarter:
        rest = text[quarter.end():]
        name = re.split(r",|\bбл\b|\bдо\b", rest, maxsplit=1)[0].strip(" " + _QUOTES)
        words = _distinctive_words(name)
        if not words:
            return None
        block = _BLOCK_RE.search(text)
        if block:
            return AddressKey("quarter_block", tuple(words), str(int(block.group(1))), text)
        quarter_number = re.search(r"(\d+)\s*$", name.replace("-", " ").strip(" " + _QUOTES))
        if quarter_number:
            return AddressKey("quarter_number", tuple(words), str(int(quarter_number.group(1))), text)
    return None


def address_matches(key: AddressKey, corpus_tokens: Sequence[str]) -> bool:
    """Does the site text state this address?"""
    if not corpus_tokens:
        return False
    words = [w for w in key.words if not w.startswith("#")]
    numbered_street = [w[1:] for w in key.words if w.startswith("#")]

    if key.kind == "street":
        size = len(words)
        return any(
            list(corpus_tokens[i : i + size]) == words
            for i in range(len(corpus_tokens) - size + 1)
        )

    window = 2 if key.kind == "quarter_number" else NUMBER_WINDOW
    for index, token in enumerate(corpus_tokens):
        if token != key.number:
            continue
        lo, hi = max(0, index - window), min(len(corpus_tokens), index + window + 1)
        nearby = set(corpus_tokens[lo:index]) | set(corpus_tokens[index + 1 : hi])
        if numbered_street:
            if all(n in nearby for n in numbered_street):
                return True
            continue
        if all(word in nearby for word in words):
            return True
    return False


# ---------------------------------------------------------------------------
# Education level
# ---------------------------------------------------------------------------

def level_family(education_level: str | None) -> str:
    return "kindergarten" if str(education_level or "").casefold() in KINDERGARTEN_LEVELS else "school"


def describes_level(text: str, family: str) -> bool:
    lowered = (text or "").casefold()
    if family == "kindergarten":
        return any(term in lowered for term in _KINDERGARTEN_TERMS)
    return bool(_SCHOOL_TERM_RE.search(lowered))


def url_names_other_level(url: str, family: str, registrable_domain: str | None) -> bool:
    """True when the subdomain/path names the other level and not this one."""
    parsed = urlparse(url or "")
    host = (parsed.netloc or "").casefold().split(":", 1)[0]
    subdomain = host
    if registrable_domain and host.endswith(registrable_domain):
        subdomain = host[: -len(registrable_domain)]
    own_part = f"{subdomain} {unquote(parsed.path or '').casefold()}"
    school_word = bool(_URL_SCHOOL_RE.search(own_part))
    kindergarten_word = bool(_URL_KINDERGARTEN_RE.search(own_part))
    if family == "kindergarten":
        return school_word and not kindergarten_word
    return kindergarten_word and not school_word


# ---------------------------------------------------------------------------
# Grouping
# ---------------------------------------------------------------------------

def registrable_domain(url: str | None) -> Optional[str]:
    from app.services.identity_adjudication import _registrable_domain

    return _registrable_domain(url)


def _host(url: str | None) -> str:
    host = (urlparse(str(url or "")).netloc or "").casefold().split(":", 1)[0]
    return host[4:] if host.startswith("www.") else host


# Path-hosted platforms: how many leading path segments name one tenant's site.
# Google Sites: /view/<site>, /site/<site>, /<workspace-domain>/<site>.
_PATH_TENANT_SEGMENTS = {"sites.google.com": 2}


def _is_platform_host(host: str) -> bool:
    """A host that is itself a (private) public suffix hosts other people's sites."""
    from app.scrapers import extractor

    return bool(host) and extractor._HOST_SUFFIX_EXTRACTOR(host).suffix == host


def site_id(url: str | None) -> str:
    """One website's identity: its host, or the tenant root on a path-hosted platform.

    On a platform whose tenant prefix is unknown the whole host counts as one site,
    so its tenants are grouped and checked rather than silently split.
    """
    host = _host(url)
    segments = _PATH_TENANT_SEGMENTS.get(host)
    if segments and _is_platform_host(host):
        parts = [p for p in (urlparse(str(url or "")).path or "").split("/") if p][:segments]
        if len(parts) == segments:
            return f"{host}/{'/'.join(parts).casefold()}"
    return host


def site_group_key(url: str | None) -> Optional[str]:
    """Registrable domain; on a path-hosted platform, the tenant's site (``site_id``)."""
    domain = registrable_domain(url)
    if not domain:
        return None
    host = _host(url)
    if domain == host and _is_platform_host(host):
        return site_id(url)
    return domain


def is_directory_url(url: str, country_code: str = "bg") -> bool:
    """A directory/listing page is never an institution's own site."""
    from app.scrapers.website_discovery import WebsiteDiscoverer

    return WebsiteDiscoverer(country_code=country_code)._is_non_official_candidate(url)


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@dataclass
class FetchedPage:
    url: str
    text: str
    links: list[tuple[str, str]] = field(default_factory=list)  # (absolute url, anchor text)


Fetcher = Callable[[str], Awaitable[Optional[FetchedPage]]]


async def http_fetch(url: str, *, timeout: float = 20.0) -> Optional[FetchedPage]:
    """Fetch one page as text plus its links (the URL validator's httpx + BeautifulSoup)."""
    import httpx
    from bs4 import BeautifulSoup

    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; SchoolComparisonBot/1.0)"},
        ) as client:
            response = await client.get(url)
    except httpx.HTTPError as exc:
        logger.info("Shared-site fetch failed for %s: %s", url, exc)
        return None
    if response.status_code >= 400:
        return None
    soup = BeautifulSoup(response.text, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    final_url = str(response.url)
    links = []
    for anchor in soup.find_all("a", href=True):
        href = str(anchor.get("href") or "").strip()
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        links.append((urljoin(final_url, href), anchor.get_text(" ", strip=True)))
    return FetchedPage(url=final_url, text=soup.get_text(" ", strip=True), links=links)


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------

@dataclass
class Member:
    school_id: int
    name: str
    school_type: str
    education_level: str
    website_url: str
    registry_addresses: list[str]
    website_derived_addresses: list[str] = field(default_factory=list)
    city: str = ""


@dataclass
class Decision:
    school_id: int
    action: str
    reason: str
    current_url: str
    new_url: Optional[str] = None
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class SiteEvidence:
    address: Optional[str]
    level_fit: bool
    url_level_conflict: bool
    pages: int

    @property
    def accepted(self) -> bool:
        return bool(self.address) and self.level_fit and not self.url_level_conflict

    def as_dict(self) -> dict[str, Any]:
        return {
            "address_matched": self.address,
            "level_fit": self.level_fit,
            "url_names_other_level": self.url_level_conflict,
            "pages": self.pages,
        }


def evaluate_site(member: Member, url: str, texts: Iterable[str], domain: Optional[str]) -> SiteEvidence:
    texts = [t for t in texts if t]
    corpus = "\n".join(texts)
    tokens = _tokens(corpus)
    matched = None
    for address in member.registry_addresses:
        key = address_key(address)
        if key and address_matches(key, tokens):
            matched = address
            break
    family = level_family(member.education_level)
    return SiteEvidence(
        address=matched,
        level_fit=describes_level(corpus, family),
        url_level_conflict=url_names_other_level(url, family, domain),
        pages=len(texts),
    )


class SiteReader:
    """Collects page text per host: stored crawl pages plus live home/contact pages."""

    def __init__(self, fetcher: Fetcher, stored_texts_by_host: Mapping[str, list[str]] | None = None):
        self._fetcher = fetcher
        self._stored = {k: list(v) for k, v in (stored_texts_by_host or {}).items()}
        self._pages: dict[str, Optional[FetchedPage]] = {}
        self._site_texts: dict[str, list[str]] = {}

    async def page(self, url: str) -> Optional[FetchedPage]:
        if url not in self._pages:
            try:
                self._pages[url] = await self._fetcher(url)
            except Exception as exc:  # a broken site must not stop the check
                logger.info("Shared-site fetch error for %s: %s", url, exc)
                self._pages[url] = None
        return self._pages[url]

    async def site_texts(self, url: str) -> list[str]:
        """Text of the site at ``url``: stored pages for its host, the page, its contact pages."""
        if url in self._site_texts:
            return self._site_texts[url]
        site = site_id(url)
        texts = list(self._stored.get(site, []))
        home = await self.page(url)
        if home is not None:
            texts.append(home.text)
            contact_urls = []
            for link, anchor in home.links:
                if site_id(link) != site or link in contact_urls:
                    continue
                if _CONTACT_LINK_RE.search(unquote(urlparse(link).path)) or _CONTACT_LINK_RE.search(anchor or ""):
                    contact_urls.append(link)
            for link in contact_urls[:MAX_CONTACT_PAGES]:
                page = await self.page(link)
                if page is not None:
                    texts.append(page.text)
        self._site_texts[url] = texts
        return texts


def _site_root(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme or 'https'}://{parsed.netloc}/"


def _is_campus_label(label: str, city: str) -> bool:
    """A campus subdomain names a city or a level (``sofia-school``, ``plovdiv-markovo``).

    Shop, blog, anniversary or language subdomains (``store``, ``20years``, ``bg``)
    are not campuses even when their footer repeats the brand's address.
    """
    city_key = re.sub(r"[^a-z]", "", transliterate_bulgarian(city or "").casefold())
    compact = re.sub(r"[^a-z]", "", label)
    if city_key and city_key in compact:
        return True
    return bool(_URL_SCHOOL_RE.search(label) or _URL_KINDERGARTEN_RE.search(label))


async def campus_links(reader: SiteReader, domain: str, city: str) -> list[str]:
    """Campus sites a brand hub at the domain root links to.

    A hub is a domain root that links at least two other subdomains of its own
    domain; the member's campuses are those whose subdomain names its city or a
    level. Returns an empty list when the root is not a hub.
    """
    home = await reader.page(f"https://{domain}/")
    if home is None:
        return []
    subdomains: list[str] = []
    for link, _ in home.links:
        parsed = urlparse(link)
        if parsed.scheme not in {"http", "https"}:
            continue
        host = _host(link)
        if host == domain or not host.endswith("." + domain):
            continue
        root = _site_root(link)
        if root not in subdomains:
            subdomains.append(root)
    if len(subdomains) < 2:
        return []
    # The member's city may have a single campus among several cities' campuses.
    campuses = [
        root for root in subdomains
        if _is_campus_label(_host(root)[: -len(domain) - 1], city)
    ]
    return campuses[:MAX_CAMPUS_CANDIDATES]


async def decide_member(
    member: Member,
    *,
    domain: str,
    reader: SiteReader,
    country_code: str = "bg",
) -> Decision:
    """Keep, replace (hub -> campus) or withhold one institution's shared-domain site."""
    url = member.website_url
    evidence: dict[str, Any] = {"registry_addresses": member.registry_addresses}
    if member.website_derived_addresses:
        evidence["ignored_website_derived_addresses"] = member.website_derived_addresses

    if is_directory_url(url, country_code):
        return Decision(member.school_id, WITHHOLD, "directory_listing_not_official_site", url, evidence=evidence)
    if not member.registry_addresses:
        return Decision(member.school_id, WITHHOLD, "no_independent_registry_address", url, evidence=evidence)
    if not any(address_key(address) for address in member.registry_addresses):
        return Decision(member.school_id, WITHHOLD, "registry_address_too_vague", url, evidence=evidence)

    campuses = await campus_links(reader, domain, member.city) if domain else []
    on_hub = bool(campuses) and _host(url) == domain

    own = evaluate_site(member, url, await reader.site_texts(url), domain)
    evidence["current_site"] = own.as_dict()
    # A brand hub may list every campus address; it is never one institution's site.
    if own.accepted and not on_hub:
        return Decision(member.school_id, KEEP, "address_and_level_match", url, evidence=evidence)
    if on_hub:
        evidence["current_site_is_brand_hub"] = True

    matches: list[str] = []
    checked: dict[str, Any] = {}
    for campus in campuses:
        if site_id(campus) == site_id(url):
            continue
        campus_evidence = evaluate_site(member, campus, await reader.site_texts(campus), domain)
        checked[campus] = campus_evidence.as_dict()
        if campus_evidence.accepted:
            matches.append(campus)
    if checked:
        evidence["campus_sites"] = checked
    if len(matches) == 1:
        return Decision(member.school_id, REPLACE, "campus_site_matches_address_and_level", url, matches[0], evidence)
    if len(matches) > 1:
        return Decision(member.school_id, WITHHOLD, "several_campus_sites_match", url, evidence=evidence)
    if on_hub:
        reason = "brand_hub_no_matching_campus"
    elif own.url_level_conflict:
        reason = "url_names_other_level"
    elif not own.address:
        reason = "registry_address_not_on_site"
    else:
        reason = "site_does_not_describe_level"
    return Decision(member.school_id, WITHHOLD, reason, url, evidence=evidence)


def shared_groups(members: Iterable[Member]) -> dict[str, list[Member]]:
    grouped: dict[str, list[Member]] = defaultdict(list)
    for member in members:
        key = site_group_key(member.website_url)
        if key:
            grouped[key].append(member)
    return {key: sorted(items, key=lambda m: m.school_id) for key, items in grouped.items() if len(items) > 1}


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def _member_from_school(school) -> Member:
    registry, derived = [], []
    for location in sorted(school.locations or [], key=lambda loc: (not loc.is_primary, loc.id or 0)):
        address = (location.address_i18n or {}).get("bg") or (location.address_i18n or {}).get("en")
        if not address:
            continue
        tags = location.location_tags or []
        # A contact address copied from the site cannot be evidence for that site,
        # unless an official register point has since confirmed it.
        if WEBSITE_CONTACT_ADDRESS_TAG in tags and OFFICIAL_COORDS_TAG not in tags:
            derived.append(address)
        else:
            registry.append(address)
    return Member(
        school_id=int(school.id),
        name=str((school.name_i18n or {}).get("bg") or (school.name_i18n or {}).get("en") or ""),
        school_type=str(school.school_type or ""),
        education_level=str(school.education_level or ""),
        website_url=str(school.website_url),
        registry_addresses=registry,
        website_derived_addresses=derived,
        city=str(school.city or ""),
    )


async def _stored_texts_by_host(db, school_ids: Sequence[int]) -> dict[str, list[str]]:
    from sqlalchemy import select

    from app.models import SourcePage
    from app.models.scrape_log import ScrapeType

    rows = (
        await db.execute(
            select(SourcePage.source_url, SourcePage.raw_markdown).where(
                SourcePage.school_id.in_(list(school_ids)),
                SourcePage.scrape_type == ScrapeType.WEBSITE,
                SourcePage.is_valid.is_(True),
                SourcePage.raw_markdown.isnot(None),
            )
        )
    ).all()
    by_host: dict[str, list[str]] = defaultdict(list)
    seen: set[tuple[str, str]] = set()
    for source_url, markdown in rows:
        site = site_id(source_url)
        marker = (site, (markdown or "")[:500])
        if marker in seen:
            continue
        seen.add(marker)
        by_host[site].append(markdown or "")
    return dict(by_host)


def _withhold(school) -> None:
    """Same state URL validation leaves behind for a website it could not accept."""
    attrs = dict(school.attributes or {})
    attrs[WEBSITE_DATA_WITHHELD_KEY] = True
    attrs.pop("validated_website_url", None)
    school.attributes = attrs
    school.scrape_status = "failed_validate"
    school.website_url = None


async def _invalidate_host_pages(db, school_id: int, url: str) -> int:
    """Mark the school's crawled pages on that host invalid (withholds their prices)."""
    from sqlalchemy import select

    from app.models import SourcePage
    from app.models.scrape_log import ScrapeType

    site = site_id(url)
    count = 0
    pages = (
        await db.execute(
            select(SourcePage).where(
                SourcePage.school_id == school_id,
                SourcePage.scrape_type == ScrapeType.WEBSITE,
            )
        )
    ).scalars()
    for page in pages:
        if site_id(page.source_url) == site and page.is_valid is not False:
            page.is_valid = False
            count += 1
    return count


async def _site_derived_locations(db, school_id: int, *, preview: bool) -> list[dict[str, Any]]:
    """The validator's strong-path location clearing: addresses/pins copied from the site.

    An address an official register point confirmed is kept; no other registry
    address is stored, so a site-copied address fails closed.
    """
    from app.scrapers.url_validator import clear_website_derived_locations

    return await clear_website_derived_locations(
        db, school_id, keep_officially_confirmed_addresses=True, preview=preview
    )


async def apply_decision(db, school, decision: Decision, *, country_code: str = "bg") -> dict[str, Any]:
    if decision.action == KEEP:
        return {"pages_invalidated": 0, "locations_cleared": []}
    # The old site is no longer trusted for either outcome, so neither is what it put
    # on the map.
    locations_cleared = await _site_derived_locations(db, school.id, preview=False)
    pages_invalidated = await _invalidate_host_pages(db, school.id, decision.current_url)
    attrs = dict(school.attributes or {})
    attrs["website_candidate_method"] = "shared_site_check"
    attrs["website_candidate_reason"] = f"Shared-site check: {decision.reason}"
    attrs[SHARED_SITE_CHECK_KEY] = {
        "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "action": decision.action,
        "reason": decision.reason,
        "previous_url": decision.current_url,
        "new_url": decision.new_url,
    }
    if decision.action == WITHHOLD:
        attrs["website_candidate_url"] = decision.current_url
        school.attributes = attrs
        _withhold(school)
    elif decision.action == REPLACE:
        from app.scrapers.website_discovery import WebsiteDiscoverer

        attrs["website_candidate_url"] = decision.new_url
        # The stored extraction describes the previous site; keep it withheld until
        # the new site is validated and extracted.
        attrs[WEBSITE_DATA_WITHHELD_KEY] = True
        attrs.pop("validated_website_url", None)
        school.attributes = attrs
        school.website_url = decision.new_url
        school.scrape_status = "pending"
        await WebsiteDiscoverer(country_code=country_code)._upsert_discovery_source_page(
            db, school.id, decision.new_url, "shared_site_check"
        )
    db.add(school)
    return {"pages_invalidated": pages_invalidated, "locations_cleared": locations_cleared}


async def run_shared_site_check(
    db,
    *,
    country: str = "bg",
    city: Optional[str] = None,
    school_ids: Optional[Sequence[int]] = None,
    dry_run: bool = True,
    fetcher: Optional[Fetcher] = None,
) -> dict[str, Any]:
    """Check every shared-domain group (optionally only those touching ``school_ids``).

    Groups are formed across the whole country: a brand's campuses in other cities
    share its domain too. ``city`` only narrows which groups are reported/acted on
    (a group is in scope when any member is in the city).
    """
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from app.models import School

    schools = (
        await db.execute(
            select(School)
            .options(selectinload(School.locations))
            .where(School.country_code == country, School.website_url.isnot(None))
            .order_by(School.id)
        )
    ).scalars().all()
    by_id = {int(s.id): s for s in schools}
    groups = shared_groups(_member_from_school(s) for s in schools)
    wanted = set(school_ids) if school_ids is not None else None
    in_scope = {
        key: members
        for key, members in groups.items()
        if (wanted is None or any(m.school_id in wanted for m in members))
        and (city is None or any(by_id[m.school_id].city == city for m in members))
    }

    stored = await _stored_texts_by_host(db, [m.school_id for ms in in_scope.values() for m in ms])
    if dry_run:
        # Nothing to write: release the read transaction before minutes of fetching.
        await db.rollback()
    reader = SiteReader(fetcher or http_fetch, stored)

    report_groups = []
    counts = {KEEP: 0, REPLACE: 0, WITHHOLD: 0}
    for key in sorted(in_scope):
        members = in_scope[key]
        domain = registrable_domain(members[0].website_url) or key
        # Platform-hosted sites have no brand hub to follow.
        hub_domain = domain if key == domain else ""
        rows = []
        for member in members:
            decision = await decide_member(member, domain=hub_domain, reader=reader, country_code=country)
            counts[decision.action] += 1
            if dry_run:
                locations = []
                if decision.action != KEEP:
                    locations = await _site_derived_locations(db, member.school_id, preview=True)
                    # Read-only: end the short read transaction before the next fetch.
                    await db.rollback()
                applied = {"would_clear_locations": locations}
            else:
                applied = await apply_decision(db, by_id[member.school_id], decision, country_code=country)
            rows.append(
                {
                    "school_id": member.school_id,
                    "name": member.name,
                    "school_type": member.school_type,
                    "education_level": member.education_level,
                    "city": member.city,
                    "registry_addresses": member.registry_addresses,
                    "website_derived_addresses": member.website_derived_addresses,
                    "current_url": decision.current_url,
                    "action": decision.action,
                    "new_url": decision.new_url,
                    "reason": decision.reason,
                    "evidence": decision.evidence,
                    "applied": applied,
                }
            )
        report_groups.append({"group": key, "domain": domain, "members": rows})

    if not dry_run:
        await db.commit()

    return {
        "dry_run": dry_run,
        "country": country,
        "city": city,
        "groups": report_groups,
        "counts": {
            "groups": len(report_groups),
            "institutions": sum(len(g["members"]) for g in report_groups),
            "kept": counts[KEEP],
            "replaced": counts[REPLACE],
            "withheld": counts[WITHHOLD],
        },
    }

