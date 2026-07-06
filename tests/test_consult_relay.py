"""Offline tests for the /consult relay handler.

The bot is a thin relay to the my-health hub: no medical logic, no medical
storage, and no OpenAI call on this path. These tests mock httpx (no network)
and assert the relay contract, the crisis-hint-first rule, and that OpenAI is
never touched.
"""

import asyncio
from types import SimpleNamespace

import httpx
import pytest

from app.core.config import settings
from app.services import consult_service as cs
from app.services import telegram as tg
from app.services.openai_service import OpenAIService


class _FakeMessage:
    """Collects everything the handler sends back, in order."""

    def __init__(self):
        self.replies = []

    async def reply_text(self, text, *args, **kwargs):
        self.replies.append(text)


def _make_update(user_id=42):
    message = _FakeMessage()
    update = SimpleNamespace(
        message=message,
        effective_message=message,
        effective_user=SimpleNamespace(id=user_id),
    )
    return update, message


def _make_context(args):
    return SimpleNamespace(args=args)


class _FakeResponse:
    def __init__(self, json_data, status_code=200, json_exc=None):
        self._json = json_data
        self.status_code = status_code
        self._json_exc = json_exc

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "error",
                request=httpx.Request("POST", "http://test/consult"),
                response=httpx.Response(self.status_code),
            )

    def json(self):
        if self._json_exc is not None:
            raise self._json_exc
        return self._json


def _fake_client_factory(captured, *, response=None, raise_exc=None):
    """Build a drop-in replacement for httpx.AsyncClient.

    ``captured`` records the outbound url/json/headers so tests can assert on
    the request the handler made.
    """

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None, headers=None):
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            if raise_exc is not None:
                raise raise_exc
            return response

    return _FakeAsyncClient


@pytest.fixture(autouse=True)
def as_owner(monkeypatch):
    """Treat the test user (id 42) as the owner so the /consult admin gate passes.

    `settings.admin_ids` is a property that re-parses TELEGRAM_ADMIN_IDS, so setting
    the raw env value is enough. Tests that need a NON-owner override this.
    """
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_IDS", "42")


@pytest.fixture(autouse=True)
def clear_consult_cooldown():
    """Reset the per-user /consult cooldown so tests don't rate-limit each other."""
    tg._last_consult.clear()
    yield
    tg._last_consult.clear()


@pytest.fixture
def enabled_relay(monkeypatch):
    monkeypatch.setattr(
        settings, "MYHEALTH_CONSULT_URL", "http://127.0.0.1:8787/consult"
    )
    monkeypatch.setattr(settings, "CONSULT_TOKEN", "secret-token")


@pytest.fixture(autouse=True)
def openai_spy(monkeypatch):
    """Fail loudly if the consult path ever calls OpenAI."""
    calls = []

    async def _forbidden(self, *args, **kwargs):
        calls.append(args)
        raise AssertionError("OpenAI must not be called on the consult path")

    monkeypatch.setattr(OpenAIService, "analyze_food_entry", _forbidden)
    monkeypatch.setattr(OpenAIService, "analyze_food_image", _forbidden)
    monkeypatch.setattr(OpenAIService, "generate_health_insights", _forbidden)
    return calls


def test_disabled_when_unconfigured(monkeypatch):
    monkeypatch.setattr(settings, "MYHEALTH_CONSULT_URL", None)
    monkeypatch.setattr(settings, "CONSULT_TOKEN", None)
    update, message = _make_update()

    asyncio.run(tg.consult(update, _make_context(["как", "дела"])))

    assert message.replies == ["Консультации сейчас недоступны."]


def test_empty_question_shows_usage(enabled_relay):
    update, message = _make_update()

    asyncio.run(tg.consult(update, _make_context([])))

    assert message.replies == ["Задай вопрос так: /consult <вопрос>"]


def test_happy_path_sends_answer_and_carries_token(enabled_relay, monkeypatch):
    captured = {}
    response = _FakeResponse({"answer": "Drink water.", "crisis_hint": None})
    monkeypatch.setattr(
        cs.httpx, "AsyncClient", _fake_client_factory(captured, response=response)
    )
    update, message = _make_update()

    asyncio.run(tg.consult(update, _make_context(["is", "water", "good?"])))

    # request contract
    assert captured["url"] == "http://127.0.0.1:8787/consult"
    assert captured["json"] == {"question": "is water good?"}
    assert captured["headers"] == {"X-Consult-Token": "secret-token"}
    # answer relayed, no crisis banner
    assert len(message.replies) == 1
    assert message.replies[0].startswith("Drink water.")


def test_crisis_hint_sent_first(enabled_relay, monkeypatch):
    captured = {}
    response = _FakeResponse(
        {
            "answer": "Here is some context.",
            "crisis_hint": "Позвони на 8-800-2000-122",
        }
    )
    monkeypatch.setattr(
        cs.httpx, "AsyncClient", _fake_client_factory(captured, response=response)
    )
    update, message = _make_update()

    asyncio.run(tg.consult(update, _make_context(["мне", "плохо"])))

    assert len(message.replies) == 2
    # crisis hint FIRST, verbatim (prefixed with a warning glyph)
    assert message.replies[0] == "⚠️ Позвони на 8-800-2000-122"
    assert message.replies[1].startswith("Here is some context.")


def test_hub_connection_error_is_friendly(enabled_relay, monkeypatch):
    captured = {}
    monkeypatch.setattr(
        cs.httpx,
        "AsyncClient",
        _fake_client_factory(captured, raise_exc=httpx.ConnectError("hub down")),
    )
    update, message = _make_update()

    asyncio.run(tg.consult(update, _make_context(["вопрос"])))

    assert message.replies == ["Не удалось получить ответ. Попробуй позже."]


