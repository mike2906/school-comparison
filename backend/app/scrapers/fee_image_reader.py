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

    The currencies named anywhere, and each table row from the first one that holds a
    price: its first cell with the numbers of the row. The same digits under another row's label or another
    currency are a different fee. Titles and header rows are left out: how a reading
    wraps them varies and says nothing about the fees.
    """
    lowered = (text or "").casefold()
    currencies = frozenset(code for code, pattern in _CURRENCIES if re.search(pattern, lowered))
    rows = []
    in_table = False
    for line in lowered.splitlines():
        numbers = tuple(numbers_in(line))
        if "|" not in line or not numbers:
            continue
        # Header rows come first and hold only small numbers ("1 installment"); from the
        # first row with a price on, every row counts, also one with a small fee.
        in_table = in_table or any(len(number) >= 3 for number in numbers)
        if in_table:
            label = "".join(re.findall(r"[^\W_]+", line.split("|", 1)[0]))
            rows.append((label, numbers))
    return currencies, sorted(rows)


async def read_fee_image(data: bytes, media_type: str, *, school_id: int | None = None) -> str | None:
    """The image's text, or None when it shows no fees or two readings disagree."""
    readings: list[str] = []
    for _ in range(2):
        agent = create_agent(tier="vision", system_prompt=_PROMPT, result_type=str)
        try:
            result = await execute_billable_request(
                lambda agent=agent: agent.run([BinaryContent(data=data, media_type=media_type)]),
                model=get_model("vision"),
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
    # A table is compared row by row; text with no table, by its numbers.
    first, second = reading_signature(readings[0]), reading_signature(readings[1])
    if first != second or (not first[1] and numbers_in(readings[0]) != numbers_in(readings[1])):
        logger.warning("Fee image readings disagree for school %s; not stored", school_id)
        return None
    return readings[0]
