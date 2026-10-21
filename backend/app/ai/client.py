"""
Model client and model tier configuration for the scraping pipeline.

This module provides:
- Model selection (cheap/capable tiers)
- OpenRouter integration via PydanticAI, and Anthropic's API directly for tiers set to an
  ``anthropic/`` model (paid from the Max plan's monthly API credits)
- Cost tracking helpers
"""
import inspect
from math import isfinite
from typing import Any, Literal, Optional

from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelHTTPError, UnexpectedModelBehavior
from pydantic_ai.messages import ModelResponse
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.profiles.anthropic import AnthropicModelProfile
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.openrouter import OpenRouterProvider

from app.config import get_settings
from app.services.provider_costs import (
    ProviderCreditsExhausted,
    note_priced_request_started,
    note_priced_response,
)

# Model tier definitions
# See: https://openrouter.ai/models for pricing and capabilities
MODEL_TIERS = {
    "cheap": "openrouter/google/gemini-2.5-flash-lite",  # lightweight tier for validation/classification
    "capable": "openrouter/openai/gpt-4o-mini",  # stronger tier for spot-check validation
    # Fee lines and fee pictures. The model copies priced lines (schemas.extraction.FeeLine)
    # and code decides what they mean. On 16 hard Sofia fee pages read twice (2026-10-09)
    # this model's lines gave the same tuition set both times on 14 and held every tuition
    # amount GPT-6.1 Sol had found, at about $0.003 a school against Sol's $0.02.
    "pricing": "openrouter/openai/gpt-6-luna",
    # Reading a fee table off a picture. GPT-6 Luna's two readings of St George's tables
    # disagreed on digits; this model's agreed. A handful of pictures per city.
    "vision": "openrouter/openai/gpt-6.1-sol",
    # Checking a fee read against its pages (stages/fee_check.py). In a trial on 15 keyed
    # schools (2026-10-16) Sonnet flagged all 9 with real problems and 1 of 4 clean ones;
    # Haiku missed 2 and flagged 3 clean ones.
    "fee_check": "anthropic/claude-sonnet-5-5",
    # Translating the fee names, class names and list items a page shows as the website
    # wrote them (stages/text_translations.py): short labels, on the Max plan's API credit.
    "translation": "anthropic/claude-haiku-5-5",
}

# Cost per 1M tokens (input/output), verified against OpenRouter 2026-07-13.
MODEL_COSTS = {
    "cheap": (0.10, 0.40),
    "capable": (0.15, 0.60),
    "pricing": (0.10, 0.50),  # OpenRouter, 2026-10-09
    "vision": (2.00, 10.00),  # OpenRouter, 2026-10-09
    "fee_check": (2.00, 10.00),  # Sonnet 5.5, if the tier is pointed at OpenRouter
    "translation": (0.10, 0.50),  # Haiku 5.5, if the tier is pointed at OpenRouter
}

# Anthropic's API reports tokens, not a cost, so a tier routed there needs its model's
# price here: it is the cost every run cap and the provider ledger count. A model missing
# from this table is refused. Prices per 1M tokens (input, output), claude-api reference
# 2026-10-06; Haiku 5.5 costs (0.50, 2.50) for a whole request whose prompt is over 100K tokens.
ANTHROPIC_PRICES = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
}
ANTHROPIC_LONG_PROMPT_TOKENS = 100_000
ANTHROPIC_LONG_PROMPT_PRICES = {
    "claude-haiku-5-5": (0.50, 2.50),
}
ANTHROPIC_PREFIX = "anthropic/"
# pydantic-ai's default (4096) is far too low: one fee page with 114 fees needed more than
# 8192 tokens from Haiku 5.5 (eval 2026-10-16). 20K stays under the SDK's non-streaming
# limit (~21K), so every call is one plain request; a longer answer fails as cut off
# (below) instead of reading as empty. At Haiku's price that is at most $0.01 of output.
ANTHROPIC_MAX_OUTPUT_TOKENS = 20_000
OPENROUTER_PREFIX = "openrouter/"
# Settings Haiku 5.5 rejects with a 400 when set to anything but the default.
_ANTHROPIC_SAMPLING_SETTINGS = ("temperature", "top_p", "top_k")

