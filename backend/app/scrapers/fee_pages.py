"""Follow fee links the site crawl does not reach.

The site crawl goes one click deep from the home page and never fetches PDFs, so a fee
page linked only from the admissions page, or a fee list published as a PDF, is never
stored and the school ends up with no prices. This step takes the links of the pages
the crawl did reach, keeps the same-site ones whose URL or link text names fees, and
fetches a few of them (and the fee links those pages carry in turn) as plain HTTP.

No model is involved. The stored text goes through the same price extraction and
evidence checks as any crawled page.
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
from dataclasses import dataclass
from typing import Awaitable, Callable, Iterable
from urllib.parse import unquote, urldefrag, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from app.config import CRAWLER_USER_AGENT
from app.scrapers.price_evidence import _PRICE_RE
from app.scrapers.shared_site_check import describes_level

logger = logging.getLogger(__name__)

MAX_FEE_FETCHES = 6
MAX_LINK_HOPS = 2  # home page -> admissions page (crawled) -> fee page -> fee PDF
FETCH_TIMEOUT_SECONDS = 20.0
MAX_BODY_BYTES = 15 * 1024 * 1024
MAX_PDF_PAGES = 8
MAX_TEXT_CHARS = 15000
MAX_FEE_IMAGES = 3  # fee tables published as pictures, read per school
PDF_RENDER_PAGES = 2
PDF_RENDER_DPI = 150

# Words that name a fee page, matched in the decoded URL and in the link text. Whole
# words where a prefix would also hit ordinary words ("fee" in "feedback", "цен" in
# "център").
_FEE_WORD_RE = re.compile(
    r"такс|ценоразпис|\bцен[аи]\b|ценова|финансови\s+условия"
    r"|\btaks[ai]\b|\bt?seni\b|\bceni\b|cenorazpis"
    r"|finansovi\s+usloviya|\bfees?\b|tuition|\bpric(?:e|es|ing)\b|financial\s+conditions"
    r"|\btarifs?\b|frais|schulgeld|geb(?:ü|ue)hr|\bkosten\b",
    re.IGNORECASE,
)
_SKIPPED_SUFFIXES = (
    ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".css", ".js", ".zip", ".mp4", ".mp3",
    ".doc", ".docx", ".xls", ".xlsx",
)  # fmt: skip

Link = tuple[str, str]  # (href, link text)
# Reads the text of an image (bytes, media type); None when it shows no fees.
ImageReader = Callable[[bytes, str], Awaitable[str | None]]
_IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp"}
# Site furniture, not content.
_DECORATION_RE = re.compile(r"logo|icon|avatar|loader|bubble|sprite|banner|flag|\.gif(?:$|\?)|\.svg(?:$|\?)", re.IGNORECASE)
# WordPress serves a scaled copy as name-700x453.png; the original reads better.
_SCALED_COPY_RE = re.compile(r"-\d{2,4}x\d{2,4}(?=\.(?:png|jpe?g|webp)$)", re.IGNORECASE)


@dataclass(frozen=True)
class FeeDocument:
    url: str
    text: str


def _host(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _same_site(url: str, site_url: str) -> bool:
    host, site = _host(url), _host(site_url)
    return bool(host) and bool(site) and (host == site or host.endswith(f".{site}"))


def page_key(url: str) -> tuple[str, str, str]:
    parsed = urlparse(url)
    return (_host(url), unquote(parsed.path).rstrip("/").lower(), parsed.query)


def _words(value: str) -> str:
    """URL or link text with separators turned into spaces, so word boundaries work."""
    return re.sub(r"[/_\-.+%=&?]+", " ", unquote(value or ""))


def _names_only_other_level(words: str, school_family: str) -> bool:
    other = "school" if school_family == "kindergarten" else "kindergarten"
    return describes_level(words, other) and not describes_level(words, school_family)


def fee_link_candidates(
    links: Iterable[Link], *, base_url: str, site_url: str, school_family: str | None = None
) -> list[Link]:
    """Same-site links whose URL or text names fees, best first, one per page.

    Returns (url, link text). With ``school_family`` ("kindergarten" or "school"), a link
    that names only the other level is dropped: a shared site's "fees - kindergarten"
    PDF is not the school's.
    """
    scored: dict[tuple[str, str, str], tuple[int, str, str]] = {}
    for href, text in links:
        url = urldefrag(urljoin(base_url, (href or "").strip())).url
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not _same_site(url, site_url):
            continue
        path = unquote(parsed.path).lower()
        if path.endswith(_SKIPPED_SUFFIXES):
            continue
        in_url = bool(_FEE_WORD_RE.search(_words(f"{parsed.path} {parsed.query}")))
        in_text = bool(_FEE_WORD_RE.search(_words(text)))
        if not (in_url or in_text):
            continue
        link_words = _words(f"{parsed.path} {text}")
        if school_family and _names_only_other_level(link_words, school_family):
            continue
        score = 2 * in_url + 2 * in_text + path.endswith(".pdf")
        key = page_key(url)
        if key not in scored or score > scored[key][0]:
            scored[key] = (score, url, " ".join((text or "").split()))
    ranked = sorted(scored.values(), key=lambda item: (-item[0], item[1]))
    return [(url, text) for _, url, text in ranked]


def html_links(html: str) -> list[Link]:
    soup = BeautifulSoup(html, "html.parser")
    return [(a["href"], a.get_text(" ", strip=True)) for a in soup.find_all("a", href=True)]


def content_images(html: str, base_url: str) -> list[Link]:
    """(image URL, alt text) of the page's content pictures, fee-named ones first.

    Used for a fee page that states no price in its text: the fee table may be one of
    its pictures. Logos, icons and small images are left out.
    """
    soup = BeautifulSoup(html, "html.parser")
    found: dict[str, tuple[bool, str]] = {}
    for image in (soup.find("main") or soup.find("article") or soup).find_all("img"):
        source = (image.get("src") or image.get("data-src") or "").strip()
        if not source or source.startswith("data:") or _DECORATION_RE.search(source):
            continue
        width = str(image.get("width") or "")
        if width.isdigit() and int(width) < 300:
            continue
        url = _SCALED_COPY_RE.sub("", urljoin(base_url, source))
        alt = " ".join(str(image.get("alt") or "").split())
        named = bool(_FEE_WORD_RE.search(_words(f"{urlparse(url).path} {alt}")))
        found.setdefault(url, (named, alt))
    ranked = sorted(found.items(), key=lambda item: not item[1][0])
    return [(url, alt) for url, (_, alt) in ranked]


def pdf_page_images(data: bytes) -> list[bytes]:
    """PNG renderings of a PDF's first pages, for a scanned file with no text layer."""
    import pdfplumber

    images: list[bytes] = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages[:PDF_RENDER_PAGES]:
            buffer = io.BytesIO()
            page.to_image(resolution=PDF_RENDER_DPI).original.save(buffer, format="PNG")
            images.append(buffer.getvalue())
    return images


