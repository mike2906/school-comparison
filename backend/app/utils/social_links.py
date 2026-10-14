"""A school's Facebook and Instagram pages: reading them from links, and which may be shown.

The social-links stage stores ``attributes["social_links"]`` from the links on the school's
own website. Only the school's own page or profile counts: share buttons, groups, events,
posts and plugin widgets are not a page (a Page Plugin widget names its page, so that page
is taken). The API shows a stored link only through ``shown_social_links``.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any, Optional
from urllib.parse import parse_qs, unquote, urlparse

from app.utils.school_facts import SITE_ACCEPTED_STATUSES, _site
from app.utils.website_data import website_data_withheld_reasons

SCHEMA_VERSION = 1
ATTRIBUTE_KEY = "social_links"
NETWORKS = ("facebook", "instagram")

_FACEBOOK_HOSTS = {"facebook.com", "m.facebook.com", "mbasic.facebook.com", "web.facebook.com", "fb.com"}
_INSTAGRAM_HOSTS = {"instagram.com", "m.instagram.com", "instagr.am"}
# First path parts that are not a page: sharing, groups (parents' groups stay out), single
# posts, Facebook's own pages.
_FACEBOOK_NOT_A_PAGE = {
    "about",
    "ads",
    "business",
    "dialog",
    "events",
    "gaming",
    "groups",
    "hashtag",
    "help",
    "home.php",
    "l.php",
    "legal",
    "login",
    "login.php",
    "marketplace",
    "media",
    "notes",
    "permalink.php",
    "pg",
    "photo",
    "photo.php",
    "photos",
    "plugins",
    "policies",
    "privacy",
    "reel",
    "reels",
    "search",
    "settings",
    "share",
    "share.php",
    "sharer",
    "sharer.php",
    "story.php",
    "tr",
    "video.php",
    "watch",
}
_INSTAGRAM_NOT_A_PROFILE = {
    "about",
    "accounts",
    "developer",
    "direct",
    "explore",
    "legal",
    "p",
    "reel",
    "reels",
    "share",
    "stories",
    "tv",
    "web",
}


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def _plain(part: str) -> bool:
    return bool(part) and all(c.isalnum() or c in "._-" for c in part)


def _facebook_page(url: str) -> Optional[str]:
    parsed = urlparse(url)
    parts = [unquote(p) for p in parsed.path.split("/") if p]
    if not parts:
        return None
    first = parts[0].lower()
    if first == "plugins":  # the Page Plugin widget: ?href= names the page it shows
        href = (parse_qs(parsed.query).get("href") or [""])[0]
        return _facebook_page(href) if _host(href) in _FACEBOOK_HOSTS else None
    if first == "pg" and len(parts) >= 2:  # the old page route: /pg/<page>/about
        return _facebook_page(f"https://www.facebook.com/{parsed.path.split('/')[2]}")
    if first == "profile.php":
        page_id = (parse_qs(parsed.query).get("id") or [""])[0]
        return f"https://www.facebook.com/profile.php?id={page_id}" if page_id.isdigit() else None
    if first in ("pages", "people") and len(parts) >= 3 and parts[2].isdigit():
        return f"https://www.facebook.com/{first}/{parsed.path.split('/')[2]}/{parts[2]}"
    if first in _FACEBOOK_NOT_A_PAGE or first in ("pages", "people") or not _plain(first):
        return None
    return f"https://www.facebook.com/{first}"  # page names are not case-sensitive


def _instagram_profile(url: str) -> Optional[str]:
    parts = [p for p in urlparse(url).path.split("/") if p]
    if not parts:
        return None
    name = parts[0].lower()
    if name in _INSTAGRAM_NOT_A_PROFILE or len(name) > 30 or not all(c.isalnum() or c in "._" for c in name):
        return None
    return f"https://www.instagram.com/{name}/"


def profile_url(href: Any) -> Optional[tuple[str, str]]:
    """``(network, canonical URL)`` when ``href`` points to a Facebook page or an Instagram
    profile, else None."""
    if not isinstance(href, str) or not href.strip():
        return None
    url = href.strip()
    if url.startswith("//"):
        url = f"https:{url}"
    if not url.lower().startswith(("http://", "https://")):
        return None
    host = _host(url)
    if host in _FACEBOOK_HOSTS:
        page = _facebook_page(url)
        return ("facebook", page) if page else None
    if host in _INSTAGRAM_HOSTS:
        profile = _instagram_profile(url)
        return ("instagram", profile) if profile else None
    return None


def pick_links(hrefs: Iterable[str]) -> tuple[dict[str, str], list[str]]:
    """The school's page on each network, and the networks left out as ambiguous.

    A page linked more often than any other wins (a footer icon repeats on every page);
    a tie between two pages names neither, since a wrong link is worse than none."""
    counts: dict[str, Counter] = {network: Counter() for network in NETWORKS}
    for href in hrefs:
        found = profile_url(href)
        if found:
            counts[found[0]][found[1].lower()] += 1
    links: dict[str, str] = {}
    ambiguous: list[str] = []
    for network, counter in counts.items():
        top = counter.most_common(2)
        if not top:
            continue
        if len(top) == 2 and top[0][1] == top[1][1]:
            ambiguous.append(network)
            continue
        links[network] = top[0][0]
    return links, ambiguous


def shown_social_links(attributes: Any, scrape_status: Any, website_url: Optional[str]) -> Optional[dict[str, str]]:
    """``{"facebook": url, "instagram": url}`` (either may be missing), or None.

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
    links = {}
    for network in NETWORKS:
        found = profile_url(block.get(network))
        if found and found[0] == network:
            links[network] = found[1]
    return links or None
