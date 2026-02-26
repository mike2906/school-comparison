"""Tests for v1 vs v2 benchmark utilities."""

from __future__ import annotations

import os

from scripts import compare_pipeline_v1_v2 as bench


def test_compute_metrics_counts_useful_and_malformed_fields():
    attrs = {
        "extracted": {
            "languages": [{"language": "English"}],
            "facilities": ["pool"],
            "programs": ["STEM"],
            "extracurricular": [],
            "accreditations": [],
            "class_size": "18 students",
            "founded_year": "1998",
            "contact": {"phones": ["+359888111222"], "emails": []},
        }
    }

    metrics = bench._compute_metrics(attrs)
    assert metrics["useful_fields"] >= 5
    assert metrics["malformed_rate"] == 0.0


def test_compute_guardrail_verdict_balanced_thresholds():
    v1 = {
        "avg_useful_fields": 5.0,
        "failure_rate": 0.10,
        "total_tokens": 1000,
    }
    v2 = {
        "avg_useful_fields": 4.6,
        "failure_rate": 0.14,
        "total_tokens": 1200,
    }

    verdict = bench._compute_guardrail_verdict(v1, v2, code_reduction_ratio=0.45)
    assert verdict["passes_guardrails"] is True
    assert verdict["clean_win"] is True


def test_select_spot_check_candidates_orders_by_divergence():
    v1_rows = [
        {"school_id": 1, "useful_fields": 6, "malformed_rate": 0.0, "input_tokens": 500, "extract_status": "extracted"},
        {"school_id": 2, "useful_fields": 2, "malformed_rate": 0.0, "input_tokens": 400, "extract_status": "extracted"},
    ]
    v2_rows = [
        {"school_id": 1, "useful_fields": 2, "malformed_rate": 0.2, "input_tokens": 1500, "extract_status": "extracted"},
        {"school_id": 2, "useful_fields": 2, "malformed_rate": 0.0, "input_tokens": 410, "extract_status": "extracted"},
    ]

    candidates = bench._select_spot_check_candidates(v1_rows, v2_rows, top_n=1)
    assert len(candidates) == 1
    assert candidates[0]["school_id"] == 1


def test_configure_models_sets_openrouter_routing_env(monkeypatch):
    monkeypatch.delenv("EXTRACTION_OPENROUTER_MODELS", raising=False)
    monkeypatch.delenv("EXTRACTION_OPENROUTER_PROVIDER_ORDER", raising=False)
    monkeypatch.delenv("EXTRACTION_OPENROUTER_PROVIDER_SORT", raising=False)
    monkeypatch.delenv("EXTRACTION_OPENROUTER_PROVIDER_ALLOW_FALLBACKS", raising=False)

    profile = bench._configure_models(
        cheap_model="openrouter/google/gemini-2.5-flash-lite",
        medium_model="openrouter/google/gemini-2.5-flash-lite",
        capable_model="openrouter/google/gemini-2.5-flash-lite",
        openrouter_models="openai/gpt-4o-mini",
        openrouter_provider_order="openai",
        openrouter_provider_sort="latency",
        openrouter_provider_allow_fallbacks="true",
    )

    assert profile["extraction_openrouter_models"] == "openai/gpt-4o-mini"
    assert os.environ["EXTRACTION_OPENROUTER_MODELS"] == "openai/gpt-4o-mini"
    assert os.environ["EXTRACTION_OPENROUTER_PROVIDER_ORDER"] == "openai"
    assert os.environ["EXTRACTION_OPENROUTER_PROVIDER_SORT"] == "latency"
    assert os.environ["EXTRACTION_OPENROUTER_PROVIDER_ALLOW_FALLBACKS"] == "true"


def test_resolve_row_cost_prefers_extractor_reported_cost():
    cost, source = bench._resolve_row_cost(
        ext_result={"token_cost_usd": 0.1234567},
        input_tokens=5000,
        output_tokens=500,
    )
    assert cost == 0.123457
    assert source == "extractor_reported"


def test_resolve_row_cost_falls_back_to_cheap_estimate():
    cost, source = bench._resolve_row_cost(
        ext_result={},
        input_tokens=1000,
        output_tokens=1000,
    )
    assert source == "estimated_cheap"
    assert cost > 0


def test_count_loc_missing_file_warns_and_returns_zero(tmp_path, capsys):
    missing_path = tmp_path / "does_not_exist.py"

    loc = bench._count_loc(missing_path)
    out = capsys.readouterr().out

    assert loc == 0
    assert "[WARN] LOC file not found:" in out
    assert str(missing_path) in out