def pdf_text(data: bytes) -> str:
    """Text layer of a PDF's first pages; empty for a scanned (image-only) file."""
    import pdfplumber

    parts: list[str] = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages[:MAX_PDF_PAGES]:
            parts.append(page.extract_text() or "")
    return "\n".join(part.strip() for part in parts if part.strip())[:MAX_TEXT_CHARS]


async def fetch_fee_documents(
    links: Iterable[Link],
    *,
    site_url: str,
    known_urls: Iterable[str],
    html_to_text: Callable[[str], str | None],
    client: httpx.AsyncClient,
    school_family: str | None = None,
    disallowed: Callable[[str], Awaitable[bool]] | None = None,
    read_image: ImageReader | None = None,
) -> list[FeeDocument]:
    """Fetch the fee pages and PDFs behind ``links`` that the crawl has not stored.

    ``links`` are (absolute URL, link text) pairs. A fetched HTML page's own fee links
    are followed too, up to :data:`MAX_LINK_HOPS` hops and :data:`MAX_FEE_FETCHES`
    requests in all. A failed or empty fetch is skipped. A PDF's text starts with its
    link text, which is often the only place that says whose fees and which year.

    With ``read_image``, a fee page that states no price has its content pictures read,
    and so has a scanned PDF: up to :data:`MAX_FEE_IMAGES` pictures per call. Each
    picture is stored under its own URL, so the source link shows what was read.
    """

    def candidates(page_links: Iterable[Link], base_url: str) -> list[Link]:
        return fee_link_candidates(
            page_links, base_url=base_url, site_url=site_url, school_family=school_family
        )

    seen = {page_key(url) for url in known_urls}
    queue = [(url, label, 1) for url, label in candidates(links, site_url)]
    documents: list[FeeDocument] = []
    fetches = 0
    images_read = 0

    async def read(data: bytes, media_type: str, heading: str) -> str | None:
        nonlocal images_read
        if read_image is None or images_read >= MAX_FEE_IMAGES:
            return None
        images_read += 1
        text = await read_image(data, media_type)
        return f"{heading}\n{text}"[:MAX_TEXT_CHARS] if text else None
    while queue and fetches < MAX_FEE_FETCHES:
        url, label, hop = queue.pop(0)
        if page_key(url) in seen:
            continue
        seen.add(page_key(url))
        if disallowed is not None and await disallowed(url):
            continue
        fetches += 1
        try:
            fetched = await _get(client, url)
        except httpx.HTTPError as exc:
            logger.info("fee link %s failed: %s", url, exc)
            continue
        if fetched is None:
            continue
        final_url, content_type, body = fetched
        if not _same_site(final_url, site_url):
            continue
        if "pdf" in content_type or body[:5] == b"%PDF-":
            try:
                text = await asyncio.to_thread(pdf_text, body)
                if text.strip() and label:
                    text = f"{label}\n{text}"[:MAX_TEXT_CHARS]
                elif not text.strip() and read_image is not None:
                    pages = await asyncio.to_thread(pdf_page_images, body)
                    read_pages = [await read(page, "image/png", label) for page in pages]
                    text = "\n".join(page for page in read_pages if page)
            except Exception as exc:  # pdfplumber raises many types on a damaged file
                logger.info("fee PDF %s unreadable: %s", url, exc)
                continue
        elif "html" in content_type:
            html = body.decode(_charset(content_type), errors="replace")
            text = (html_to_text(html) or "")[:MAX_TEXT_CHARS]
            if hop < MAX_LINK_HOPS:
                nested = candidates(html_links(html), final_url)
                queue.extend((nested_url, nested_label, hop + 1) for nested_url, nested_label in nested)
            if read_image is not None and not _PRICE_RE.search(text):
                for image_url, alt in content_images(html, final_url):
                    if images_read >= MAX_FEE_IMAGES:
                        break
                    if not _same_site(image_url, site_url) or page_key(image_url) in seen:
                        continue
                    seen.add(page_key(image_url))
                    try:
                        image = await _get(client, image_url)
                    except httpx.HTTPError:
                        continue
                    if image is None or image[1].split(";")[0].strip() not in _IMAGE_TYPES:
                        continue
                    read_text = await read(image[2], image[1].split(";")[0].strip(), alt or label)
                    if read_text:
                        documents.append(FeeDocument(url=image[0], text=read_text))
        else:
            continue
        if text.strip():
            seen.add(page_key(final_url))
            documents.append(FeeDocument(url=final_url, text=text))
    return documents


async def _get(client: httpx.AsyncClient, url: str) -> tuple[str, str, bytes] | None:
    """(final URL, content type, body) of a 200 response no larger than the size cap."""
    async with client.stream(
        "GET", url, follow_redirects=True, timeout=FETCH_TIMEOUT_SECONDS
    ) as response:
        if response.status_code != 200:
            return None
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body.extend(chunk)
            if len(body) > MAX_BODY_BYTES:
                return None
        content_type = response.headers.get("content-type", "").lower()
        return str(response.url), content_type, bytes(body)


def _charset(content_type: str) -> str:
    match = re.search(r"charset=([\w-]+)", content_type)
    name = match.group(1) if match else "utf-8"
    try:
        "".encode(name)
    except LookupError:
        return "utf-8"
    return name


def fee_http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(headers={"User-Agent": CRAWLER_USER_AGENT})