# Models that can't turn thinking off and refuse the forced tool call that structured
# answers use by default: they think and answer through structured outputs
# (output_config.format). pydantic-ai 1.107 doesn't know them yet, so their profile is
# given here; drop an entry once pydantic-ai's own profile covers the model.
ANTHROPIC_THINKING_PROFILES = {
    "claude-sonnet-5-5": AnthropicModelProfile(
        supports_json_schema_output=True,
        anthropic_supports_adaptive_thinking=True,
        anthropic_supports_effort=True,
        anthropic_supports_xhigh_effort=True,
        anthropic_disallows_budget_thinking=True,
        anthropic_disallows_sampling_settings=True,
        anthropic_supports_forced_tool_choice=False,
    ),
}

ModelTier = Literal["cheap", "capable", "pricing", "vision", "fee_check", "translation"]


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


def get_provider(tier: ModelTier) -> Literal["openrouter", "anthropic"]:
    """Which API a tier calls: ``anthropic/<model>`` goes to Anthropic directly; anything
    else (``openrouter/<vendor>/<model>``, or a bare ``<vendor>/<model>`` as before) goes
    to OpenRouter. ``anthropic/...`` on OpenRouter is spelled ``openrouter/anthropic/...``."""
    return "anthropic" if get_model(tier).startswith(ANTHROPIC_PREFIX) else "openrouter"


def missing_key(tier: ModelTier) -> Optional[str]:
    """The API key setting a tier needs and this checkout's .env lacks, or None."""
    settings = get_settings()
    if get_provider(tier) == "anthropic":
        return None if settings.anthropic_api_key else "ANTHROPIC_API_KEY"
    return None if settings.openrouter_api_key else "OPENROUTER_API_KEY"


def _anthropic_model_name(tier: ModelTier) -> str:
    name = get_model(tier).removeprefix(ANTHROPIC_PREFIX)
    if name not in ANTHROPIC_PRICES:
        raise ValueError(
            f"no price for Anthropic model {name!r} (tier {tier!r}); add it to ANTHROPIC_PRICES"
        )
    return name


def anthropic_cost_usd(model_name: str, input_tokens: int, output_tokens: int) -> float:
    """Anthropic's price for one request, from its token counts."""
    prices = ANTHROPIC_PRICES[model_name]
    if input_tokens > ANTHROPIC_LONG_PROMPT_TOKENS and model_name in ANTHROPIC_LONG_PROMPT_PRICES:
        prices = ANTHROPIC_LONG_PROMPT_PRICES[model_name]
    input_price, output_price = prices
    return input_tokens / 1_000_000 * input_price + output_tokens / 1_000_000 * output_price


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
    if get_provider(tier) == "anthropic":
        return ANTHROPIC_PRICES[_anthropic_model_name(tier)]
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


def get_usage(result: Any) -> tuple[int, int]:
    """Extract input/output tokens from pydantic-ai result."""
    usage_obj = result.usage() if callable(getattr(result, "usage", None)) else getattr(result, "usage", None)
    if usage_obj is None:
        return 0, 0

    input_tokens = getattr(usage_obj, "input_tokens", None)
    if input_tokens is None:
        input_tokens = getattr(usage_obj, "request_tokens", 0)

    output_tokens = getattr(usage_obj, "output_tokens", None)
    if output_tokens is None:
        output_tokens = getattr(usage_obj, "response_tokens", 0)

    return int(input_tokens or 0), int(output_tokens or 0)


def get_openrouter_model(tier: ModelTier) -> OpenRouterModel:
    """Build the native OpenRouter model required for exact usage metadata."""
    settings = get_settings()
    model_name = get_model(tier).replace("openrouter/", "", 1)
    provider = OpenRouterProvider(api_key=settings.openrouter_api_key)
    return OpenRouterModel(model_name=model_name, provider=provider)


