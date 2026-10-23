"""Tiers routed to Anthropic's API directly (``anthropic/<model>``), next to OpenRouter."""

import json
from types import SimpleNamespace

import httpx
import pytest
from pydantic import BaseModel
from pydantic_ai.exceptions import UnexpectedModelBehavior
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
        model_tier_fee_check=tiers.get("fee_check", ""),
        model_tier_translation=tiers.get("translation", ""),
        model_tier_facts=tiers.get("facts", ""),
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


def test_a_bare_vendor_model_id_stays_on_openrouter(monkeypatch):
    monkeypatch.setattr(ai_client, "get_settings", lambda: _settings(capable="openai/gpt-4o-mini"))
    assert ai_client.get_provider("capable") == "openrouter"
    assert ai_client.get_model_costs("capable") == ai_client.MODEL_COSTS["capable"]


def test_an_unpriced_anthropic_model_is_refused(monkeypatch):
    monkeypatch.setattr(ai_client, "get_settings", lambda: _settings(pricing="anthropic/claude-opus-5-5"))
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
    assert body["max_tokens"] == ai_client.ANTHROPIC_MAX_OUTPUT_TOKENS
    assert result.response.provider_details["cost_source"] == "computed_from_usage"
    expected = 20_000 / 1e6 * 0.10 + 400 / 1e6 * 0.50
    assert ai_client.extract_provider_cost_usd(result) == pytest.approx(expected)
    # The provider ledger accepts the response as exactly attributed, as with OpenRouter.
    [attribution] = extract_exact_provider_attributions(result)
    assert attribution["provider_request_id"] == "msg_test_1"
    assert attribution["input_tokens"] == 20_000
    assert float(attribution["provider_cost_usd"]) == pytest.approx(expected)


async def test_an_answer_cut_off_at_max_tokens_fails_instead_of_reading_as_empty(monkeypatch, haiku_pricing):
    def cut_off(request):
        response = _tool_answer(request)
        body = response.json()
        body["content"][0]["input"] = {}
        body["stop_reason"] = "max_tokens"
        body["usage"]["output_tokens"] = ai_client.ANTHROPIC_MAX_OUTPUT_TOKENS
        return httpx.Response(200, json=body)

    _mock_anthropic(monkeypatch, cut_off)
    agent = ai_client.create_agent("pricing", "system", Pick)
    with pytest.raises(UnexpectedModelBehavior, match="cut off"):
        await agent.run("hello")


async def test_a_cut_off_answer_is_noted_at_its_price_for_the_cost_guard(monkeypatch, haiku_pricing):
    from app.services import provider_costs

    def cut_off(request):
        body = _tool_answer(request).json()
        body["stop_reason"] = "max_tokens"
        return httpx.Response(200, json=body)

    _mock_anthropic(monkeypatch, cut_off)
    call = provider_costs._PricedCall()
    token = provider_costs._PRICED_CALL.set(call)
    try:
        with pytest.raises(UnexpectedModelBehavior):
            await ai_client.create_agent("pricing", "system", Pick).run("hello")
    finally:
        provider_costs._PRICED_CALL.reset(token)
    assert call.started == len(call.responses) == 1
    [row] = provider_costs._failed_call_attributions(call)
    assert float(row["provider_cost_usd"]) > 0


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (400, {"type": "invalid_request_error", "message": "Your credit balance is too low to access the Anthropic API."}),
        (400, {"type": "invalid_request_error", "message": "You have reached your specified workspace API usage limits."}),
        (401, {"type": "authentication_error", "message": "invalid x-api-key"}),
        (429, {"type": "rate_limit_error", "message": "Monthly spend limit reached.",
               "details": {"error_code": "enforced_spend_limit_reached"}}),
    ],
)
async def test_an_account_refusal_stops_the_run(monkeypatch, haiku_pricing, status, error):
    _mock_anthropic(monkeypatch, lambda request: httpx.Response(status, json={"type": "error", "error": error}))
    agent = ai_client.create_agent("pricing", "system", Pick)
    with pytest.raises(ProviderCreditsExhausted):
        await agent.run("hello")


