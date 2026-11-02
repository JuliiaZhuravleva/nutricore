"""The read of a meal behind the router: the real pipeline, the audit rows, the picker,
the costs and the out-of-credit path, through the real application.

The model's own calls are the fakes; everything above them is real. No network, no paid
call.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from openai.types import CompletionUsage
from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ExtBot

import app.services.openai_service as ois
import app.services.product_lookup_service as pls_module
from app.core.config import settings
from app.crud.crud_app_setting import crud_app_setting
from app.models import AiCallLog, InboundMessage, Meal
from app.services import capture
from app.services import model_selection as ms
from app.services import telegram as tg
from app.services.meal_draft import num
from app.services.model_selection import OPENAI_MODEL_SETTING_KEY
from app.services.product_lookup_service import (
    NameWebSearchStrategy,
    SavedFoodRAGStrategy,
)
from app.services.usage_meter import record as record_usage
from tests.test_ai_errors import CREDITS_BODY, QUOTA_BODY, RATE_BODY, sdk_error
from tests.test_capture_router import (  # noqa: F401 (fixtures)
    CHAT,
    NOW_TS,
    OWNER,
    PHOTO,
    Session,
    _with_app,
    db,
    owner,
    photo,
    send,
    text,
)

NUTRITION = {
    "foods": ["овсянка"],
    "portion": "300г",
    "calories": 350.0,
    "protein": 10.0,
    "fats": 8.0,
    "carbs": 60.0,
}
COST = "💸 Запрос: $0.0004 · 1 вызов ИИ"


@pytest.fixture(autouse=True)
def _sessions(Session, monkeypatch):
    monkeypatch.setattr(pls_module, "SessionLocal", Session)
    monkeypatch.setattr(ms, "SessionLocal", Session)


@pytest.fixture(autouse=True)
def _restore_models():
    """The service is a singleton: what a pick changes, it changes for the next test."""
    svc = tg.telegram_service.openai_service
    model, tasks = svc.model, dict(svc.task_models)
    yield
    svc.model, svc.task_models = model, tasks


class _NoClient:
    """Stands where the OpenAI client stands: anything that reaches it is a real call."""

    def __init__(self):
        self.reached = []

    def __getattr__(self, name):
        self.reached.append(name)
        raise AssertionError(f"a real API call: {name}")


@pytest.fixture
def model(monkeypatch):
    """The calls under the pipeline; each a miss but the reading itself."""
    svc = tg.telegram_service.openai_service
    client = _NoClient()
    monkeypatch.setattr(svc, "client", client)
    answer = json.dumps(NUTRITION)
    image = AsyncMock(return_value=answer)
    entry = AsyncMock(return_value=answer)
    monkeypatch.setattr(svc, "analyze_food_image", image)
    monkeypatch.setattr(svc, "analyze_food_entry", entry)
    monkeypatch.setattr(svc, "extract_barcode_from_image", AsyncMock(return_value=None))
    monkeypatch.setattr(
        ois.OpenAIService,
        "extract_nutrition_label",
        AsyncMock(return_value=json.dumps({"basis": None})),
    )
    monkeypatch.setattr(
        "app.services.product_lookup_service.OpenFoodFactsService.search_by_name",
        AsyncMock(return_value=None),
    )
    # Both would ask the real client: the first for an embedding, as soon as the
    # person has a row. Each is tested on its own in test_product_lookup_service.py.
    monkeypatch.setattr(SavedFoodRAGStrategy, "resolve", AsyncMock(return_value=None))
    monkeypatch.setattr(NameWebSearchStrategy, "resolve", AsyncMock(return_value=None))
    yield svc
    assert (
        client.reached == []
    )  # swallowed on the way up: the pipeline logs and goes on


@pytest.fixture
def alerts(monkeypatch):
    sent = []

    async def notify(body):
        sent.append(body)
        return True

    monkeypatch.setattr(tg, "notify_admins", notify)
    monkeypatch.setattr(tg, "_credit_alerted", {})
    return sent


def _guest(monkeypatch):
    """The sender is a subscribed guest, not the owner."""
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_IDS", "7")
    monkeypatch.setattr(settings, "ALLOWED_TELEGRAM_IDS", str(OWNER))
    monkeypatch.setattr(tg, "check_subscription", AsyncMock(return_value=True))
    monkeypatch.setattr(tg, "may_receive", lambda _db, _telegram_id: True)


def _meal(db) -> Meal:
    db.expire_all()
    return db.query(Meal).one()


def _inbound(db) -> InboundMessage:
    db.expire_all()
    return db.query(InboundMessage).one()


def test_num_drops_a_trailing_zero():
    assert num(250.0) == "250"
    assert num(22.24) == "22.2"
    assert num(140.0, 0) == "140"


# --- what reaches the model -----------------------------------------------------


def test_a_photo_goes_as_a_data_url_never_the_token_bearing_one(db, owner, model):
    send(photo())
    (sent,) = model.analyze_food_image.call_args.args
    assert sent.startswith("data:image/jpeg;base64,")
    assert "/file/" not in sent


def test_a_photo_without_a_caption_passes_none(db, owner, model):
    send(photo())
    assert model.analyze_food_image.call_args.kwargs.get("caption") is None


def test_a_caption_reaches_the_model(db, owner, model):
    send(photo(caption="без сахара"))
    assert model.analyze_food_image.call_args.kwargs.get("caption") == "без сахара"


# --- ai_call_logs ---------------------------------------------------------------


def test_a_photo_records_each_call_it_made(db, owner, model):
    send(photo())
    db.expire_all()
    rows = db.query(AiCallLog).all()
    # Barcode and image at once; the label OCR falls through on its null basis.
    assert {r.kind for r in rows} == {"barcode_extraction", "image", "label_ocr"}
    (image,) = [r for r in rows if r.kind == "image"]
    assert image.status == "ok"
    assert image.input_ref == "big"
    assert image.parsed_result == NUTRITION
    assert image.model == tg.telegram_service.openai_service.model_for("vision")
    assert image.telegram_id == OWNER
    assert image.latency_ms is not None


def test_a_text_records_its_call(db, owner, model):
    send(text("овсянка на воде"))
    db.expire_all()
    (row,) = db.query(AiCallLog).all()
    assert row.kind == "text"
    assert row.status == "ok"
    assert row.input_ref == "овсянка на воде"


def test_a_failed_call_records_its_error(db, owner, model):
    model.analyze_food_entry.side_effect = RuntimeError("boom")
    send(text("овсянка на воде"))
    db.expire_all()
    (row,) = db.query(AiCallLog).all()
    assert row.status == "error"


# --- inbound_messages -----------------------------------------------------------


def test_a_download_that_fails_still_leaves_a_row_for_reprocess(db, owner, model):
    async def steps(application, wire):
        wire.fail_download = True
        await application.process_update(
            Update.de_json({"update_id": 1, "message": photo()}, application.bot)
        )
        await asyncio.gather(*list(tg._meal_reads))

    asyncio.run(_with_app(steps))
    row = _inbound(db)
    assert row.status == "failed"
    assert row.photo_file_id == "big"
    model.analyze_food_image.assert_not_called()


def test_a_refused_edit_still_marks_the_row_analyzed(db, owner, model, monkeypatch):
    # Otherwise /reprocess would pay for this read again.
    tried = []

    async def refused(*_args, **_kwargs):
        tried.append(1)
        raise TelegramError("edit refused")

    monkeypatch.setattr(ExtBot, "edit_message_text", refused)
    send(photo())
    assert tried
    assert _inbound(db).status == "analyzed"
    assert _meal(db).reading_status == "read"


# --- food groups ----------------------------------------------------------------


def test_a_read_with_tags_stamps_the_meal(db, owner, model):
    answer = {
        **NUTRITION,
        "food_items": [{"name": "овсянка", "groups": ["grain"]}],
    }
    model.analyze_food_image.return_value = json.dumps(answer)
    send(photo())
    assert _meal(db).nutrients["food_groups"] == ["grain"]


@pytest.mark.parametrize("items", [None, []])
def test_a_read_nobody_tagged_stays_unstamped_so_the_retag_finds_it(
    db, owner, model, items
):
    answer = dict(NUTRITION)
    if items is not None:
        answer["food_items"] = items
    model.analyze_food_image.return_value = json.dumps(answer)
    send(photo())
    assert "food_groups" not in _meal(db).nutrients


# --- costs ----------------------------------------------------------------------


def _metered(model, *, cost_on):
    answer = model.analyze_food_entry.return_value

    async def analyze(*_args, **_kwargs):
        record_usage(
            "gpt-6-luna",
            CompletionUsage(
                prompt_tokens=2000, completion_tokens=400, total_tokens=2400
            ),
        )
        return answer

    model.analyze_food_entry.side_effect = analyze
    return cost_on


def test_the_line_says_what_the_read_cost_when_switched_on(db, owner, model):
    _metered(model, cost_on=True)
    owner.settings = {**owner.settings, "show_costs": True}
    db.commit()
    wire = send(text("овсянка на воде"))
    assert COST in wire.seen()[-1]


@pytest.mark.parametrize("setting", [{}, {"show_costs": False}])
def test_no_cost_on_the_line_unless_switched_on(db, owner, model, setting):
    _metered(model, cost_on=False)
    owner.settings = {**owner.settings, **setting}
    db.commit()
    wire = send(text("овсянка на воде"))
    assert wire.seen()[-1].startswith("Записано")
    assert "💸" not in wire.seen()[-1]


# --- out of credit --------------------------------------------------------------


def test_out_of_credit_tells_the_owner_where_to_pay_apart_from_the_line(
    db, owner, model, alerts
):
    model.analyze_food_entry.side_effect = sdk_error(429, QUOTA_BODY)
    wire = send(text("овсянка на воде"))
    assert wire.edits()[-1]["text"] == capture.M7A_FAILED
    (told,) = [p["text"] for p in wire.sent() if "💳" in p["text"]]
    assert "У OpenAI закончился баланс или лимит трат" in told
    assert "platform.openai.com" in told
    assert "/reprocess" in told
    assert alerts == []  # the owner reads it right here


def test_out_of_credit_survives_the_pipeline(db, owner, model, alerts):
    # The pipeline swallows the vision error; it must still be the cause.
    model.analyze_food_image.side_effect = sdk_error(429, QUOTA_BODY)
    wire = send(photo())
    assert any("У OpenAI закончился баланс" in p["text"] for p in wire.sent())
    assert _inbound(db).status == "failed"  # /reprocess picks it up after the top-up
    assert _meal(db).reading_status == "failed"


def test_openrouter_out_of_credit_names_openrouter(db, owner, model, alerts):
    model.analyze_food_entry.side_effect = sdk_error(
        402, CREDITS_BODY, host="openrouter.ai/api"
    )
    wire = send(text("овсянка на воде"))
    (told,) = [p["text"] for p in wire.sent() if "💳" in p["text"]]
    assert "У OpenRouter закончился баланс" in told
    assert "openrouter.ai → Settings → Credits" in told


def test_a_guests_two_meals_alert_the_owner_once_and_the_guest_never_sees_billing(
    db, owner, model, alerts, monkeypatch
):
    _guest(monkeypatch)
    model.analyze_food_entry.side_effect = sdk_error(429, QUOTA_BODY)
    wire = send(text("овсянка на воде"), text("гречка с молоком"))
    assert len(alerts) == 1
    assert f"у пользователя {OWNER}" in alerts[0] and "/reprocess" in alerts[0]
    assert not any("💳" in seen or "platform" in seen for seen in wire.seen())
    assert wire.edits()[-1]["text"] == capture.M7A_FAILED


def test_an_alert_that_failed_is_not_counted_as_sent(db, owner, model, monkeypatch):
    _guest(monkeypatch)
    monkeypatch.setattr(tg, "notify_admins", AsyncMock(return_value=False))
    monkeypatch.setattr(tg, "_credit_alerted", {})
    model.analyze_food_entry.side_effect = sdk_error(429, QUOTA_BODY)
    send(text("овсянка на воде"))
    assert tg._credit_alerted == {}  # the next meal tries to tell the owner again


def test_a_rate_limit_is_the_same_one_sentence_with_no_alert(db, owner, model, alerts):
    model.analyze_food_entry.side_effect = sdk_error(429, RATE_BODY)
    wire = send(text("овсянка на воде"))
    assert wire.edits()[-1]["text"] == capture.M7A_FAILED
    assert not any("💳" in seen for seen in wire.seen())
    assert alerts == []


# --- the picker -----------------------------------------------------------------


def _dead(
    model, monkeypatch, *, method="analyze_food_entry", name="gpt-old", task="text"
):
    """The first call finds the model gone; the second one is read."""
    err = tg.ModelUnavailableError(name, Exception("x"), task=task)
    fake = AsyncMock(side_effect=[err, json.dumps(NUTRITION)])
    monkeypatch.setattr(model, method, fake)
    candidates = AsyncMock(return_value=["openai:gpt-6-luna"])
    monkeypatch.setattr(model, "list_candidates", candidates)
    return fake, candidates


def _update(application, message):
    return Update.de_json(
        {"update_id": message["message_id"], "message": message}, application.bot
    )


async def _settle():
    await asyncio.gather(*list(tg._meal_reads))


def _with_picker(first, *then):
    """Sends ``first``, then each of ``then``; returns the wire."""
    box = {}

    async def steps(application, wire):
        box["wire"] = wire
        box["application"] = application
        for message in (first, *then):
            await application.process_update(_update(application, message))
            await _settle()

    asyncio.run(_with_app(steps))
    return box["wire"], box["application"]


def test_a_dead_model_asks_for_candidates_without_it(db, owner, model, monkeypatch):
    _fake, candidates = _dead(model, monkeypatch)
    wire, _application = _with_picker(text("овсянка на воде"))
    candidates.assert_awaited_once_with("text", exclude="gpt-old")
    assert any("Модель «gpt-old» недоступна" in seen for seen in wire.seen())


def test_a_pick_on_the_default_replaces_the_default(db, owner, model, monkeypatch):
    _dead(model, monkeypatch)
    wire, _application = _with_picker(
        text("овсянка на воде"), text("openai:gpt-6-luna")
    )
    assert model.model == "openai:gpt-6-luna"
    assert "text" not in model.task_models
    assert crud_app_setting.get(db, OPENAI_MODEL_SETTING_KEY) == "openai:gpt-6-luna"
    assert crud_app_setting.get(db, "model:text") is None
    assert _meal(db).reading_status == "read"
    assert wire.seen()[-1].startswith("Записано")


def test_a_pick_on_a_task_of_its_own_replaces_only_that(db, owner, model, monkeypatch):
    default = model.model
    model.set_model("openrouter:qwen/qwen3.8-flash", task="text")
    _dead(model, monkeypatch, name="openrouter:qwen/qwen3.8-flash")
    _with_picker(text("овсянка на воде"), text("openai:gpt-6-luna"))
    assert model.model == default
    assert model.model_for("text") == "openai:gpt-6-luna"
    assert crud_app_setting.get(db, "model:text") == "openai:gpt-6-luna"
    assert crud_app_setting.get(db, OPENAI_MODEL_SETTING_KEY) is None


def test_the_read_after_a_pick_keeps_the_caption(db, owner, model, monkeypatch):
    fake, _candidates = _dead(
        model, monkeypatch, method="analyze_food_image", task="vision"
    )
    _with_picker(photo(caption="без сахара"), text("openai:gpt-6-luna"))
    assert fake.await_count == 2
    assert fake.call_args.kwargs.get("caption") == "без сахара"


def test_other_text_with_the_picker_open_is_a_message_and_the_picker_stays(
    db, owner, model, monkeypatch
):
    _dead(model, monkeypatch)
    _wire, application = _with_picker(text("овсянка на воде"), text("🍌"))
    assert application.user_data[OWNER]["model_choices"] == ["openai:gpt-6-luna"]
    assert len(db.query(Meal).all()) == 2


def test_cancel_closes_the_picker_and_changes_nothing(db, owner, model, monkeypatch):
    before = model.model
    _dead(model, monkeypatch)
    wire, application = _with_picker(text("овсянка на воде"), text("Отмена"))
    assert wire.seen()[-1] == "Отменено."
    assert "model_choices" not in application.user_data[OWNER]
    assert model.model == before
    db.expire_all()
    assert db.query(Meal).count() == 1  # the word is no meal


def test_a_guest_gets_no_picker(db, owner, model, monkeypatch):
    _guest(monkeypatch)
    fake, candidates = _dead(model, monkeypatch)
    wire, application = _with_picker(text("овсянка на воде"))
    # Known positive: the same dead model did reach this guest's read.
    assert fake.await_count == 1
    candidates.assert_not_called()
    assert "model_choices" not in application.user_data[OWNER]
    assert not any("недоступна" in seen for seen in wire.seen())


def test_a_guest_typing_a_models_name_writes_food_and_changes_no_model(
    db, owner, model, monkeypatch
):
    _guest(monkeypatch)
    before = model.model
    wire, _application = _with_picker(text("openai:gpt-6-luna"))
    assert model.model == before
    assert model.analyze_food_entry.await_count == 1
    assert wire.seen()[-1].startswith("Записано")


# --- a button of a draft from before the drafts went ---------------------------


def test_a_tap_on_an_old_draft_only_says_it_is_stale(db, owner):
    box = {}

    async def steps(application, wire):
        box["wire"] = wire
        await application.process_update(
            Update.de_json(
                {
                    "update_id": 1,
                    "callback_query": {
                        "id": "1",
                        "from": {"id": OWNER, "is_bot": False, "first_name": "o"},
                        "chat_instance": "c",
                        "data": "meal:abc123:s",
                        "message": {
                            "message_id": 77,
                            "date": NOW_TS,
                            "chat": {"id": CHAT, "type": "private"},
                            "text": "старый черновик",
                        },
                    },
                },
                application.bot,
            )
        )

    asyncio.run(_with_app(steps))
    calls = dict(box["wire"].calls)
    assert "неактуален" in calls["answerCallbackQuery"]["text"]
    assert "editMessageReplyMarkup" in calls
    assert db.query(Meal).count() == 0
