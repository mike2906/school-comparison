"""Fee tables the fees agent finds only as pictures.

594 publishes its school fees as two pictures on a page whose own text names only a
deposit, so the reader never saw them. A page's pictures named for fees (file name or
alt text) on the school's own site are read with ``fee_image_reader``, which reads each
one twice and keeps the transcription only when both readings agree.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from urllib.parse import unquote, urlparse

from app.config import get_settings
from app.scrapers.fee_image_reader import read_fee_image
from app.scrapers.fee_pages import _FEE_WORD_RE, _IMAGE_TYPES, _same_site, _words, content_images, scaled_copies
from app.services.provider_costs import ProviderCostError, ProviderResponseUnattributed

logger = logging.getLogger(__name__)

MAX_IMAGES = 4  # per school: 594 shows two school and two preschool tables
IMAGE_COST_ESTIMATE_USD = 0.03  # two vision reads: at most $0.018 in the 2026-10-14 Sofia run (19 pictures)


def fee_images(html: str, base_url: str) -> list[tuple[str, str, str]]:
    """(picture URL, fallback URL, alt text) of the page's pictures named for fees.

    The URL is the unscaled original; the fallback is the scaled copy the page shows."""
    scaled = scaled_copies(html, base_url)
    return [
        (url, scaled.get(url, url), alt)
        for url, alt in content_images(html, base_url)
        if _FEE_WORD_RE.search(_words(f"{unquote(urlparse(url).path)} {alt}"))
    ]


def on_site(picture: tuple[str, str, str], site_url: str) -> bool:
    """The picture, or its scaled copy, is on the school's own site."""
    return any(_same_site(url, site_url) for url in picture[:2])


async def read_image(
    client,
    picture: tuple[str, str, str],
    *,
    page_url: str,
    site_url: str,
    school_id: int,
    stage: str,
    allowed: Callable[[str], Awaitable[bool]],
    on_cost: Callable[[float], None],
) -> tuple[str, str] | None:
    """(URL read, text) of one fee picture on the school's own site, or None.

    The text starts with the page the picture is on and its alt text, which often say
    the level (594's preschool tables sit on /preschool-fees-2026-2027/)."""
    for url in dict.fromkeys(picture[:2]):
        if not _same_site(url, site_url) or not await allowed(url):
            continue
        try:
            response = await client.get(url, follow_redirects=True, timeout=30)
        except Exception as exc:  # a picture that can't be fetched is skipped
            logger.info("fee picture %s failed: %s", url, exc)
            continue
        media_type = (response.headers.get("content-type") or "").split(";")[0].strip()
        if response.status_code != 200 or media_type not in _IMAGE_TYPES or not _same_site(str(response.url), site_url):
            continue
        try:
            text = await read_fee_image(
                response.content,
                media_type,
                school_id=school_id,
                stage=stage,
                on_cost=on_cost,
                reraise=(ProviderCostError,),
            )
        except ProviderResponseUnattributed:  # counted at its assumed maximum, as the ledger counts it
            on_cost(float(get_settings().provider_request_reserve_usd))
            return None
        if not text:
            return None
        heading = " ".join(part for part in (f"Fee picture on {page_url}", picture[2]) if part)
        return str(response.url), f"{heading}\n{text}"
    return None
