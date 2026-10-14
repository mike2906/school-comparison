"""Fee tables the fees agent finds only as pictures.

594 publishes its school fees as two pictures on a page whose own text names only a
deposit, so the reader never saw them. A page's pictures named for fees (file name or
alt text) are read with ``fee_image_reader``, which reads each one twice and keeps the
transcription only when both readings agree.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from urllib.parse import unquote, urlparse

from app.scrapers.fee_image_reader import read_fee_image
from app.scrapers.fee_pages import _FEE_WORD_RE, _IMAGE_TYPES, _words, content_images, scaled_copies

logger = logging.getLogger(__name__)

MAX_IMAGES = 4  # per school: 594 shows two school and two preschool tables
IMAGE_COST_ESTIMATE_USD = 0.06  # two vision reads of one picture


def fee_images(html: str, base_url: str) -> list[tuple[str, str]]:
    """(picture URL, fallback URL) of the page's pictures named for fees.

    The URL is the unscaled original; the fallback is the scaled copy the page shows."""
    scaled = scaled_copies(html, base_url)
    return [
        (url, scaled.get(url, url))
        for url, alt in content_images(html, base_url)
        if _FEE_WORD_RE.search(_words(f"{unquote(urlparse(url).path)} {alt}"))
    ]


async def read_image(
    client,
    urls: tuple[str, str],
    *,
    school_id: int,
    stage: str,
    allowed: Callable[[str], Awaitable[bool]],
    on_cost: Callable[[float], None],
) -> tuple[str, str] | None:
    """(URL read, transcription) of one fee picture, or None when it can't be read or the readings disagree."""
    for url in dict.fromkeys(urls):
        if not await allowed(url):
            continue
        try:
            response = await client.get(url, follow_redirects=True, timeout=30)
        except Exception as exc:  # a picture that can't be fetched is skipped
            logger.info("fee picture %s failed: %s", url, exc)
            continue
        media_type = (response.headers.get("content-type") or "").split(";")[0].strip()
        if response.status_code != 200 or media_type not in _IMAGE_TYPES:
            continue
        text = await read_fee_image(response.content, media_type, school_id=school_id, stage=stage, on_cost=on_cost)
        return (str(response.url), text) if text else None
    return None
