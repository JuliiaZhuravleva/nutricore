"""End-to-end handler test for the photo meal-logging path (TD-004).

Drives ``process_meal_input`` with a photo update — no network, no DB — and
asserts the fixed image path holds together:
  - the image is sent to OpenAI as a base64 **data URL**, never the Telegram
    file URL (which embeds the bot token);
  - the returned JSON string is parsed (fault #3) and the ``portion`` key is
    read without a KeyError (fault #4);
  - the meal draft carries the parsed nutrition, a description, and the photo's
    ``file_id``.

The owner bypasses ``subscription_required`` before any DB access
(``check_subscription`` short-circuits on admin ids), so the handler runs
without a database.
"""

import asyncio
import base64
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.config import settings
from app.services import telegram as tg

_NUTRITION = {
    "foods": ["banana", "oatmeal"],
    "calories": 350,
    "protein": 12,
    "fats": 6,
    "carbs": 60,
    "portion": "1 bowl (300g)",
}
_IMAGE_BYTES = bytes(b"\xff\xd8\xff-fake-jpeg-bytes")


class _FakeMessage:
    def __init__(self, photo):
        self.photo = photo
        self.from_user = SimpleNamespace(id=42, username="owner")
        self.text = None
        self.replies = []

    async def reply_text(self, text, *args, **kwargs):
        self.replies.append(text)


def _make_photo_update():
    message = _FakeMessage(photo=[SimpleNamespace(file_id="PHOTO_FILE_ID")])
    update = SimpleNamespace(
        message=message,
        effective_user=SimpleNamespace(id=42, username="owner"),
    )
    return update, message


def _make_context():
    file = SimpleNamespace(
        download_as_bytearray=AsyncMock(return_value=bytearray(_IMAGE_BYTES)),
        file_path="https://api.telegram.org/file/bot<TOKEN>/photos/x.jpg",
    )
    bot = SimpleNamespace(get_file=AsyncMock(return_value=file))
    return SimpleNamespace(bot=bot, user_data={})


@pytest.fixture(autouse=True)
def _owner_is_admin(monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_IDS", "42")


@pytest.fixture
def analyze_mock(monkeypatch):
    mock = AsyncMock(return_value=json.dumps(_NUTRITION))
    monkeypatch.setattr(tg.telegram_service.openai_service, "analyze_food_image", mock)
    return mock


def test_photo_meal_flow(analyze_mock):
    update, message = _make_photo_update()
    context = _make_context()

    state = asyncio.run(tg.process_meal_input(update, context))

    assert state == tg.CONFIRMING_MEAL

    # The image was sent as a base64 data URL, NOT the token-bearing Telegram URL.
    (sent_url,) = analyze_mock.call_args.args
    assert "api.telegram.org" not in sent_url
    assert (
        sent_url == "data:image/jpeg;base64," + base64.b64encode(_IMAGE_BYTES).decode()
    )

    # The meal draft carries parsed nutrition, a description, and the photo ref.
    meal = context.user_data["current_meal"]
    assert meal["nutrition"] == _NUTRITION
    assert meal["description"] == "banana, oatmeal"
    assert meal["photos"] == ["PHOTO_FILE_ID"]

    # The reply shows the parsed foods and calories (proves parse + portion key).
    assert message.replies, "handler should have replied"
    assert "banana, oatmeal" in message.replies[0]
    assert "350" in message.replies[0]


def test_photo_meal_empty_foods_gets_fallback_description(analyze_mock):
    analyze_mock.return_value = json.dumps({**_NUTRITION, "foods": []})
    update, _ = _make_photo_update()
    context = _make_context()

    state = asyncio.run(tg.process_meal_input(update, context))

    assert state == tg.CONFIRMING_MEAL
    assert context.user_data["current_meal"]["description"] == "Фото приёма пищи"
