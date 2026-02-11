"""
OpenRouter client and model tier configuration for the scraping pipeline.

This module provides:
- Model tier selection (cheap/medium/capable)
- OpenRouter integration via PydanticAI
- Cost tracking helpers
"""
from typing import Literal
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIModel

from app.config import get_settings

# Model tier definitions
# See: https://openrouter.ai/models for pricing and capabilities
MODEL_TIERS = {
    "cheap": "openrouter/google/gemini-2.0-flash-lite",  # $0.075/$0.30 per 1M tokens
    "medium": "openrouter/google/gemini-2.5-flash",  # $0.15/$0.60 per 1M tokens
    "capable": "openrouter/google/gemini-2.5-pro",  # $1.25/$10.00 per 1M tokens
}

# Cost per 1M tokens (input/output)
MODEL_COSTS = {
    "cheap": (0.075, 0.30),
    "medium": (0.15, 0.60),
    "capable": (1.25, 10.00),
}

ModelTier = Literal["cheap", "medium", "capable"]


def get_model(tier: ModelTier) -> str:
    """
    Get the model string for a given tier.

    Checks for environment variable overrides (e.g., MODEL_TIER_CHEAP)
    before returning the default model.

    Args:
        tier: Model tier ("cheap", "medium", or "capable")

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
        tier: Model tier ("cheap", "medium", or "capable")

    Returns:
        Tuple of (input_cost, output_cost) per 1M tokens in USD

    Example:
        >>> get_model_costs("cheap")
        (0.075, 0.30)
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
        0.00022500000000000003
    """
    input_cost, output_cost = get_model_costs(tier)
    return (input_tokens / 1_000_000 * input_cost) + (output_tokens / 1_000_000 * output_cost)


def get_openai_model(tier: ModelTier) -> OpenAIModel:
    """
    Get an OpenAIModel instance configured for OpenRouter.

    PydanticAI uses the OpenAI-compatible API that OpenRouter provides.

    Args:
        tier: Model tier to use

    Returns:
        Configured OpenAIModel instance

    Example:
        >>> model = get_openai_model("medium")
        >>> # Use with PydanticAI Agent:
        >>> agent = Agent(model=model, result_type=MySchema)
    """
    settings = get_settings()
    model_name = get_model(tier)

    # Extract the model name without the openrouter/ prefix for the API
    # OpenRouter expects format: google/gemini-2.0-flash-lite
    api_model_name = model_name.replace("openrouter/", "")

    return OpenAIModel(
        model_name=api_model_name,
        base_url="https://openrouter.ai/api/v1",
        api_key=settings.openrouter_api_key,
    )


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
        ...     tier="medium",
        ...     system_prompt="Extract school information",
        ...     result_type=SchoolInfo
        ... )
        >>> result = await agent.run("Sofia School #1 is a state school")
        >>> print(result.data.name)
        'Sofia School #1'
    """
    model = get_openai_model(tier)

    return Agent(
        model=model,
        system_prompt=system_prompt,
        result_type=result_type,
        **agent_kwargs,
    )


# Export public API
__all__ = [
    "ModelTier",
    "get_model",
    "get_model_costs",
    "calculate_cost",
    "get_openai_model",
    "create_agent",
    "MODEL_TIERS",
    "MODEL_COSTS",
]
