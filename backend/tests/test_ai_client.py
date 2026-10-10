"""Tests for AI client model-tier configuration."""

from types import SimpleNamespace

import pytest

from app.ai.client import (
    MODEL_COSTS,
    MODEL_TIERS,
    calculate_cost,
    get_model,
    get_model_costs,
)


@pytest.fixture(autouse=True)
def _default_model_tier_settings(monkeypatch):
    """Keep tests deterministic regardless of local .env overrides."""
    monkeypatch.setattr(
        "app.ai.client.get_settings",
        lambda: SimpleNamespace(
            openrouter_api_key="test-key",
            model_tier_cheap="",
            model_tier_capable="",
        ),
    )


class TestModelTier:
    """Test model tier configuration."""

    def test_model_tiers_defined(self):
        assert MODEL_TIERS == {
            "cheap": "openrouter/google/gemini-2.5-flash-lite",
            "capable": "openrouter/openai/gpt-4o-mini",
            "pricing": "openrouter/openai/gpt-6-luna",
            "vision": "openrouter/openai/gpt-6.1-sol",
        }

    def test_model_costs_defined(self):
        assert MODEL_COSTS == {
            "cheap": (0.10, 0.40),
            "capable": (0.15, 0.60),
            "pricing": (0.10, 0.50),
            "vision": (2.00, 10.00),
        }

    def test_get_model_cheap(self):
        model = get_model("cheap")
        assert model == "openrouter/google/gemini-2.5-flash-lite"

    def test_get_model_costs_cheap(self):
        input_cost, output_cost = get_model_costs("cheap")
        assert input_cost == 0.10
        assert output_cost == 0.40

    def test_get_model_capable(self):
        model = get_model("capable")
        assert model == "openrouter/openai/gpt-4o-mini"

    def test_get_model_costs_capable(self):
        input_cost, output_cost = get_model_costs("capable")
        assert input_cost == 0.15
        assert output_cost == 0.60


class TestCostCalculation:
    """Test cost calculation functions."""

    def test_calculate_cost_cheap(self):
        cost = calculate_cost("cheap", 1000, 500)
        expected = (1000 / 1_000_000 * 0.10) + (500 / 1_000_000 * 0.40)
        assert cost == pytest.approx(expected)

    def test_calculate_cost_zero_tokens(self):
        cost = calculate_cost("cheap", 0, 0)
        assert cost == 0.0

    def test_calculate_cost_large_numbers(self):
        cost = calculate_cost("cheap", 1_000_000, 500_000)
        expected = (1_000_000 / 1_000_000 * 0.10) + (500_000 / 1_000_000 * 0.40)
        assert cost == pytest.approx(expected)


class TestCostEstimates:
    """Test realistic cost estimates for the pipeline."""

    def test_navigation_stage_estimate(self):
        total_cost = sum(calculate_cost("cheap", 2000, 500) for _ in range(500))
        assert total_cost == pytest.approx(0.20)

    def test_extraction_stage_estimate(self):
        total_cost = sum(calculate_cost("cheap", 4000, 800) for _ in range(500))
        assert total_cost == pytest.approx(0.36)

    def test_summarization_stage_estimate(self):
        total_cost = sum(calculate_cost("capable", 3000, 1000) for _ in range(500))
        assert total_cost == pytest.approx(0.525)

    def test_full_pipeline_estimate(self):
        navigation = 500 * calculate_cost("cheap", 2000, 500)
        extraction = 500 * calculate_cost("cheap", 4000, 800)
        summarization = 500 * calculate_cost("capable", 3000, 1000)
        total = navigation + extraction + summarization
        assert total == pytest.approx(1.085)


def test_create_agent_uses_native_openrouter_exact_usage_settings(monkeypatch):
    from app.ai import client as ai_client

    captured = {}

    class DummyProvider:
        def __init__(self, *, api_key):
            captured["api_key"] = api_key

    class DummyModel:
        def __init__(self, model_name, *, provider):
            captured["model_name"] = model_name
            captured["provider"] = provider

    class DummyAgent:
        def __init__(self, **kwargs):
            captured["agent_kwargs"] = kwargs

    monkeypatch.setattr(ai_client, "OpenRouterProvider", DummyProvider)
    monkeypatch.setattr(ai_client, "OpenRouterModel", DummyModel)
    monkeypatch.setattr(ai_client, "Agent", DummyAgent)

    ai_client.create_agent("cheap", "system", dict)

    assert captured["model_name"] == "google/gemini-2.5-flash-lite"
    assert captured["api_key"] == "test-key"
    assert captured["agent_kwargs"]["model_settings"] == {
        "openrouter_usage": {"include": True}
    }
