"""Read a fee table that a school publishes as an image.

Some schools put their fee list on the page as a picture, so the page text carries no
price at all. A vision model transcribes the picture; nothing is interpreted here, the
transcription is stored as the image's page text and goes through the same price
extraction and evidence checks as any page.

A misread digit would become a wrong published price, and the evidence check cannot
catch it (it compares rows with this same transcription). So the picture is read twice
and the transcription is used only when both readings name the same currencies and give
the same numbers under the same row labels.
"""

from __future__ import annotations

import logging
import re

from pydantic_ai import BinaryContent

from app.ai.client import create_agent, get_model
from app.services.provider_costs import execute_billable_request

logger = logging.getLogger(__name__)

READ_TIMEOUT_SECONDS = 60.0
NOTHING = "NONE"
_PROMPT = (
    "Transcribe every piece of text in this image exactly as written, in its own language. "
    "Write a table row by row, one row per line, with ' | ' between cells, and repeat the "
    "header row first. Keep every number, currency sign and date exactly as shown. Do not "
    "translate, summarise, explain or add anything. If the image shows no fees or prices, "
    f"answer with the single word {NOTHING}."
)


def numbers_in(text: str) -> list[str]:
    """The numbers of a transcription, digits only, in order ("7 880 EUR" -> "7880")."""
    return [re.sub(r"\D", "", match) for match in re.findall(r"\d[\d  .,]*\d|\d", text or "")]


_CURRENCIES = (("EUR", r"€|\beur|евро"), ("BGN", r"лв|\bbgn|лева"), ("USD", r"\$|\busd"), ("GBP", r"£|\bgbp"))


def reading_signature(text: str) -> tuple[frozenset[str], list[tuple[str, tuple[str, ...]]]]:
    """What two readings of one picture must agree on.

    The currencies named anywhere, and for each line that holds numbers its label (the
    words before the first number) with those numbers: the same digits under another
    row's label or another currency are a different fee.
    """
    lowered = (text or "").casefold()
    currencies = frozenset(code for code, pattern in _CURRENCIES if re.search(pattern, lowered))
    rows = []
    for line in lowered.splitlines():
        numbers = tuple(numbers_in(line))
        if numbers:
            label = re.split(r"\d", line, maxsplit=1)[0]
            rows.append(("".join(re.findall(r"[^\W\d_]+", label)), numbers))
    return currencies, rows


async def read_fee_image(data: bytes, media_type: str, *, school_id: int | None = None) -> str | None:
    """The image's text, or None when it shows no fees or two readings disagree."""
    readings: list[str] = []
    for _ in range(2):
        agent = create_agent(tier="pricing", system_prompt=_PROMPT, result_type=str)
        try:
            result = await execute_billable_request(
                lambda agent=agent: agent.run([BinaryContent(data=data, media_type=media_type)]),
                model=get_model("pricing"),
                school_id=school_id,
                stage="navigate",
                timeout_seconds=READ_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            logger.warning("Fee image read failed for school %s: %s", school_id, exc)
            return None
        text = str(getattr(result, "output", None) or getattr(result, "data", "") or "").strip()
        if not text or text.upper() == NOTHING:
            return None
        readings.append(text)
    if reading_signature(readings[0]) != reading_signature(readings[1]):
        logger.warning("Fee image readings disagree for school %s; not stored", school_id)
        return None
    return readings[0]
