"""Tiers routed to Anthropic's API directly (``anthropic/<model>``), next to OpenRouter."""

import json
from types import SimpleNamespace

import httpx
import pytest
from pydantic import BaseModel
from pydantic_ai.providers.anthropic import AnthropicProvider

from app.ai import client as ai_client
from app.services.provider_costs import ProviderCreditsExhausted, extract_exact_provider_attributions


class Pick(BaseModel):
    url: str


def _settings(**tiers):
    return SimpleNamespace(
        openrouter_api_key="or-key",
        anthropic_api_key="ant-key",
        model_tier_cheap=tiers.get("cheap", ""),
        model_tier_capable=tiers.get("capable", ""),
        model_tier_pricing=tiers.get("pricing", ""),
        model_tier_vision=tiers.get("vision", ""),
    )


@pytest.fixture
def haiku_pricing(monkeypatch):
    monkeypatch.setattr(ai_client, "get_settings", lambda: _settings(pricing="anthropic/claude-haiku-5-5"))


def _mock_anthropic(monkeypatch, handler):
    """Route the Anthropic SDK through ``handler`` and record each request body."""
    sent = []

    def transport(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return handler(request)

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    monkeypatch.setattr(
        ai_client,
        "AnthropicProvider",
        lambda api_key: AnthropicProvider(api_key=api_key, http_client=http_client),
    )
    return sent


def _tool_answer(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    tool_name = body["tools"][0]["name"]
    return httpx.Response(
        200,
        json={
            "id": "msg_test_1",
            "type": "message",
            "role": "assistant",
            "model": "claude-haiku-5-5",
            "content": [{"type": "tool_use", "id": "toolu_1", "name": tool_name, "input": {"url": "/fees"}}],
            "stop_reason": "tool_use",
            "stop_sequence": None,
            "usage": {"input_tokens": 20_000, "output_tokens": 400},
        },
    )


def test_provider_follows_the_tier_prefix(monkeypatch):
    monkeypatch.setattr(ai_client, "get_settings", lambda: _settings(pricing="anthropic/claude-haiku-5-5"))
    assert ai_client.get_provider("pricing") == "anthropic"
    assert ai_client.get_provider("cheap") == "openrouter"


def test_an_unknown_prefix_is_refused(monkeypatch):
    monkeypatch.setattr(ai_client, "get_settings", lambda: _settings(pricing="claude-haiku-5-5"))
    with pytest.raises(ValueError, match="must start with"):
        ai_client.get_provider("pricing")


def test_an_unpriced_anthropic_model_is_refused(monkeypatch):
    monkeypatch.setattr(ai_client, "get_settings", lambda: _settings(pricing="anthropic/claude-sonnet-5-5"))
    with pytest.raises(ValueError, match="no price"):
        ai_client.create_agent("pricing", "system", Pick)
    with pytest.raises(ValueError, match="no price"):
        ai_client.get_model_costs("pricing")


def test_a_missing_key_is_refused(monkeypatch):
    settings = _settings(pricing="anthropic/claude-haiku-5-5")
    settings.anthropic_api_key = ""
    monkeypatch.setattr(ai_client, "get_settings", lambda: settings)
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        ai_client.create_agent("pricing", "system", Pick)


def test_prices_follow_the_anthropic_model(haiku_pricing):
    assert ai_client.get_model_costs("pricing") == (0.10, 0.50)
    assert ai_client.calculate_cost("pricing", 1_000_000, 1_000_000) == pytest.approx(0.60)
    assert ai_client.anthropic_cost_usd("claude-haiku-5-5", 100_000, 0) == pytest.approx(0.01)
    # Over 100K prompt tokens the whole request is billed at the long-prompt price.
    assert ai_client.anthropic_cost_usd("claude-haiku-5-5", 100_001, 1_000) == pytest.approx(
        100_001 / 1e6 * 0.50 + 1_000 / 1e6 * 2.50
    )


def test_openrouter_tiers_keep_their_settings_next_to_an_anthropic_tier(monkeypatch):
    captured = {}

    class DummyAgent:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(ai_client, "get_settings", lambda: _settings(pricing="anthropic/claude-haiku-5-5"))
    monkeypatch.setattr(ai_client, "Agent", DummyAgent)
    ai_client.create_agent("cheap", "system", Pick, model_settings={"temperature": 0})
    assert type(captured["model"]).__name__ == "OpenRouterModel"
    assert captured["model_settings"] == {"temperature": 0, "openrouter_usage": {"include": True}}


async def test_an_anthropic_call_sends_no_sampling_settings_and_reports_its_cost(monkeypatch, haiku_pricing):
    sent = _mock_anthropic(monkeypatch, _tool_answer)
    agent = ai_client.create_agent("pricing", "Pick the fee page.", Pick, model_settings={"temperature": 0.2})

    result = await agent.run("links: /fees /about")

    assert result.output == Pick(url="/fees")
    body = sent[0]
    assert body["model"] == "claude-haiku-5-5"
    assert "temperature" not in body
    assert body["thinking"] == {"type": "disabled"}
    assert body["tool_choice"]["type"] == "any"
    expected = 20_000 / 1e6 * 0.10 + 400 / 1e6 * 0.50
    assert ai_client.extract_provider_cost_usd(result) == pytest.approx(expected)
    # The provider ledger accepts the response as exactly attributed, as with OpenRouter.
    [attribution] = extract_exact_provider_attributions(result)
    assert attribution["provider_request_id"] == "msg_test_1"
    assert attribution["input_tokens"] == 20_000
    assert float(attribution["provider_cost_usd"]) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (400, {"type": "invalid_request_error", "message": "Your credit balance is too low to access the Anthropic API."}),
        (401, {"type": "authentication_error", "message": "invalid x-api-key"}),
    ],
)
async def test_an_account_refusal_stops_the_run(monkeypatch, haiku_pricing, status, error):
    _mock_anthropic(monkeypatch, lambda request: httpx.Response(status, json={"type": "error", "error": error}))
    agent = ai_client.create_agent("pricing", "system", Pick)
    with pytest.raises(ProviderCreditsExhausted):
        await agent.run("hello")


async def test_other_request_errors_stay_per_call_errors(monkeypatch, haiku_pricing):
    error = {"type": "invalid_request_error", "message": "prompt is too long"}
    _mock_anthropic(monkeypatch, lambda request: httpx.Response(400, json={"type": "error", "error": error}))
    agent = ai_client.create_agent("pricing", "system", Pick)
    with pytest.raises(Exception) as caught:
        await agent.run("hello")
    assert not isinstance(caught.value, ProviderCreditsExhausted)
