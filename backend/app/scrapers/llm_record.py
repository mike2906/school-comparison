"""Record extraction LLM answers to disk, so a later run can be compared without paying again.

Off unless ``EXTRACTION_LLM_RECORD_DIR`` is set. Each successful typed answer is written to
``<dir>/<key>.json``, where the key hashes the system prompt, the user prompt (which holds
the page text), the output type, the model tier and the model id. An existing file is never
overwritten, so use a fresh absolute directory per recorded run. A refactor that leaves prompts unchanged
produces the same keys, so a replay of the recorded answers isolates our own code. Only the
key and the parsed answer are stored, never the page text.

Recording must never break extraction: any write error is logged and ignored.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel

logger = logging.getLogger(__name__)

RECORD_FORMAT_VERSION = 1


def record_key(*, system_prompt: str, user_prompt: str, result_type: type, tier: str, model: str = "") -> str:
    digest = hashlib.sha256()
    for part in (system_prompt, user_prompt, result_type.__name__, tier, model):
        digest.update(part.encode("utf-8", errors="surrogatepass"))
        digest.update(b"\0")
    return digest.hexdigest()


def record_answer(
    record_dir: str,
    *,
    system_prompt: str,
    user_prompt: str,
    result_type: type,
    tier: str,
    model: str = "",
    temperature: float | None = None,
    parsed: Any,
    school_id: int | None,
) -> None:
    if not record_dir or not isinstance(parsed, BaseModel):
        return
    try:
        key = record_key(
            system_prompt=system_prompt, user_prompt=user_prompt, result_type=result_type, tier=tier, model=model
        )
        path = Path(record_dir) / f"{key}.json"
        if path.exists():
            # Never overwrite: a later run with recording still on must not replace the baseline.
            return
        payload = {
            "format": RECORD_FORMAT_VERSION,
            "key": key,
            "result_type": result_type.__name__,
            "tier": tier,
            "model": model,
            "temperature": temperature,
            "school_id": school_id,
            "output": parsed.model_dump(mode="json"),
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except Exception as exc:  # noqa: BLE001 - recording is best-effort
        logger.warning("Could not record extraction answer: %s", exc)
