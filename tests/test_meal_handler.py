"""What stays of the meal handlers' tests: the parsing of a model's answer, the model
chosen at startup and /reprocess. The live flow of a meal is in test_capture_router.py
and test_capture_read.py.
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.services.product_lookup_service as pls_module
from app.core.config import settings
from app.crud.crud_app_setting import crud_app_setting
from app.db.base import Base
from app.models.ai_call_log import AiCallLog
from app.models.inbound_message import InboundMessage
from app.services import ai_call_log_service as ai_log
from app.services import inbound_message_service as im_service
from app.services import model_selection as ms
from app.services import telegram as tg
from app.services.model_selection import OPENAI_MODEL_SETTING_KEY

_NUTRITION = {
    "foods": ["banana", "oatmeal"],
    "calories": 350,
    "protein": 12,
    "fats": 6,
    "carbs": 60,
    "portion": "1 bowl (300g)",
}
_IMAGE_BYTES = bytes(b"\xff\xd8\xff-fake-jpeg-bytes")


# --- fakes / helpers -------------------------------------------------------


class _FakeMessage:
    def __init__(self, *, photo=None, text=None, caption=None):
        self.photo = photo
        self.text = text
        self.caption = caption
        self.from_user = SimpleNamespace(id=42, username="owner")
        self.replies = []
        self.reply_markups = []

    async def reply_text(self, text, *args, **kwargs):
        self.replies.append(text)
        self.reply_markups.append(kwargs.get("reply_markup"))


def _make_update(*, photo=None, text=None, caption=None):
    message = _FakeMessage(photo=photo, text=text, caption=caption)
    update = SimpleNamespace(
        message=message,
        effective_user=SimpleNamespace(id=42, username="owner"),
    )
    return update, message


def _make_text_update(text):
    return _make_update(text=text)


# --- fixtures --------------------------------------------------------------


@pytest.fixture(autouse=True)
def _owner_is_admin(monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_IDS", "42")


@pytest.fixture
def image_mock(monkeypatch):
    mock = AsyncMock(return_value=json.dumps(_NUTRITION))
    monkeypatch.setattr(tg.telegram_service.openai_service, "analyze_food_image", mock)
    return mock


@pytest.fixture
def entry_mock(monkeypatch):
    mock = AsyncMock(return_value=json.dumps(_NUTRITION))
    monkeypatch.setattr(tg.telegram_service.openai_service, "analyze_food_entry", mock)
    return mock


@pytest.fixture(autouse=True)
def patched_db(monkeypatch):
    """Point all SessionLocals at a fresh in-memory SQLite DB for every test.

    Autouse so the best-effort ai_call_logs recording (and confirm_meal's save)
    write to a throwaway DB instead of attempting a real connection. Tests that
    inspect the DB request `patched_db` by name to get the engine.
    """
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(tg, "SessionLocal", Session)  # confirm_meal's save
    monkeypatch.setattr(ai_log, "SessionLocal", Session)  # ai_call_logs hook
    monkeypatch.setattr(im_service, "SessionLocal", Session)  # inbound_messages hook
    monkeypatch.setattr(pls_module, "SessionLocal", Session)  # pipeline session
    monkeypatch.setattr(ms, "SessionLocal", Session)  # model override persist/load
    yield engine
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture(autouse=True)
def barcode_mock(monkeypatch):
    """Return None (no barcode) for all photo tests.

    Without this, the pipeline's barcode extraction call hits the real OpenAI
    API (→ 401 with the test key) and leaves an unexpected ai_call_logs row.
    The barcode path is tested separately in test_product_lookup_service.py.
    """
    mock = AsyncMock(return_value=None)
    monkeypatch.setattr(
        tg.telegram_service.openai_service, "extract_barcode_from_image", mock
    )
    return mock


@pytest.fixture(autouse=True)
def name_search_mock(monkeypatch):
    """Return None (no name match) for the A8 name-search strategy in all photo
    tests.

    The autouse ``barcode_mock`` above makes the barcode path miss, so the
    pipeline now reaches ``NameOFFStrategy``; without this its ``search_by_name``
    hits the real OFF API (nondeterministic network). The name-search path is
    tested directly in test_product_lookup_service.py / test_open_food_facts_service.py.
    """
    mock = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "app.services.product_lookup_service.OpenFoodFactsService.search_by_name",
        mock,
    )
    return mock


@pytest.fixture(autouse=True)
def label_ocr_mock(monkeypatch):
    """Return a null-basis response for the A10 label-OCR strategy in all photo
    tests so it falls through to vision without making real OpenAI calls.

    The label-OCR path is tested directly in test_product_lookup_service.py.
    The mock goes through analyze_and_log and adds one ``label_ocr`` row to
    ai_call_logs (expected by tests that count logged rows).
    """
    import json

    import app.services.openai_service as ois

    mock = AsyncMock(return_value=json.dumps({"basis": None}))
    monkeypatch.setattr(ois.OpenAIService, "extract_nutrition_label", mock)
    return mock


@pytest.fixture(autouse=True)
def web_search_mock(monkeypatch):
    """Return None (no match) for the A9 NameWebSearchStrategy in all photo tests.

    Mocked at the strategy level so no Responses-API call is made and no extra
    ai_call_logs row is written.  The A9 path is tested directly in
    test_product_lookup_service.py.
    """
    from app.services.product_lookup_service import NameWebSearchStrategy

    mock = AsyncMock(return_value=None)
    monkeypatch.setattr(NameWebSearchStrategy, "resolve", mock)
    return mock


# --- shared helpers --------------------------------------------------------


def test_parse_nutrition_accepts_json_string_and_dict():
    from_string = tg._parse_nutrition(json.dumps(_NUTRITION))
    from_dict = tg._parse_nutrition(dict(_NUTRITION))
    assert from_string == _NUTRITION
    assert from_dict == _NUTRITION


def test_parse_nutrition_normalizes_foods():
    assert tg._parse_nutrition(json.dumps({**_NUTRITION, "foods": "apple"}))[
        "foods"
    ] == ["apple"]
    assert tg._parse_nutrition(json.dumps({**_NUTRITION, "foods": []}))["foods"] == []
    # Missing foods key → empty list, never a KeyError.
    no_foods = {k: v for k, v in _NUTRITION.items() if k != "foods"}
    assert tg._parse_nutrition(json.dumps(no_foods))["foods"] == []


def test_parse_nutrition_coerces_non_string_foods():
    # A swapped-in model may return foods as dicts/numbers — coerce so the later
    # ", ".join(...) can't raise TypeError.
    data = tg._parse_nutrition(
        json.dumps({**_NUTRITION, "foods": [{"name": "rice"}, 42]})
    )
    assert all(isinstance(x, str) for x in data["foods"])
    assert len(data["foods"]) == 2


# --- _parse_nutrition validation -------------------------------------------


def test_parse_nutrition_rejects_malformed():
    with pytest.raises(ValueError):
        tg._parse_nutrition("null")  # JSON null → not an object
    with pytest.raises(ValueError):
        tg._parse_nutrition(json.dumps({"foods": ["x"]}))  # missing calories/macros


# --- model self-heal on deprecation (TD-005) -------------------------------


@pytest.fixture(autouse=True)
def _restore_model():
    """The OpenAIService is a process-wide singleton; a test that switches its
    model must not leak that into later tests."""
    svc = tg.telegram_service.openai_service
    original = svc.model
    original_picks = dict(svc.task_models)
    yield
    svc.model = original
    svc.task_models = original_picks


def test_persisted_task_pick_applies_on_startup(patched_db):
    svc = tg.telegram_service.openai_service
    with tg.SessionLocal() as db:
        crud_app_setting.set(db, "model:vision", "openai:gpt-6-luna")

    tg.apply_persisted_model(svc)

    assert svc.model_for("vision") == "openai:gpt-6-luna"


def test_load_persisted_model_applies_on_startup(patched_db):
    svc = tg.telegram_service.openai_service
    with tg.SessionLocal() as db:
        crud_app_setting.set(db, OPENAI_MODEL_SETTING_KEY, "gpt-persisted")

    tg.apply_persisted_model(svc)

    assert svc.model == "gpt-persisted"


# --- inbound message persistence + reprocess (TD-009) -----------------------


def test_reprocess_reanalyzes_failed_text(patched_db, entry_mock):
    with sessionmaker(bind=patched_db)() as db:
        db.add(
            InboundMessage(
                telegram_id=42,
                kind="text",
                content="oatmeal",
                status="failed",
                error="old model",
            )
        )
        db.commit()

    update, message = _make_text_update("/reprocess")
    context = SimpleNamespace(user_data={}, bot=SimpleNamespace())

    asyncio.run(tg.reprocess(update, context))

    with sessionmaker(bind=patched_db)() as db:
        rows = db.query(InboundMessage).all()
    assert len(rows) == 1
    assert rows[0].status == "analyzed"
    assert rows[0].ai_analysis == _NUTRITION
    assert "успешно 1" in message.replies[-1]


def test_reprocess_records_ai_call_log(patched_db, entry_mock):
    # The replay path goes through analyze_and_log, so a re-analysis is visible in
    # ai_call_logs just like the live flow (not a silent, untraced OpenAI call).
    with sessionmaker(bind=patched_db)() as db:
        db.add(
            InboundMessage(
                telegram_id=42, kind="text", content="oatmeal", status="failed"
            )
        )
        db.commit()

    update, _ = _make_text_update("/reprocess")
    context = SimpleNamespace(user_data={}, bot=SimpleNamespace())

    asyncio.run(tg.reprocess(update, context))

    with sessionmaker(bind=patched_db)() as db:
        logs = db.query(AiCallLog).all()
    assert len(logs) == 1
    assert logs[0].kind == "text"
    assert logs[0].status == "ok"
    assert logs[0].telegram_id == 42


def test_reprocess_empty_queue(patched_db):
    update, message = _make_text_update("/reprocess")
    context = SimpleNamespace(user_data={}, bot=SimpleNamespace())

    asyncio.run(tg.reprocess(update, context))

    assert "Нет сообщений" in message.replies[-1]


def test_reprocess_stops_when_model_still_unavailable(patched_db, monkeypatch):
    with sessionmaker(bind=patched_db)() as db:
        db.add(
            InboundMessage(
                telegram_id=42, kind="text", content="oatmeal", status="failed"
            )
        )
        db.commit()
    monkeypatch.setattr(
        tg.telegram_service.openai_service,
        "analyze_food_entry",
        AsyncMock(side_effect=tg.ModelUnavailableError("gpt-old", Exception("x"))),
    )

    update, message = _make_text_update("/reprocess")
    context = SimpleNamespace(user_data={}, bot=SimpleNamespace())

    asyncio.run(tg.reprocess(update, context))

    assert "всё ещё недоступна" in message.replies[-1]
    with sessionmaker(bind=patched_db)() as db:
        # Left queued (still failed) — not silently dropped.
        assert db.query(InboundMessage).first().status == "failed"


# --- the caption must survive every path that re-runs the analysis ----------
#
# It is persisted to inbound_messages on receipt, so "the caption is saved" was
# true while the model never saw it. These assert the CALL, end to end through
# the real pipeline: the only mock is the vision method itself.

_REAL_CAPTION = (
    "Я доела остатки (1/3 порции примерно) этого батата с Classic Fasting Ranch Sauce"
)


def test_reprocess_replays_the_stored_caption(patched_db, image_mock):
    """/reprocess OVERWRITES ai_analysis — replaying without the caption would
    actively downgrade a record the live flow got right."""
    with sessionmaker(bind=patched_db)() as db:
        db.add(
            InboundMessage(
                telegram_id=42,
                kind="image",
                content=_REAL_CAPTION,
                photo_file_id="PHOTO_FILE_ID",
                status="failed",
                error="old model",
            )
        )
        db.commit()

    update, _ = _make_text_update("/reprocess")
    file = SimpleNamespace(
        download_as_bytearray=AsyncMock(return_value=bytearray(_IMAGE_BYTES))
    )
    context = SimpleNamespace(
        user_data={}, bot=SimpleNamespace(get_file=AsyncMock(return_value=file))
    )

    asyncio.run(tg.reprocess(update, context))

    assert image_mock.call_args.kwargs.get("caption") == _REAL_CAPTION


# --- the loader while the analysis runs --------------------------------------


class _EditableMessage(_FakeMessage):
    """Replies come back as messages that can be edited, like Telegram's."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.screen = []  # [text, markup] per message still on screen

    async def reply_text(self, text, *args, **kwargs):
        await super().reply_text(text, *args, **kwargs)
        shown = [text, kwargs.get("reply_markup")]
        self.screen.append(shown)
        message = self

        class _Sent:
            chat_id, message_id = 42, len(self.screen)

            async def edit_text(self, text, reply_markup=None):
                shown[:] = [text, reply_markup]
                return self

            async def delete(self):
                message.screen.remove(shown)

        return _Sent()


# --- 💸 the cost line (⚙️ Настройки → Стоимость запросов) -----------------------


# gpt-6-luna: 2000 in × $0.10 + 400 out × $0.50 per 1M tokens.
_COST = "💸 Запрос: $0.0004 · 1 вызов ИИ"


# --- a used-up AI balance / a rate limit (ai_errors) ------------------------


@pytest.fixture
def alerts(monkeypatch):
    """The owner alerts that went out; the hourly dedupe starts empty."""
    sent = []

    async def notify(text):
        sent.append(text)
        return True

    monkeypatch.setattr(tg, "notify_admins", notify)
    monkeypatch.setattr(tg, "_credit_alerted", {})
    return sent
