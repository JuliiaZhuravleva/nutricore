"""Unit tests for OpenAIService.analyze_food_image (TD-004 regression guard).

The image path was broken four ways at once; two of them are silent-return
regressions we lock in here (no network — the OpenAI client is mocked):
  1. it hardcoded the removed `gpt-4-vision-preview` model instead of the
     configured `settings.OPENAI_MODEL`;
  2. it sent the bare-string `image_url` form instead of the nested `{"url": ...}`.
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


def _service_with_mock_client():
    """Real service, but its OpenAI client.chat.completions.create is mocked."""
    service = OpenAIService()
    fake_response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=_FAKE_CONTENT))]
    )
    service.client.chat.completions.create = AsyncMock(return_value=fake_response)
    return service


def test_analyze_food_image_uses_configured_model():
    service = _service_with_mock_client()

    asyncio.run(service.analyze_food_image("https://example.test/food.jpg"))

    kwargs = service.client.chat.completions.create.call_args.kwargs
    assert kwargs["model"] == settings.OPENAI_MODEL
    assert kwargs["model"] != "gpt-4-vision-preview"


def test_analyze_food_image_sends_nested_image_url():
    service = _service_with_mock_client()
    url = "https://example.test/food.jpg"

    asyncio.run(service.analyze_food_image(url))

    kwargs = service.client.chat.completions.create.call_args.kwargs
    # messages: [system, user]; the user content is a list of parts.
    user_parts = kwargs["messages"][1]["content"]
    image_parts = [p for p in user_parts if p.get("type") == "image_url"]
    assert image_parts == [{"type": "image_url", "image_url": {"url": url}}]
