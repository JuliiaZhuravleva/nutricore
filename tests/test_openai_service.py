"""Unit tests for OpenAIService (the three public analysis methods).

No network: the OpenAI client's ``chat.completions.create`` is mocked and we
assert on the request the service builds and the value it returns. These lock in
the TD-004 fixes (configured model, nested ``image_url``) and the surrounding
contract (JSON response_format, tuning params, raw-string return).
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import openai
import pytest

from app.core.config import settings
from app.services.openai_service import (
    ModelUnavailableError,
    OpenAIService,
    is_model_not_found_error,
)

_FAKE_CONTENT = json.dumps(
    {
        "foods": ["apple"],
        "calories": 95,
        "protein": 0.5,
        "fats": 0.3,
        "carbs": 25,
        "portion": "1 medium apple (182g)",
    }
)


def _service_with_mock_client(content=_FAKE_CONTENT):
    """Real service, but its OpenAI client.chat.completions.create is mocked."""
    service = OpenAIService()
    fake_response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )
    service.client.chat.completions.create = AsyncMock(return_value=fake_response)
    return service


def _create_kwargs(service):
    return service.client.chat.completions.create.call_args.kwargs


# --- analyze_food_image (TD-004) ------------------------------------------


def test_analyze_food_image_uses_configured_model():
    service = _service_with_mock_client()

    asyncio.run(service.analyze_food_image("https://example.test/food.jpg"))

    kwargs = _create_kwargs(service)
    assert kwargs["model"] == settings.OPENAI_MODEL
    assert kwargs["model"] != "gpt-4-vision-preview"


def test_analyze_food_image_sends_nested_image_url():
    service = _service_with_mock_client()
    url = "https://example.test/food.jpg"

    asyncio.run(service.analyze_food_image(url))

    user_parts = _create_kwargs(service)["messages"][1]["content"]
    image_parts = [p for p in user_parts if p.get("type") == "image_url"]
    text_parts = [p for p in user_parts if p.get("type") == "text"]
    assert image_parts == [{"type": "image_url", "image_url": {"url": url}}]
    assert text_parts, "the user turn should also carry a text instruction"


def test_analyze_food_image_requests_json_and_returns_raw_content():
    service = _service_with_mock_client()

    result = asyncio.run(service.analyze_food_image("https://example.test/food.jpg"))

    kwargs = _create_kwargs(service)
    assert kwargs["response_format"] == {"type": "json_object"}
    assert kwargs["max_tokens"] == settings.OPENAI_MAX_TOKENS
    # Returns the raw JSON string (the caller parses) — not a dict.
    assert result == _FAKE_CONTENT


# --- analyze_food_entry ----------------------------------------------------


def test_analyze_food_entry_uses_configured_model_and_tuning():
    service = _service_with_mock_client()

    result = asyncio.run(service.analyze_food_entry("chicken breast 200g"))

    kwargs = _create_kwargs(service)
    assert kwargs["model"] == settings.OPENAI_MODEL
    assert kwargs["temperature"] == settings.OPENAI_TEMPERATURE
    assert kwargs["max_tokens"] == settings.OPENAI_MAX_TOKENS
    assert kwargs["response_format"] == {"type": "json_object"}
    assert result == _FAKE_CONTENT


def test_analyze_food_entry_forwards_the_text():
    service = _service_with_mock_client()

    asyncio.run(service.analyze_food_entry("chicken breast 200g"))

    messages = _create_kwargs(service)["messages"]
    assert any("chicken breast 200g" in m["content"] for m in messages)


# --- generate_health_insights ---------------------------------------------


def test_generate_health_insights_returns_free_text():
    service = _service_with_mock_client(content="You eat well.")

    result = asyncio.run(service.generate_health_insights({"calories": 2000}))

    kwargs = _create_kwargs(service)
    assert kwargs["model"] == settings.OPENAI_MODEL
    # Free-text insight — must NOT force json_object like the extraction calls.
    assert "response_format" not in kwargs
    assert result == "You eat well."


# --- client config ---------------------------------------------------------


def test_client_configured_with_max_retries():
    # Transient OpenAI errors are retried by the SDK; the count comes from settings.
    service = OpenAIService()
    assert service.client.max_retries == settings.OPENAI_MAX_RETRIES


# --- model self-heal (TD-005) ---------------------------------------------


def _openai_not_found(
    msg="The model `gpt-x` does not exist or you do not have access to it.",
):
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    response = httpx.Response(404, request=request)
    return openai.NotFoundError(msg, response=response, body=None)


def test_is_model_not_found_detects_variants():
    assert is_model_not_found_error(SimpleNamespace(code="model_not_found"))
    assert is_model_not_found_error(Exception("The model gpt-x does not exist"))
    assert is_model_not_found_error(Exception("This model has been deprecated"))
    assert not is_model_not_found_error(Exception("rate limit exceeded"))


def test_create_translates_model_not_found_to_typed_error():
    service = OpenAIService()
    service.client.chat.completions.create = AsyncMock(side_effect=_openai_not_found())

    with pytest.raises(ModelUnavailableError) as excinfo:
        asyncio.run(service.analyze_food_entry("apple"))

    # Carries the offending model so the caller can offer a replacement.
    assert excinfo.value.model == settings.OPENAI_MODEL


def test_create_reraises_unrelated_errors():
    # A non-model 400 must not be misread as a deprecation.
    service = OpenAIService()
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    bad = openai.BadRequestError(
        "invalid temperature", response=httpx.Response(400, request=request), body=None
    )
    service.client.chat.completions.create = AsyncMock(side_effect=bad)

    with pytest.raises(openai.BadRequestError):
        asyncio.run(service.analyze_food_entry("apple"))


def test_set_model_switches_subsequent_calls():
    service = _service_with_mock_client()

    service.set_model("gpt-4o")
    asyncio.run(service.analyze_food_entry("apple"))

    assert service.model == "gpt-4o"
    assert _create_kwargs(service)["model"] == "gpt-4o"


def test_list_suitable_models_filters_and_excludes():
    service = OpenAIService()
    fake = SimpleNamespace(
        data=[
            SimpleNamespace(id="gpt-4o"),
            SimpleNamespace(id="gpt-4o-mini"),
            SimpleNamespace(id="gpt-4o-audio-preview"),  # non-chat variant
            SimpleNamespace(id="gpt-3.5-turbo"),  # not in the allowlist
            SimpleNamespace(id="text-embedding-3-small"),
            SimpleNamespace(id="o1"),
        ]
    )
    service.client.models.list = AsyncMock(return_value=fake)

    models = asyncio.run(service.list_suitable_models())

    assert {"gpt-4o", "gpt-4o-mini", "o1"} <= set(models)
    assert "gpt-4o-audio-preview" not in models
    assert "gpt-3.5-turbo" not in models
    assert "text-embedding-3-small" not in models


def test_list_suitable_models_falls_back_on_error():
    service = OpenAIService()
    service.client.models.list = AsyncMock(side_effect=Exception("network down"))

    models = asyncio.run(service.list_suitable_models())

    assert models == ["gpt-4o-mini", "gpt-4o"]
