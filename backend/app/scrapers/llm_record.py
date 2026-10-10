"""Record extraction LLM answers to disk, so a later run can be compared without paying again.

Off unless ``EXTRACTION_LLM_RECORD_DIR`` is set. Each successful typed answer is written to
``<dir>/<key>.json``, where the key hashes the system prompt, the user prompt (which holds
the page text), the output type and the model tier. A refactor that leaves prompts unchanged
produces the same keys, so a replay of the recorded answers isolates our own code. Only the
key and the parsed answer are stored, never the page text.

Recording must never break extraction: any write error is logged and ignored.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel

logger = logging.getLogger(__name__)

RECORD_FORMAT_VERSION = 1


def record_key(*, system_prompt: str, user_prompt: str, result_type: type, tier: str) -> str:
    digest = hashlib.sha256()
    for part in (system_prompt, user_prompt, result_type.__name__, tier):
        digest.update(part.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def record_answer(
    record_dir: str,
    *,
    system_prompt: str,
    user_prompt: str,
    result_type: type,
    tier: str,
    parsed: Any,
    school_id: int | None,
) -> None:
    if not record_dir or not isinstance(parsed, BaseModel):
        return
    key = record_key(system_prompt=system_prompt, user_prompt=user_prompt, result_type=result_type, tier=tier)
    payload = {
        "format": RECORD_FORMAT_VERSION,
        "key": key,
        "result_type": result_type.__name__,
        "tier": tier,
        "school_id": school_id,
        "output": parsed.model_dump(mode="json"),
    }
    try:
        directory = Path(record_dir)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{key}.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001 - recording is best-effort
        logger.warning("Could not record extraction answer %s: %s", key, exc)
