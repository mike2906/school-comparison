"""
OpenRouter client and model tier configuration for the scraping pipeline.

This module provides:
- Model selection (cheap/capable tiers)
- OpenRouter integration via PydanticAI
- Cost tracking helpers
"""
import inspect
from math import isfinite
from typing import Any, Literal

from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIModel
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.providers.openrouter import OpenRouterProvider

from app.config import get_settings

# Model tier definitions
# See: https://openrouter.ai/models for pricing and capabilities
MODEL_TIERS = {
    "cheap": "openrouter/google/gemini-2.5-flash-lite",  # lightweight tier for validation/classification
    "capable": "openrouter/openai/gpt-4o-mini",  # stronger tier for spot-check validation
}

# Cost per 1M tokens (input/output), verified against OpenRouter 2026-07-13.
MODEL_COSTS = {
    "cheap": (0.10, 0.40),
    "capable": (0.15, 0.60),
}

ModelTier = Literal["cheap", "capable"]


def get_model(tier: ModelTier) -> str:
    """
    Get the model string for a given tier.

    Checks for environment variable overrides (e.g., MODEL_TIER_CHEAP)
    before returning the default model.

    Args:
        tier: Model tier ("cheap")

    Returns:
        OpenRouter model string (e.g., "openrouter/google/gemini-2.0-flash-lite")

    Example:
        >>> get_model("cheap")
        'openrouter/google/gemini-2.0-flash-lite'
    """
    settings = get_settings()
    override = getattr(settings, f"model_tier_{tier}", None)
    return override or MODEL_TIERS[tier]


def get_model_costs(tier: ModelTier) -> tuple[float, float]:
    """
    Get the cost per 1M tokens for a given tier.

    Args:
        tier: Model tier ("cheap")

    Returns:
        Tuple of (input_cost, output_cost) per 1M tokens in USD

    Example:
        >>> get_model_costs("cheap")
        (0.10, 0.40)
    """
    return MODEL_COSTS[tier]


def calculate_cost(tier: ModelTier, input_tokens: int, output_tokens: int) -> float:
    """
    Calculate the cost of a model inference call.

    Args:
        tier: Model tier used
        input_tokens: Number of input tokens
        output_tokens: Number of output tokens

    Returns:
        Cost in USD

    Example:
        >>> calculate_cost("cheap", 1000, 500)
        0.0003
    """
    input_cost, output_cost = get_model_costs(tier)
    return (input_tokens / 1_000_000 * input_cost) + (output_tokens / 1_000_000 * output_cost)


def extract_provider_cost_usd(result: Any) -> float:
    """Read exact provider-reported cost from a PydanticAI result when available."""
    messages = []
    if callable(getattr(result, "all_messages", None)):
        try:
            messages = result.all_messages()
        except Exception:
            messages = []
    total = 0.0
    for message in messages:
        details = getattr(message, "provider_details", None)
        if not isinstance(details, dict) or details.get("cost") is None:
            continue
        try:
            value = float(details["cost"])
        except (TypeError, ValueError, OverflowError):
            continue
        if isfinite(value) and value >= 0:
            total += value
    if total > 0:
        return total

    response = getattr(result, "response", None)
    details = getattr(response, "provider_details", None) if response is not None else None
    if isinstance(details, dict):
        try:
            value = float(details.get("cost") or 0.0)
        except (TypeError, ValueError, OverflowError):
            return 0.0
        return value if isfinite(value) and value >= 0 else 0.0
    return 0.0


def get_openai_model(tier: ModelTier) -> OpenAIModel:
    """
    Get an OpenAIModel instance configured for OpenRouter.

    PydanticAI uses the OpenAI-compatible API that OpenRouter provides.

    Args:
        tier: Model tier to use

    Returns:
        Configured OpenAIModel instance

    Example:
        >>> model = get_openai_model("cheap")
        >>> # Use with PydanticAI Agent:
        >>> agent = Agent(model=model, result_type=MySchema)
    """
    settings = get_settings()
    model_name = get_model(tier)

    # Extract the model name without the openrouter/ prefix for the API
    # OpenRouter expects format: google/gemini-2.0-flash-lite
    api_model_name = model_name.replace("openrouter/", "")

    # pydantic-ai changed OpenAIModel initialization to use `provider=...`.
    # Keep a compatibility fallback for older versions that accept base_url/api_key directly.
    init_params = inspect.signature(OpenAIModel.__init__).parameters
    if "provider" in init_params:
        provider = OpenAIProvider(
            base_url="https://openrouter.ai/api/v1",
            api_key=settings.openrouter_api_key,
        )
        return OpenAIModel(
            model_name=api_model_name,
            provider=provider,
        )

    return OpenAIModel(
        model_name=api_model_name,
        base_url="https://openrouter.ai/api/v1",
        api_key=settings.openrouter_api_key,
    )


def get_openrouter_model(tier: ModelTier) -> OpenRouterModel:
    """Build the native OpenRouter model required for exact usage metadata."""
    settings = get_settings()
    model_name = get_model(tier).replace("openrouter/", "", 1)
    provider = OpenRouterProvider(api_key=settings.openrouter_api_key)
    return OpenRouterModel(model_name=model_name, provider=provider)


def create_agent(
    tier: ModelTier,
    system_prompt: str,
    result_type: type,
    **agent_kwargs,
) -> Agent:
    """
    Create a PydanticAI agent with the specified tier and configuration.

    This is a convenience wrapper around Agent() that handles model
    selection and OpenRouter configuration.

    Args:
        tier: Model tier to use
        system_prompt: System prompt for the agent
        result_type: Pydantic model class for structured output
        **agent_kwargs: Additional arguments passed to Agent()

    Returns:
        Configured PydanticAI Agent

    Example:
        >>> from pydantic import BaseModel
        >>> class SchoolInfo(BaseModel):
        ...     name: str
        ...     type: str
        >>> agent = create_agent(
        ...     tier="cheap",
        ...     system_prompt="Extract school information",
        ...     result_type=SchoolInfo
        ... )
        >>> result = await agent.run("Sofia School #1 is a state school")
        >>> print(result.data.name)
        'Sofia School #1'
    """
    model = get_openrouter_model(tier)
    model_settings = dict(agent_kwargs.pop("model_settings", {}) or {})
    model_settings.setdefault("openrouter_usage", {"include": True})
    init_params = inspect.signature(Agent.__init__).parameters
    if "output_type" in init_params:
        return Agent(
            model=model,
            system_prompt=system_prompt,
            output_type=result_type,
            model_settings=model_settings,
            **agent_kwargs,
        )

    return Agent(
        model=model,
        system_prompt=system_prompt,
        result_type=result_type,
        model_settings=model_settings,
        **agent_kwargs,
    )


# Export public API
__all__ = [
    "ModelTier",
    "get_model",
    "get_model_costs",
    "calculate_cost",
    "extract_provider_cost_usd",
    "get_openai_model",
    "get_openrouter_model",
    "create_agent",
    "MODEL_TIERS",
    "MODEL_COSTS",
]
