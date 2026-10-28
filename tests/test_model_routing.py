"""OpenAIService routing by task and route (ADR-0004).

The OpenRouter error tests run the real SDK against an httpx MockTransport that
answers with the bodies OpenRouter returned live on 2026-10-27, so the
not-found matcher is checked against the SDK's own error text, not a fake one.
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import openai
import pytest
from openai import AsyncOpenAI

import app.services.openai_service as ois
from app.core.config import settings
from app.services.model_registry import REASONING_TOKEN_FLOOR
from app.services.openai_service import (
    ModelConfigError,
    ModelUnavailableError,
    OpenAIService,
)
from app.services.usage_meter import metering as metering

_FAKE = SimpleNamespace(
    choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))]
)

# Bodies OpenRouter returned live (2026-10-27).
_OR_UNKNOWN_MODEL = (
    400,
    {"error": {"message": "openai/gpt-0-nope is not a valid model ID", "code": 400}},
)
_OR_NO_ENDPOINTS = (
    404,
    {
        "error": {
            "message": "No endpoints found that can handle the requested parameters.",
            "code": 404,
        }
    },
)
_OR_OK = (
    200,
    {
        "id": "gen-1",
        "object": "chat.completion",
        "created": 0,
        "model": "google/gemini-3.8-flash",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": '{"ok": true}'},
            }
        ],
    },
)


@pytest.fixture
def alerts(monkeypatch):
    """Spy on the admin alert at its call site in openai_service."""
    calls = []

    async def spy(task, ref, err):
        calls.append((task, ref, err))
        return True

    monkeypatch.setattr(ois, "report_model_failure", spy)
    return calls


@pytest.fixture
def openrouter_key(monkeypatch):
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "test-openrouter-key")


def _openrouter_transport(service, status_body, seen=None):
    status, body = status_body

    def handler(request):
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, json=body)

    service._openrouter = AsyncOpenAI(
        api_key="test-openrouter-key",
        base_url=settings.OPENROUTER_BASE_URL,
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


# --- which model a task runs on ---------------------------------------------


def test_task_pick_beats_setting_beats_default(monkeypatch):
    service = OpenAIService()
    assert service.model_for("vision") == settings.OPENAI_MODEL

    monkeypatch.setattr(settings, "MODEL_VISION", "openai:gpt-6-luna")
    assert service.model_for("vision") == "openai:gpt-6-luna"
    assert service.model_for("text") == settings.OPENAI_MODEL

    service.set_model("openrouter:google/gemini-3.8-flash", task="vision")
    assert service.model_for("vision") == "openrouter:google/gemini-3.8-flash"
    assert service.model == settings.OPENAI_MODEL


def test_persisted_task_picks_load_on_construction(monkeypatch):
    monkeypatch.setattr(
        ois, "get_persisted_task_models", lambda: {"barcode": "openai:gpt-6-luna"}
    )
    assert OpenAIService().model_for("barcode") == "openai:gpt-6-luna"


def test_each_call_site_names_its_task(monkeypatch):
    service = OpenAIService()
    service.client.chat.completions.create = AsyncMock(return_value=_FAKE)
    seen = []
    real = service._create

    async def spy(**kw):
        seen.append(kw["task"])
        return await real(**kw)

    service._create = spy
    url = "https://example.test/x.jpg"
    asyncio.run(service.analyze_food_entry("apple"))
    asyncio.run(service.analyze_food_image(url))
    asyncio.run(service.extract_barcode_from_image(url))
    asyncio.run(service.extract_nutrition_label(url))
    asyncio.run(service.generate_health_insights({}))
    assert seen == ["text", "vision", "barcode", "label_ocr", "text"]


# --- parameters reach the client --------------------------------------------


def test_reasoning_model_gets_its_parameters_at_the_client(monkeypatch):
    """gpt-6-luna rejects max_tokens and a non-default temperature with a 400
    (live, 2026-10-27): neither may reach the client."""
    monkeypatch.setattr(settings, "MODEL_TEXT", "openai:gpt-6-luna")
    service = OpenAIService()
    service.client.chat.completions.create = AsyncMock(return_value=_FAKE)

    asyncio.run(service.analyze_food_entry("apple"))

    kwargs = service.client.chat.completions.create.call_args.kwargs
    assert kwargs["model"] == "gpt-6-luna"
    assert "max_tokens" not in kwargs
    assert "temperature" not in kwargs
    assert kwargs["max_completion_tokens"] == max(
        settings.OPENAI_MAX_TOKENS, REASONING_TOKEN_FLOOR
    )
    assert kwargs["response_format"] == {"type": "json_object"}


def test_openrouter_ref_goes_to_the_openrouter_client_only(monkeypatch, openrouter_key):
    monkeypatch.setattr(settings, "MODEL_VISION", "openrouter:google/gemini-3.8-flash")
    service = OpenAIService()
    service.client.chat.completions.create = AsyncMock()
    requests = []
    _openrouter_transport(service, _OR_OK, requests)

    asyncio.run(service.analyze_food_image("https://example.test/x.jpg"))

    service.client.chat.completions.create.assert_not_called()
    assert len(requests) == 1
    assert str(requests[0].url) == "https://openrouter.ai/api/v1/chat/completions"
    body = json.loads(requests[0].content)
    assert body["model"] == "google/gemini-3.8-flash"
    assert body["provider"] == {"require_parameters": True, "data_collection": "deny"}


def test_openrouter_client_is_built_from_settings(openrouter_key):
    service = OpenAIService()
    client = service._client_for(ois.parse_model_ref("openrouter:qwen/qwen3.8-flash"))
    assert str(client.base_url).rstrip("/") == settings.OPENROUTER_BASE_URL
    assert client.api_key == "test-openrouter-key"
    assert client is not service.client
    # Built once, reused.
    assert service._client_for(ois.parse_model_ref("openrouter:x/y")) is client


# --- failures ----------------------------------------------------------------


def test_openrouter_ref_without_key_is_a_config_error_and_alerts(monkeypatch, alerts):
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", None)
    monkeypatch.setattr(settings, "MODEL_BARCODE", "openrouter:google/gemini-3.8-flash")
    service = OpenAIService()
    service.client.chat.completions.create = AsyncMock()

    with pytest.raises(ModelConfigError) as excinfo:
        asyncio.run(service.extract_barcode_from_image("https://example.test/x.jpg"))

    assert excinfo.value.task == "barcode"
    assert excinfo.value.model == "openrouter:google/gemini-3.8-flash"
    assert isinstance(excinfo.value, ModelUnavailableError)
    service.client.chat.completions.create.assert_not_called()
    assert [(t, r) for t, r, _ in alerts] == [
        ("barcode", "openrouter:google/gemini-3.8-flash")
    ]


@pytest.mark.parametrize("answer", [_OR_UNKNOWN_MODEL, _OR_NO_ENDPOINTS])
def test_openrouter_model_gone_is_unavailable_and_alerts(
    monkeypatch, openrouter_key, alerts, answer
):
    monkeypatch.setattr(settings, "MODEL_TEXT", "openrouter:openai/gpt-0-nope")
    service = OpenAIService()
    _openrouter_transport(service, answer)

    with pytest.raises(ModelUnavailableError) as excinfo:
        asyncio.run(service.analyze_food_entry("apple"))

    assert excinfo.value.task == "text"
    assert excinfo.value.route == "openrouter"
    assert len(alerts) == 1
    assert alerts[0][:2] == ("text", "openrouter:openai/gpt-0-nope")


def test_openai_model_gone_alerts_with_task_and_ref(alerts):
    service = OpenAIService()
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    service.client.chat.completions.create = AsyncMock(
        side_effect=openai.NotFoundError(
            "The model `gpt-x` does not exist",
            response=httpx.Response(404, request=request),
            body=None,
        )
    )

    with pytest.raises(ModelUnavailableError) as excinfo:
        asyncio.run(service.extract_nutrition_label("https://example.test/x.jpg"))

    assert excinfo.value.task == "label_ocr"
    assert [(t, r) for t, r, _ in alerts] == [("label_ocr", settings.OPENAI_MODEL)]


def test_unrelated_bad_request_neither_maps_nor_alerts(alerts):
    """No finding: a parameter 400 is a bug to surface, not a dead model."""
    service = OpenAIService()
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    service.client.chat.completions.create = AsyncMock(
        side_effect=openai.BadRequestError(
            "Unsupported parameter: 'max_tokens' is not supported with this model.",
            response=httpx.Response(400, request=request),
            body=None,
        )
    )

    with pytest.raises(openai.BadRequestError):
        asyncio.run(service.analyze_food_entry("apple"))
    assert alerts == []


# --- web search ----------------------------------------------------------------


def test_web_search_through_openrouter_uses_its_server_tool(
    monkeypatch, openrouter_key
):
    monkeypatch.setattr(
        settings, "OPENAI_WEB_SEARCH_MODEL", "openrouter:openai/gpt-6-luna"
    )
    service = OpenAIService()
    service.web_search_client.responses.create = AsyncMock()
    requests = []
    _openrouter_transport(service, _OR_OK, requests)

    out = asyncio.run(service.web_search_nutrition(["Alpro soya"]))

    assert out == '{"ok": true}'
    service.web_search_client.responses.create.assert_not_called()
    body = json.loads(requests[0].content)
    assert body["model"] == "openai/gpt-6-luna"
    assert body["tools"] == [{"type": "openrouter:web_search"}]
    assert body["provider"]["data_collection"] == "deny"
    assert "Alpro soya" in body["messages"][0]["content"]


def test_web_search_model_gone_alerts_before_the_caller_swallows_it(
    monkeypatch, alerts
):
    monkeypatch.setattr(settings, "OPENAI_WEB_SEARCH_MODEL", "gpt-x")
    service = OpenAIService()
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    service.web_search_client.responses.create = AsyncMock(
        side_effect=openai.NotFoundError(
            "The model `gpt-x` does not exist",
            response=httpx.Response(404, request=request),
            body=None,
        )
    )

    with pytest.raises(ModelUnavailableError) as excinfo:
        asyncio.run(service.web_search_nutrition(["x"]))

    assert excinfo.value.task == "web_search"
    assert [(t, r) for t, r, _ in alerts] == [("web_search", "gpt-x")]


def test_web_search_on_the_openai_route_sends_the_bare_model_id(monkeypatch):
    monkeypatch.setattr(settings, "OPENAI_WEB_SEARCH_MODEL", "openai:gpt-6-luna")
    service = OpenAIService()
    service.web_search_client.responses.create = AsyncMock(
        return_value=SimpleNamespace(output_text="{}")
    )

    asyncio.run(service.web_search_nutrition(["x"]))

    kwargs = service.web_search_client.responses.create.call_args.kwargs
    assert kwargs["model"] == "gpt-6-luna"
    assert kwargs["tools"] == [{"type": "web_search"}]


@pytest.mark.parametrize(
    "setting", ["OPENAI_WEB_SEARCH_MODEL", "OPENAI_MODEL"], ids=["pinned", "default"]
)
@pytest.mark.parametrize(
    "gemini",
    ["openrouter:google/gemini-3.8-flash", "openrouter:google/gemini-3.5-flash-lite"],
)
def test_web_search_never_goes_to_gemini(monkeypatch, openrouter_key, setting, gemini):
    # $0.70 a search on gemini-3.8-flash (2026-10-27): it goes to gpt-6-luna instead.
    monkeypatch.setattr(settings, "OPENAI_WEB_SEARCH_MODEL", None)
    monkeypatch.setattr(settings, setting, gemini)
    service = OpenAIService()
    service.web_search_client.responses.create = AsyncMock(
        return_value=SimpleNamespace(output_text="{}")
    )
    openrouter_requests = []
    _openrouter_transport(service, _OR_OK, openrouter_requests)

    asyncio.run(service.web_search_nutrition(["x"]))

    assert service.web_search_model == "gpt-6-luna"
    kwargs = service.web_search_client.responses.create.call_args.kwargs
    assert kwargs["model"] == "gpt-6-luna"
    assert openrouter_requests == []


def test_web_search_stays_on_a_model_that_may_search(monkeypatch):
    # No finding: Claude and an unknown id keep the search (only Gemini is barred).
    for ref in ("openrouter:anthropic/claude-haiku-5.5", "gpt-brand-new"):
        monkeypatch.setattr(settings, "OPENAI_WEB_SEARCH_MODEL", ref)
        assert OpenAIService().web_search_model == ref


# --- what the admin can switch to ---------------------------------------------


def _listing(*ids):
    return AsyncMock(
        return_value=SimpleNamespace(data=[SimpleNamespace(id=i) for i in ids])
    )


def test_candidates_without_openrouter_key_are_openai_only_and_still_listed(
    monkeypatch,
):
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", None)
    service = OpenAIService()
    service.client.models.list = _listing("gpt-4o-mini", "gpt-6-luna", "gpt-6.1-terra")

    got = asyncio.run(service.list_candidates("vision", exclude="gpt-4o-mini"))

    # gpt-4o-mini just failed; gpt-5.6-luna is no longer listed.
    assert got == ["openai:gpt-6-luna", "openai:gpt-6.1-terra"]


def test_candidates_include_other_companies_with_a_key(monkeypatch, openrouter_key):
    service = OpenAIService()
    service.client.models.list = _listing("gpt-6-luna")
    _openrouter_transport(
        service,
        (
            200,
            {
                "data": [
                    {"id": "google/gemini-3.8-flash"},
                    {"id": "anthropic/claude-haiku-5.5"},
                ]
            },
        ),
    )

    got = asyncio.run(service.list_candidates("text"))

    assert got == [
        "openai:gpt-6-luna",
        "openrouter:anthropic/claude-haiku-5.5",
        "openrouter:google/gemini-3.8-flash",
    ]


def test_an_unfetchable_list_filters_nothing(monkeypatch):
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", None)
    service = OpenAIService()
    service.client.models.list = AsyncMock(side_effect=Exception("network down"))

    got = asyncio.run(service.list_candidates("text"))

    assert got == [r for r in ois.REGISTRY if r.startswith("openai:")]


def test_photo_tasks_get_only_vision_models(monkeypatch):
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", None)
    monkeypatch.setattr(
        ois,
        "REGISTRY",
        {
            "openai:text-only": ois.REGISTRY["openai:gpt-6-luna"].__class__(
                vision=False
            ),
            "openai:sees": ois.REGISTRY["openai:gpt-6-luna"],
        },
    )
    service = OpenAIService()
    service.client.models.list = AsyncMock(side_effect=Exception("down"))

    assert asyncio.run(service.list_candidates("barcode")) == ["openai:sees"]
    assert asyncio.run(service.list_candidates("text")) == [
        "openai:text-only",
        "openai:sees",
    ]


def test_nothing_left_falls_back_to_openais_replacement(monkeypatch):
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", None)
    service = OpenAIService()
    service.client.models.list = _listing("gpt-4o")

    assert asyncio.run(service.list_candidates("text")) == ["openai:gpt-6-luna"]


def test_runs_on_default_follows_where_the_model_is_configured(monkeypatch):
    service = OpenAIService()
    assert service.runs_on_default("vision")
    monkeypatch.setattr(settings, "MODEL_VISION", "openai:gpt-6-luna")
    assert not service.runs_on_default("vision")
    assert service.runs_on_default("text")
    service.set_model("openai:gpt-6-luna", task="text")
    assert not service.runs_on_default("text")


# --- the 💸 meter ----------------------------------------------------------------

# _OR_OK with the usage OpenRouter adds to a reply; "cost" (USD) is ASSUMED from
# its docs, not yet seen live: without it the call shows as «цена неизвестна».
_OR_OK_WITH_COST = (
    200,
    {
        **_OR_OK[1],
        "usage": {
            "prompt_tokens": 1200,
            "completion_tokens": 80,
            "total_tokens": 1280,
            "cost": 0.00042,
        },
    },
)


@pytest.mark.parametrize("task", ["vision", "web_search"])
def test_an_openrouter_call_is_metered_at_its_reported_cost(
    monkeypatch, openrouter_key, task
):
    # Gemini has no web search here (the call would fall back to OpenAI).
    ref = "openrouter:openai/gpt-6-luna"
    monkeypatch.setattr(settings, "MODEL_VISION", ref)
    monkeypatch.setattr(settings, "OPENAI_WEB_SEARCH_MODEL", ref)
    service = OpenAIService()
    service.web_search_client.responses.create = AsyncMock()  # never the network
    _openrouter_transport(service, _OR_OK_WITH_COST)

    with metering() as meter:
        if task == "vision":
            asyncio.run(service.analyze_food_image("https://example.test/x.jpg"))
        else:
            asyncio.run(service.web_search_nutrition(["Alpro soya"]))

    assert meter.calls == 1
    assert meter.usd == pytest.approx(0.00042)
    assert meter.unpriced == []
