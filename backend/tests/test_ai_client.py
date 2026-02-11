"""Tests for AI client and model tier configuration."""
import pytest
from app.ai.client import (
    get_model,
    get_model_costs,
    calculate_cost,
    MODEL_TIERS,
    MODEL_COSTS,
)


class TestModelTiers:
    """Test model tier configuration."""

    def test_model_tiers_defined(self):
        """All three tiers are defined."""
        assert "cheap" in MODEL_TIERS
        assert "medium" in MODEL_TIERS
        assert "capable" in MODEL_TIERS

    def test_model_costs_defined(self):
        """All three tiers have costs defined."""
        assert "cheap" in MODEL_COSTS
        assert "medium" in MODEL_COSTS
        assert "capable" in MODEL_COSTS

    def test_get_model_cheap(self):
        """Get cheap tier model."""
        model = get_model("cheap")
        assert model == "openrouter/google/gemini-2.0-flash-lite"

    def test_get_model_medium(self):
        """Get medium tier model."""
        model = get_model("medium")
        assert model == "openrouter/google/gemini-2.5-flash"

    def test_get_model_capable(self):
        """Get capable tier model."""
        model = get_model("capable")
        assert model == "openrouter/google/gemini-2.5-pro"

    def test_get_model_costs_cheap(self):
        """Get cheap tier costs."""
        input_cost, output_cost = get_model_costs("cheap")
        assert input_cost == 0.075
        assert output_cost == 0.30

    def test_get_model_costs_medium(self):
        """Get medium tier costs."""
        input_cost, output_cost = get_model_costs("medium")
        assert input_cost == 0.15
        assert output_cost == 0.60

    def test_get_model_costs_capable(self):
        """Get capable tier costs."""
        input_cost, output_cost = get_model_costs("capable")
        assert input_cost == 1.25
        assert output_cost == 10.00


class TestCostCalculation:
    """Test cost calculation functions."""

    def test_calculate_cost_cheap(self):
        """Calculate cost for cheap tier."""
        # 1000 input tokens, 500 output tokens
        cost = calculate_cost("cheap", 1000, 500)
        expected = (1000 / 1_000_000 * 0.075) + (500 / 1_000_000 * 0.30)
        assert cost == pytest.approx(expected)

    def test_calculate_cost_medium(self):
        """Calculate cost for medium tier."""
        # 10000 input tokens, 2000 output tokens
        cost = calculate_cost("medium", 10000, 2000)
        expected = (10000 / 1_000_000 * 0.15) + (2000 / 1_000_000 * 0.60)
        assert cost == pytest.approx(expected)

    def test_calculate_cost_capable(self):
        """Calculate cost for capable tier."""
        # 5000 input tokens, 1000 output tokens
        cost = calculate_cost("capable", 5000, 1000)
        expected = (5000 / 1_000_000 * 1.25) + (1000 / 1_000_000 * 10.00)
        assert cost == pytest.approx(expected)

    def test_calculate_cost_zero_tokens(self):
        """Calculate cost with zero tokens."""
        cost = calculate_cost("cheap", 0, 0)
        assert cost == 0.0

    def test_calculate_cost_large_numbers(self):
        """Calculate cost with large token counts."""
        # 1 million input, 500k output (cheap tier)
        cost = calculate_cost("cheap", 1_000_000, 500_000)
        expected = (1_000_000 / 1_000_000 * 0.075) + (500_000 / 1_000_000 * 0.30)
        assert cost == pytest.approx(expected)


class TestCostEstimates:
    """Test realistic cost estimates for the pipeline."""

    def test_navigation_stage_estimate(self):
        """Stage 3: Navigation (500 schools, cheap tier)."""
        # Estimate: 2K input + 500 output per school
        total_cost = sum(calculate_cost("cheap", 2000, 500) for _ in range(500))
        # Should be around $0.15 according to the plan
        assert 0.10 < total_cost < 0.20

    def test_extraction_stage_estimate(self):
        """Stage 4: Extraction (500 schools, medium tier)."""
        # Estimate: 4K input + 800 output per school
        total_cost = sum(calculate_cost("medium", 4000, 800) for _ in range(500))
        # Should be around $0.54 according to the plan
        assert 0.40 < total_cost < 0.70

    def test_summarization_stage_estimate(self):
        """Stage 6: Summarization (500 schools, medium tier)."""
        # Estimate: 3K input + 1000 output per school
        total_cost = sum(calculate_cost("medium", 3000, 1000) for _ in range(500))
        # Should be around $0.35 according to the plan (allowing for variance)
        assert 0.25 < total_cost < 0.60

    def test_full_pipeline_estimate(self):
        """Estimate full pipeline cost (steady-state run)."""
        # Simplified calculation
        navigation = 500 * calculate_cost("cheap", 2000, 500)
        extraction = 500 * calculate_cost("medium", 4000, 800)
        summarization = 500 * calculate_cost("medium", 3000, 1000)

        total = navigation + extraction + summarization
        # Should be around $1-2 per run according to the plan
        assert 0.80 < total < 2.50