class PricedAnthropicModel(AnthropicModel):
    """Anthropic model whose responses carry their cost in ``provider_details["cost"]``,
    as OpenRouter's do, so run caps and the provider ledger (provider_costs) work
    unchanged. An out-of-credit or refused key stops the run (ProviderCreditsExhausted)."""

    # Only request() is priced: no stage streams. A streamed call would come back without
    # a cost, and the ledger would reject it as unattributed rather than undercount it.
    async def request(self, messages, model_settings, model_request_parameters) -> ModelResponse:
        note_priced_request_started()
        try:
            response = await super().request(messages, model_settings, model_request_parameters)
        except ModelHTTPError as exc:
            if _is_account_error(exc):
                raise ProviderCreditsExhausted(f"Anthropic refused the request: {exc}") from exc
            raise
        usage = response.usage
        cost = anthropic_cost_usd(self.model_name, usage.input_tokens, usage.output_tokens)
        # "cost" is what the ledger and caps read; cost_source records that it was computed
        # from Anthropic's token counts, not reported in dollars like OpenRouter's.
        response.provider_details = {
            **(response.provider_details or {}),
            "cost": cost,
            "cost_source": "computed_from_usage",
        }
        # The provider-cost guard books a call that then fails (invalid output twice, the
        # cut-off below) at these costs; one still in flight keeps the request reserve.
        note_priced_response(response)
        if response.finish_reason == "length":
            # A structured answer cut off at max_tokens parses as an empty or partial
            # result; fail the call so the school counts as not read, not as "no fees".
            raise UnexpectedModelBehavior(f"Anthropic answer cut off at {usage.output_tokens} output tokens")
        return response


# A 400 with one of these means no later call can succeed: the credits ran out ("Your
# credit balance is too low ...") or an organization or workspace spend limit was reached
# ("You have reached your specified ... API usage limits ..."). A usage tier's monthly cap
# comes back as a 429 with error_code "enforced_spend_limit_reached" (Anthropic's
# rate-limit docs); other 429s are ordinary rate limits that pydantic-ai retries.
_ACCOUNT_LIMIT_MESSAGES = ("credit balance", "usage limit", "spend limit")


def _is_account_error(exc: ModelHTTPError) -> bool:
    if exc.status_code in (401, 403):  # a bad or revoked key
        return True
    body = str(exc.body).lower()
    if exc.status_code == 429:
        return "enforced_spend_limit_reached" in body
    return exc.status_code == 400 and any(message in body for message in _ACCOUNT_LIMIT_MESSAGES)


def get_anthropic_model(tier: ModelTier) -> PricedAnthropicModel:
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise ValueError(f"tier {tier!r} is set to {get_model(tier)!r} but ANTHROPIC_API_KEY is empty")
    provider = AnthropicProvider(api_key=settings.anthropic_api_key)
    name = _anthropic_model_name(tier)
    profile = provider.model_profile(name)
    if name in ANTHROPIC_THINKING_PROFILES:
        profile = AnthropicModelProfile.from_profile(profile).update(ANTHROPIC_THINKING_PROFILES[name])
    return PricedAnthropicModel(name, provider=provider, profile=profile)


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
    model_settings = dict(agent_kwargs.pop("model_settings", {}) or {})
    if get_provider(tier) == "anthropic":
        model = get_anthropic_model(tier)
        for setting in _ANTHROPIC_SAMPLING_SETTINGS:
            model_settings.pop(setting, None)
        if model.model_name in ANTHROPIC_THINKING_PROFILES:
            # Thinking on makes pydantic-ai answer through structured outputs, not a tool call.
            model_settings.setdefault("anthropic_thinking", {"type": "adaptive"})
        else:
            # Haiku 5.5 thinks by default; off keeps the forced tool call that returns the
            # structured answer (Anthropic refuses forced tool calls while thinking).
            model_settings.setdefault("anthropic_thinking", {"type": "disabled"})
        # pydantic-ai's Anthropic default is 4,096 output tokens, below the 6,000 the fee and
        # facts reads budget for; OpenRouter calls have no such default.
        model_settings.setdefault("max_tokens", ANTHROPIC_MAX_OUTPUT_TOKENS)
    else:
        model = get_openrouter_model(tier)
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
    "get_provider",
    "anthropic_cost_usd",
    "get_anthropic_model",
    "get_model_costs",
    "calculate_cost",
    "extract_provider_cost_usd",
    "get_openrouter_model",
    "create_agent",
    "MODEL_TIERS",
    "MODEL_COSTS",
    "ANTHROPIC_PRICES",
]
