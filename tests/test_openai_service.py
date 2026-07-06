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

from app.core.config import settings
from app.services.openai_service import OpenAIService

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
