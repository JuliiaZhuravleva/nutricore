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


# --- embed_text (B2 / ADR-0003 §2) ----------------------------------------


def _fake_embedding_response(embeddings: list[list[float]]):
    """SimpleNamespace that mimics openai.types.CreateEmbeddingResponse."""
    return SimpleNamespace(
        data=[
            SimpleNamespace(embedding=emb, index=i) for i, emb in enumerate(embeddings)
        ]
    )


def _service_with_mock_embeddings(embeddings: list[list[float]]):
    """Real service with client.embeddings.create mocked."""
    service = OpenAIService()
    service.client.embeddings.create = AsyncMock(
        return_value=_fake_embedding_response(embeddings)
    )
    return service


_FAKE_VECTOR = [0.1, 0.2, 0.3]


def test_embed_text_uses_configured_model_and_dims(monkeypatch):
    """embed_text passes OPENAI_EMBEDDING_MODEL and OPENAI_EMBEDDING_DIMS."""
    monkeypatch.setattr("app.services.openai_service.record_ai_call", lambda **_: None)
    service = _service_with_mock_embeddings([_FAKE_VECTOR])

    asyncio.run(service.embed_text("Greek yogurt"))

    call_kwargs = service.client.embeddings.create.call_args.kwargs
    assert call_kwargs["model"] == settings.OPENAI_EMBEDDING_MODEL
    assert call_kwargs["dimensions"] == settings.OPENAI_EMBEDDING_DIMS
    assert call_kwargs["input"] == "Greek yogurt"


def test_embed_text_returns_embedding_vector(monkeypatch):
    """embed_text returns the list[float] from response.data[0].embedding."""
    monkeypatch.setattr("app.services.openai_service.record_ai_call", lambda **_: None)
    vector = [0.42, 0.11, 0.99]
    service = _service_with_mock_embeddings([vector])

    result = asyncio.run(service.embed_text("avocado"))

    assert result == vector


def test_embed_text_records_ok_audit_log(monkeypatch):
    """embed_text records kind='embedding' status='ok' on success."""
    logged = {}
    monkeypatch.setattr(
        "app.services.openai_service.record_ai_call",
        lambda **kw: logged.update(kw),
    )
    service = _service_with_mock_embeddings([[0.0, 0.5]])

    asyncio.run(service.embed_text("apple"))

    assert logged["kind"] == "embedding"
    assert logged["status"] == "ok"
    assert logged["model"] == settings.OPENAI_EMBEDDING_MODEL
    assert logged["input_ref"] == "apple"
    assert logged["parsed_result"] == {"dims": 2}


def test_embed_text_truncates_long_input_ref(monkeypatch):
    """Long text is truncated to 200 chars in the audit log input_ref."""
    logged = {}
    monkeypatch.setattr(
        "app.services.openai_service.record_ai_call",
        lambda **kw: logged.update(kw),
    )
    service = _service_with_mock_embeddings([[0.1]])
    long_text = "а" * 300

    asyncio.run(service.embed_text(long_text))

    assert len(logged["input_ref"]) == 200


def test_embed_text_records_error_log_and_reraises(monkeypatch):
    """On API failure embed_text records status='error' and re-raises."""
    logged = {}
    monkeypatch.setattr(
        "app.services.openai_service.record_ai_call",
        lambda **kw: logged.update(kw),
    )
    service = OpenAIService()
    service.client.embeddings.create = AsyncMock(
        side_effect=Exception("rate limit hit")
    )

    with pytest.raises(Exception, match="rate limit hit"):
        asyncio.run(service.embed_text("chicken"))

    assert logged["kind"] == "embedding"
    assert logged["status"] == "error"
    assert "rate limit hit" in logged["error"]


# --- embed_texts (batch, B2 / ADR-0003 §2) ---------------------------------


def test_embed_texts_empty_returns_empty_no_api_call(monkeypatch):
    """embed_texts([]) returns [] without calling the API."""
    monkeypatch.setattr("app.services.openai_service.record_ai_call", lambda **_: None)
    service = OpenAIService()
    service.client.embeddings.create = AsyncMock()

    result = asyncio.run(service.embed_texts([]))

    assert result == []
    service.client.embeddings.create.assert_not_called()


def test_embed_texts_batch_call_returns_in_order(monkeypatch):
    """embed_texts sends list input and returns embeddings in input order."""
    monkeypatch.setattr("app.services.openai_service.record_ai_call", lambda **_: None)
    vec_a = [1.0, 0.0]
    vec_b = [0.0, 1.0]
    # Simulate API returning items in reverse index order to test sorting.
    service = OpenAIService()
    service.client.embeddings.create = AsyncMock(
        return_value=SimpleNamespace(
            data=[
                SimpleNamespace(embedding=vec_b, index=1),
                SimpleNamespace(embedding=vec_a, index=0),
            ]
        )
    )

    result = asyncio.run(service.embed_texts(["first", "second"]))

    assert result == [vec_a, vec_b]
    call_kwargs = service.client.embeddings.create.call_args.kwargs
    assert call_kwargs["input"] == ["first", "second"]
    assert call_kwargs["model"] == settings.OPENAI_EMBEDDING_MODEL
    assert call_kwargs["dimensions"] == settings.OPENAI_EMBEDDING_DIMS


def test_embed_texts_records_ok_audit_log(monkeypatch):
    """embed_texts records kind='embedding' status='ok' with count+dims."""
    logged = {}
    monkeypatch.setattr(
        "app.services.openai_service.record_ai_call",
        lambda **kw: logged.update(kw),
    )
    service = _service_with_mock_embeddings([[0.1, 0.2], [0.3, 0.4]])

    asyncio.run(service.embed_texts(["apple", "яблоко"]))

    assert logged["kind"] == "embedding"
    assert logged["status"] == "ok"
    assert logged["parsed_result"] == {"count": 2, "dims": 2}


def test_embed_texts_records_error_log_and_reraises(monkeypatch):
    """On batch failure embed_texts records status='error' and re-raises."""
    logged = {}
    monkeypatch.setattr(
        "app.services.openai_service.record_ai_call",
        lambda **kw: logged.update(kw),
    )
    service = OpenAIService()
    service.client.embeddings.create = AsyncMock(
        side_effect=Exception("quota exceeded")
    )

    with pytest.raises(Exception, match="quota exceeded"):
        asyncio.run(service.embed_texts(["apple", "banana"]))

    assert logged["kind"] == "embedding"
    assert logged["status"] == "error"
    assert "quota exceeded" in logged["error"]


# --- persisted model override on construction (TD-007) ----------------------


def test_construction_applies_persisted_model_override(monkeypatch):
    """OpenAIService picks up the persisted runtime model override at construction,
    so every instance — not just the bot singleton — honours the owner's in-chat
    model switch (TD-005 self-heal made consistent across instances by TD-007)."""
    monkeypatch.setattr(
        "app.services.openai_service.get_persisted_model",
        lambda: "gpt-persisted-init",
    )
    service = OpenAIService()
    assert service.model == "gpt-persisted-init"


def test_construction_falls_back_to_configured_model(monkeypatch):
    """No persisted override → the configured default model."""
    monkeypatch.setattr("app.services.openai_service.get_persisted_model", lambda: None)
    service = OpenAIService()
    assert service.model == settings.OPENAI_MODEL