def test_hub_403_is_friendly(enabled_relay, monkeypatch):
    captured = {}
    response = _FakeResponse({}, status_code=403)
    monkeypatch.setattr(
        cs.httpx, "AsyncClient", _fake_client_factory(captured, response=response)
    )
    update, message = _make_update()

    asyncio.run(tg.consult(update, _make_context(["вопрос"])))

    assert message.replies == ["Не удалось получить ответ. Попробуй позже."]


def test_malformed_json_is_friendly(enabled_relay, monkeypatch):
    """A 200 with a non-JSON body (resp.json() raises) is handled gracefully."""
    captured = {}
    response = _FakeResponse(None, json_exc=ValueError("not json"))
    monkeypatch.setattr(
        cs.httpx, "AsyncClient", _fake_client_factory(captured, response=response)
    )
    update, message = _make_update()

    asyncio.run(tg.consult(update, _make_context(["вопрос"])))

    assert message.replies == ["Не удалось получить ответ. Попробуй позже."]


def test_non_dict_json_is_friendly(enabled_relay, monkeypatch):
    """A 200 whose JSON is not an object (e.g. a list) is handled gracefully."""
    captured = {}
    response = _FakeResponse(["unexpected"])
    monkeypatch.setattr(
        cs.httpx, "AsyncClient", _fake_client_factory(captured, response=response)
    )
    update, message = _make_update()

    asyncio.run(tg.consult(update, _make_context(["вопрос"])))

    assert message.replies == ["Не удалось получить ответ. Попробуй позже."]


def test_non_admin_denied(enabled_relay, monkeypatch):
    """A non-owner is denied before any outbound call to the hub."""
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_IDS", "999")  # user 42 is not admin
    captured = {}
    response = _FakeResponse({"answer": "should not be sent", "crisis_hint": None})
    monkeypatch.setattr(
        cs.httpx, "AsyncClient", _fake_client_factory(captured, response=response)
    )
    update, message = _make_update()

    asyncio.run(tg.consult(update, _make_context(["какой", "у", "меня", "вес"])))

    assert message.replies == ["⛔️ Эта команда доступна только владельцу."]
    assert captured == {}  # gate short-circuits before the hub is contacted


def test_invalid_url_is_friendly(enabled_relay, monkeypatch):
    """A misconfigured URL (httpx.InvalidURL, not an HTTPError) is handled."""
    captured = {}
    monkeypatch.setattr(
        cs.httpx,
        "AsyncClient",
        _fake_client_factory(captured, raise_exc=httpx.InvalidURL("bad url")),
    )
    update, message = _make_update()

    asyncio.run(tg.consult(update, _make_context(["вопрос"])))

    assert message.replies == ["Не удалось получить ответ. Попробуй позже."]


def test_non_string_answer_is_coerced(enabled_relay, monkeypatch):
    """A non-string answer (e.g. a number) must not crash the reply."""
    captured = {}
    response = _FakeResponse({"answer": 42, "crisis_hint": None})
    monkeypatch.setattr(
        cs.httpx, "AsyncClient", _fake_client_factory(captured, response=response)
    )
    update, message = _make_update()

    asyncio.run(tg.consult(update, _make_context(["вопрос"])))

    assert len(message.replies) == 1
    assert message.replies[0].startswith("42")


def test_consult_rate_limited(enabled_relay, monkeypatch):
    """A rapid second call is rate-limited and does not contact the hub."""
    response = _FakeResponse({"answer": "ok", "crisis_hint": None})

    # First call goes through and contacts the hub.
    captured1 = {}
    monkeypatch.setattr(
        cs.httpx, "AsyncClient", _fake_client_factory(captured1, response=response)
    )
    asyncio.run(tg.consult(*_call("вопрос")))
    assert captured1 != {}

    # Immediate second call (same user) is blocked before the hub.
    captured2 = {}
    monkeypatch.setattr(
        cs.httpx, "AsyncClient", _fake_client_factory(captured2, response=response)
    )
    update, message = _make_update()
    asyncio.run(tg.consult(update, _make_context(["вопрос"])))

    assert message.replies == ["Слишком часто — подожди пару секунд."]
    assert captured2 == {}


def _call(*args):
    """Helper: fresh (update, context) for a consult call."""
    update, _ = _make_update()
    return update, _make_context(list(args))


def test_failed_call_does_not_consume_cooldown(enabled_relay, monkeypatch):
    """A failed hub call must not block an immediate retry."""
    # First call fails at the hub.
    captured1 = {}
    monkeypatch.setattr(
        cs.httpx,
        "AsyncClient",
        _fake_client_factory(captured1, raise_exc=httpx.ConnectError("down")),
    )
    update1, message1 = _make_update()
    asyncio.run(tg.consult(update1, _make_context(["вопрос"])))
    assert message1.replies == ["Не удалось получить ответ. Попробуй позже."]

    # Immediate retry succeeds — must NOT be rate-limited.
    captured2 = {}
    response = _FakeResponse({"answer": "ok", "crisis_hint": None})
    monkeypatch.setattr(
        cs.httpx, "AsyncClient", _fake_client_factory(captured2, response=response)
    )
    update2, message2 = _make_update()
    asyncio.run(tg.consult(update2, _make_context(["вопрос"])))
    assert captured2 != {}  # the hub was contacted on the retry
    assert message2.replies[0].startswith("ok")