def test_the_legacy_extractor_refuses_an_anthropic_tier(monkeypatch):
    from app.scrapers import extractor

    monkeypatch.setattr(extractor, "get_settings", lambda: _settings(cheap="anthropic/claude-haiku-5-5"))
    monkeypatch.setattr(ai_client, "get_settings", lambda: _settings(cheap="anthropic/claude-haiku-5-5"))
    with pytest.raises(ValueError, match="OpenRouter only"):
        extractor._build_openrouter_model("cheap")


async def test_two_requests_are_each_priced_on_their_own_prompt(monkeypatch, haiku_pricing):
    """Two 60K-token requests stay at the base price: the 100K threshold is per request."""
    calls = iter([
        {"content": [{"type": "text", "text": "not json"}], "stop_reason": "end_turn"},
        None,
    ])

    def handler(request):
        step = next(calls)
        if step is None:
            response = _tool_answer(request).json()
        else:
            response = {"id": "msg_test_0", "type": "message", "role": "assistant", "model": "claude-haiku-5-5",
                        "stop_sequence": None, **step}
        response["usage"] = {"input_tokens": 60_000, "output_tokens": 100}
        response["id"] = f"msg_{60_000}_{id(step)}"
        return httpx.Response(200, json=response)

    _mock_anthropic(monkeypatch, handler)
    agent = ai_client.create_agent("pricing", "system", Pick, output_retries=1)
    result = await agent.run("hello")
    per_request = 60_000 / 1e6 * 0.10 + 100 / 1e6 * 0.50
    assert ai_client.extract_provider_cost_usd(result) == pytest.approx(2 * per_request)
    assert len(extract_exact_provider_attributions(result)) == 2


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (400, {"type": "invalid_request_error", "message": "prompt is too long"}),
        (429, {"type": "rate_limit_error", "message": "Number of request tokens has exceeded your per-minute rate limit"}),
    ],
)
async def test_other_request_errors_stay_per_call_errors(monkeypatch, haiku_pricing, status, error):
    _mock_anthropic(
        monkeypatch,
        lambda request: httpx.Response(status, json={"type": "error", "error": error}, headers={"retry-after": "0"}),
    )
    agent = ai_client.create_agent("pricing", "system", Pick)
    with pytest.raises(Exception) as caught:
        await agent.run("hello")
    assert not isinstance(caught.value, ProviderCreditsExhausted)


async def test_a_thinking_model_answers_in_json_with_adaptive_thinking(monkeypatch):
    """Sonnet 5.5 refuses a forced tool and disabled thinking: it gets structured output instead."""
    monkeypatch.setattr(ai_client, "get_settings", lambda: _settings())

    def answer(request):
        return httpx.Response(
            200,
            json={
                "id": "msg_test_s",
                "type": "message",
                "role": "assistant",
                "model": "claude-sonnet-5-5",
                "content": [
                    {"type": "thinking", "thinking": "the fee page", "signature": "sig"},
                    {"type": "text", "text": '{"url": "/fees"}'},
                ],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 10_000, "output_tokens": 1_000},
            },
        )

    sent = _mock_anthropic(monkeypatch, answer)
    agent = ai_client.create_agent("fee_check", "Pick the fee page.", Pick, model_settings={"anthropic_effort": "medium"})

    result = await agent.run("links: /fees /about")

    assert result.output == Pick(url="/fees")
    body = sent[0]
    assert body["model"] == "claude-sonnet-5-5"
    assert body["thinking"] == {"type": "adaptive"}
    assert "tool_choice" not in body and "temperature" not in body
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["output_config"]["effort"] == "medium"
    expected = 10_000 / 1e6 * 2.00 + 1_000 / 1e6 * 10.00
    assert ai_client.extract_provider_cost_usd(result) == pytest.approx(expected)
