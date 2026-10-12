"""Recording extraction LLM answers for later offline comparison."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.config import get_settings
from app.schemas.extraction import PageFees
from app.scrapers import extractor
from app.scrapers.llm_record import record_answer, record_key


def _answer() -> PageFees:
    return PageFees(lines=[])


def test_record_key_changes_with_every_part():
    base = dict(system_prompt="s", user_prompt="u", result_type=PageFees, tier="cheap")
    key = record_key(**base)
    assert key == record_key(**base)
    for change in (
        {"system_prompt": "s2"},
        {"user_prompt": "u2"},
        {"result_type": dict},
        {"tier": "capable"},
        {"model": "other/model"},
    ):
        assert record_key(**{**base, **change}) != key
    # Parts are delimited, so moving text between prompts changes the key.
    assert record_key(**{**base, "system_prompt": "su", "user_prompt": ""}) != key


def test_record_answer_writes_the_parsed_answer_without_the_prompts(tmp_path):
    record_answer(
        str(tmp_path),
        system_prompt="secret system",
        user_prompt="page text",
        result_type=PageFees,
        tier="cheap",
        parsed=_answer(),
        school_id=7,
    )

    [path] = list(tmp_path.iterdir())
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert path.stem == payload["key"]
    assert payload["result_type"] == "PageFees"
    assert payload["school_id"] == 7
    assert PageFees.model_validate(payload["output"]) == _answer()
    assert "page text" not in path.read_text(encoding="utf-8")


def test_record_answer_is_off_without_a_dir_and_skips_failed_parses(tmp_path):
    record_answer("", system_prompt="s", user_prompt="u", result_type=PageFees,
                  tier="cheap", parsed=_answer(), school_id=None)
    record_answer(str(tmp_path), system_prompt="s", user_prompt="u", result_type=PageFees,
                  tier="cheap", parsed=None, school_id=None)
    assert list(tmp_path.iterdir()) == []


def test_record_answer_never_raises(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("not a directory")
    record_answer(str(blocker / "sub"), system_prompt="s", user_prompt="u",
                  result_type=PageFees, tier="cheap", parsed=_answer(), school_id=None)


def test_record_answer_never_raises_on_unencodable_prompts_or_dump_errors(tmp_path):
    record_answer(str(tmp_path), system_prompt="s", user_prompt="lone surrogate \ud800",
                  result_type=PageFees, tier="cheap", parsed=_answer(), school_id=None)

    class Broken(PageFees):
        def model_dump(self, **_kwargs):
            raise ValueError("cannot dump")

    record_answer(str(tmp_path / "broken"), system_prompt="s", user_prompt="u",
                  result_type=PageFees, tier="cheap", parsed=Broken(lines=[]), school_id=None)
    assert not (tmp_path / "broken").exists()


def test_record_answer_keeps_the_first_recording(tmp_path):
    common = dict(system_prompt="s", user_prompt="u", result_type=PageFees, tier="cheap")
    record_answer(str(tmp_path), **common, parsed=_answer(), school_id=1)
    record_answer(str(tmp_path), **common, parsed=_answer(), school_id=2)

    [path] = list(tmp_path.iterdir())
    assert json.loads(path.read_text(encoding="utf-8"))["school_id"] == 1


@pytest.mark.asyncio
async def test_run_typed_agent_records_when_enabled(tmp_path, monkeypatch):
    class FakeAgent:
        def __init__(self, **_kwargs):
            pass

        def output_validator(self, fn):
            return fn

        async def run(self, _prompt):
            return SimpleNamespace(output=_answer())

    async def _execute(call, **_kwargs):
        return await call()

    monkeypatch.setenv("EXTRACTION_LLM_RECORD_DIR", str(tmp_path))
    get_settings.cache_clear()
    try:
        with (
            patch.object(extractor, "Agent", FakeAgent),
            patch.object(extractor, "_build_openrouter_model", return_value=None),
            patch.object(extractor, "_build_openrouter_model_settings", return_value={}),
            patch.object(extractor, "execute_billable_request", side_effect=_execute),
            patch.object(extractor, "_extract_openrouter_cost_usd", return_value=0.0),
        ):
            parsed, *_ = await extractor._run_typed_agent(
                system_prompt="system",
                user_prompt="user",
                result_type=PageFees,
                timeout_seconds=5,
                llm_stats=extractor.ExtractionLLMStats(),
                school_id=3,
            )
    finally:
        get_settings.cache_clear()

    assert parsed == _answer()
    expected_key = record_key(
        system_prompt="system", user_prompt="user", result_type=PageFees, tier="cheap",
        model=extractor.get_model("cheap"),
    )
    payload = json.loads((tmp_path / f"{expected_key}.json").read_text(encoding="utf-8"))
    assert payload["model"] == extractor.get_model("cheap")
