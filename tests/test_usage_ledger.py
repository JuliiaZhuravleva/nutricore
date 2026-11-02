"""The cost ledger: every paid route, through its real caller, books the right person.

No call leaves the machine: the fake stands where the SDK's client stands, under the real
``OpenAIService`` methods, and answers in the SDK's own shape. Nothing here reads a key.
"""

from __future__ import annotations

import ast
import asyncio
import json
import logging
import math
import re
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from telegram import Update
from telegram.ext import TypeHandler

from app.api.v1 import ai
from app.core.config import settings
from app.crud.crud_subscription import crud_subscription
from app.crud.crud_usage_ledger import crud_usage_ledger
from app.models import Meal, PersonalFood, UsageLedger, User
from app.models.usage_ledger import NO_PERSON
from app.services import action_journal, food_groups_retag, portion_suggestions
from app.services import product_lookup_service as pls_module
from app.services import telegram as tg
from app.services import usage_ledger, usage_meter
from app.services.attribution import attribute_to, current_person
from app.services.openai_service import OpenAIService
from app.services.product_lookup_service import (
    ImageSignals,
    LabelOCRStrategy,
    NameWebSearchStrategy,
)
from celery_app.tasks.personal_food import embed_and_save_personal_food
from tests.test_capture_router import (  # noqa: F401 (fixtures)
    NOW_TS,
    NUTRITION,
    OWNER,
    PHOTO,
    Session,
    Wire,
    _message,
    db,
    owner,
    send,
)

ROOT = Path(__file__).resolve().parent.parent
SASHA = 3003
ZHENYA = 4004
OGG = b"OggS\x00\x02" + b"\x00" * 40
USD = Decimal("0.0000000001")

# gpt-6-luna at (0.10, 0.01, 0.50) per 1M: (1000 * 0.10 + 100 * 0.50) / 1M.
CHAT_USD = Decimal("0.00015")
# text-embedding-3-small at 0.02 per 1M.
EMBED_USD = Decimal("0.00002")
# gpt-4o-mini-transcribe at (1.25, 1.25, 5.00) per 1M.
HEAR_USD = Decimal("0.00175")
# The same tokens and one search, at $10 per 1000.
SEARCH_USD = CHAT_USD + Decimal("0.01")


class Fake:
    """The SDK's client: every endpoint a paid call can reach, and what each one took."""

    async def close(self):
        pass

    def __init__(self):
        self.reached: list = []
        self.expected = Decimal(0)
        self.down = False
        self.chat_usd = CHAT_USD
        self.usage = SimpleNamespace(prompt_tokens=1000, completion_tokens=100)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._chat))
        self.responses = SimpleNamespace(create=self._search)
        self.embeddings = SimpleNamespace(create=self._embed)
        self.audio = SimpleNamespace(transcriptions=SimpleNamespace(create=self._hear))

    def with_options(self, **_):
        return self

    def _took(self, endpoint: str, usd: Decimal) -> None:
        if self.down:
            raise RuntimeError("the model is down in this test")
        self.reached.append(endpoint)
        self.expected += usd

    async def _chat(self, **_):
        self._took("chat", self.chat_usd)
        message = SimpleNamespace(content=json.dumps(NUTRITION))
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message, finish_reason="stop")],
            usage=self.usage,
        )

    async def _search(self, **_):
        self._took("search", SEARCH_USD)
        return SimpleNamespace(
            output=[SimpleNamespace(type="web_search_call")],
            output_text=json.dumps({"identification": None}),
            usage=SimpleNamespace(input_tokens=1000, output_tokens=100),
        )

    async def _embed(self, *, input, **_):
        many = input if isinstance(input, list) else [input]
        self._took("embed", EMBED_USD)
        return SimpleNamespace(
            data=[
                SimpleNamespace(index=i, embedding=[0.0] * 3) for i in range(len(many))
            ],
            usage=SimpleNamespace(prompt_tokens=1000, total_tokens=1000),
        )

    async def _hear(self, **_):
        self._took("hear", HEAR_USD)
        return SimpleNamespace(
            text="овсянка",
            usage=SimpleNamespace(input_tokens=1000, output_tokens=100),
        )


@pytest.fixture
def fake(monkeypatch, Session):  # noqa: F811
    """Under the bot's one service, and one database for all that writes."""
    svc = tg.telegram_service.openai_service
    client = Fake()
    monkeypatch.setattr(svc, "client", client)
    monkeypatch.setattr(svc, "web_search_client", client)
    monkeypatch.setattr(svc, "transcribe_client", client)
    monkeypatch.setattr(svc, "_openrouter", client)
    monkeypatch.setattr(svc, "model", "openai:gpt-6-luna")
    monkeypatch.setattr(svc, "task_models", {})
    monkeypatch.setattr(settings, "OPENAI_WEB_SEARCH_MODEL", "openai:gpt-6-luna")
    monkeypatch.setattr(settings, "OPENAI_TRANSCRIBE_MODEL", "gpt-4o-mini-transcribe")
    monkeypatch.setattr(settings, "OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "not-a-key")
    monkeypatch.setattr(settings, "BOT_ACCESS_MODE", "whitelist")
    monkeypatch.setattr(settings, "ALLOWED_TELEGRAM_IDS", f"{SASHA},{ZHENYA}")
    monkeypatch.setattr(settings, "WHISPER_URL", "")
    for name in tuple(vars(settings)):
        if name.startswith("MODEL_"):
            monkeypatch.setattr(settings, name, None)
    monkeypatch.setattr(pls_module, "SessionLocal", Session)
    monkeypatch.setattr(usage_ledger, "SessionLocal", Session)
    monkeypatch.setattr(action_journal, "SessionLocal", Session)
    monkeypatch.setattr(Wire, "file_bytes", OGG)
    monkeypatch.setattr(tg._card_delivery, "_screens", {})
    monkeypatch.setattr(
        "app.services.product_lookup_service.OpenFoodFactsService.search_by_name",
        AsyncMock(return_value=None),
    )
    return client


@pytest.fixture
def sasha(db):  # noqa: F811
    user = User(telegram_id=SASHA, settings={"timezone": "Asia/Tbilisi"})
    db.add(user)
    db.commit()
    crud_subscription.create_subscription(
        db, telegram_id=SASHA, granted_by_telegram_id=OWNER
    )
    return user


def _ledger(db) -> dict:  # noqa: F811
    db.expire_all()
    return {row.telegram_id: row for row in db.scalars(select(UsageLedger))}


def _from(person: int, **fields) -> dict:
    return _message(
        chat={"id": person, "type": "private"},
        **{"from": {"id": person, "is_bot": False, "first_name": "x", "username": "x"}},
        **fields,
    )


def _assert_booked(db, fake, person: int) -> None:  # noqa: F811
    """All of it to one person, none to anyone else, call for call and cent for cent."""
    rows = _ledger(db)
    assert fake.reached, "the route never reached a paid endpoint"
    assert set(rows) == {person}
    row = rows[person]
    assert row.calls == len(fake.reached)
    assert row.unpriced_calls == 0
    assert Decimal(str(row.cost_usd)).quantize(USD) == fake.expected.quantize(USD)


def _text_read(db, fake, svc):  # noqa: F811
    send(_from(SASHA, text="овсянка"))
    return SASHA, {"chat"}


def _photo_read(db, fake, svc):  # noqa: F811
    # The whole pipeline, up to where these answers stop it: barcode, vision and the
    # saved-food lookup. Label OCR and web search have their own routes below.
    send(_from(SASHA, photo=PHOTO))
    return SASHA, {"chat", "embed"}


def _voice_read(db, fake, svc):  # noqa: F811
    voice = {"file_id": "v", "file_unique_id": "vu", "duration": 3, "file_size": 1000}
    send(_from(SASHA, voice=voice))
    return SASHA, {"hear", "chat"}


def _signals() -> ImageSignals:
    return ImageSignals(
        image_data_url="data:",
        barcode=None,
        vision_result={"foods": ["сок"]},
        portion_grams=None,
        telegram_id=SASHA,
    )


def _label_ocr(db, fake, svc):  # noqa: F811
    with attribute_to(SASHA):
        asyncio.run(LabelOCRStrategy().resolve(_signals(), db))
    return SASHA, {"chat"}


def _web_search_on_openai(db, fake, svc):  # noqa: F811
    with attribute_to(SASHA):
        asyncio.run(NameWebSearchStrategy().resolve(_signals(), db))
    return SASHA, {"search"}


def _web_search_on_openrouter(db, fake, svc):  # noqa: F811
    settings.OPENAI_WEB_SEARCH_MODEL = "openrouter:vendor/gpt-6-luna"
    # OpenRouter reports its own price, and that is taken as is.
    fake.usage = SimpleNamespace(cost=0.003, prompt_tokens=1, completion_tokens=1)
    fake.chat_usd = Decimal("0.003")
    with attribute_to(SASHA):
        asyncio.run(NameWebSearchStrategy().resolve(_signals(), db))
    return SASHA, {"chat"}


def _retag_script(db, fake, svc):  # noqa: F811
    person = db.scalar(select(User).where(User.telegram_id == SASHA))
    db.add(Meal(user_id=person.id, description="суп", nutrients={"foods": ["суп"]}))
    db.commit()
    asyncio.run(food_groups_retag.run(db, svc, apply=False, limit_usd=1.0))
    return SASHA, {"chat"}


def _portion_ask(db, fake, svc):  # noqa: F811
    person = db.scalar(select(User).where(User.telegram_id == SASHA))
    dish = _plain_dish(db, person)
    with patch("app.services.meal_items.wants_suggestion", return_value=True):
        asyncio.run(portion_suggestions.ask(_session_of(db), svc, dish.id))
    return SASHA, {"chat"}


def _portion_script(db, fake, svc):  # noqa: F811
    person = db.scalar(select(User).where(User.telegram_id == SASHA))
    dish = _plain_dish(db, person)
    with patch.object(portion_suggestions, "pending", return_value=[dish]):
        asyncio.run(portion_suggestions.run(_session_of(db), svc, apply=True))
    return SASHA, {"chat"}


def _celery_embed(db, fake, svc):  # noqa: F811
    person = db.scalar(select(User).where(User.telegram_id == SASHA))
    factory = _session_of(db)
    with (
        patch("celery_app.tasks.personal_food.SessionLocal", factory),
        patch("celery_app.tasks.personal_food.OpenAIService", return_value=svc),
        patch("celery_app.tasks.personal_food._ask_portion", return_value=None),
    ):
        embed_and_save_personal_food.run(
            user_id=person.id,
            canonical_name="сок",
            per_100g_calories=45.0,
            per_100g_proteins=1.0,
            per_100g_fats=0.0,
            per_100g_carbs=10.0,
        )
    return SASHA, {"embed"}


def _typed_dish_fill(db, fake, svc):  # noqa: F811
    fill = SimpleNamespace(name="суп", item_id=0, rev=0, event_id=0)
    asyncio.run(tg._fill_typed_dish(fill, SASHA))
    return SASHA, {"chat"}


def _rest_analyze(db, fake, svc):  # noqa: F811
    # Nobody is named on this door, so nobody is charged.
    asyncio.run(ai.analyze_food("суп", service=svc))
    return NO_PERSON, {"chat"}


def _health_insights(db, fake, svc):  # noqa: F811
    with attribute_to(SASHA):
        asyncio.run(svc.generate_health_insights({"calories": 1}))
    return SASHA, {"chat"}


def _embed_texts(db, fake, svc):  # noqa: F811
    asyncio.run(svc.embed_texts(["суп", "чай"]))
    return NO_PERSON, {"embed"}


def _plain_dish(db, person) -> PersonalFood:  # noqa: F811
    dish = PersonalFood(user_id=person.id, canonical_name="сок")
    db.add(dish)
    db.commit()
    return dish


def _session_of(db):  # noqa: F811
    """A session on the same database that a ``with`` does not close for good."""
    from sqlalchemy.orm import sessionmaker

    return sessionmaker(bind=db.get_bind())


ROUTES = [
    _text_read,
    _photo_read,
    _voice_read,
    _label_ocr,
    _web_search_on_openai,
    _web_search_on_openrouter,
    _retag_script,
    _portion_ask,
    _portion_script,
    _celery_embed,
    _typed_dish_fill,
    _rest_analyze,
    _health_insights,
    _embed_texts,
]


@pytest.mark.parametrize("route", ROUTES, ids=lambda r: r.__name__.lstrip("_"))
def test_every_paid_route_books_its_person(
    route, fake, db, owner, sasha, monkeypatch, no_portion_call  # noqa: F811
):
    monkeypatch.setattr(OpenAIService, "suggest_portion", no_portion_call)
    monkeypatch.setattr(
        settings, "OPENAI_WEB_SEARCH_MODEL", settings.OPENAI_WEB_SEARCH_MODEL
    )
    person, endpoints = route(db, fake, tg.telegram_service.openai_service)
    assert endpoints <= set(fake.reached)
    _assert_booked(db, fake, person)


def test_the_retag_script_books_each_meal_to_its_own_person(
    fake, db, owner, sasha  # noqa: F811
):
    """It runs with nobody behind it, and its rows are two people's."""
    db.add(Meal(user_id=owner.id, description="суп", nutrients={"foods": ["суп"]}))
    db.add(Meal(user_id=sasha.id, description="чай", nutrients={"foods": ["чай"]}))
    db.commit()
    svc = tg.telegram_service.openai_service
    asyncio.run(food_groups_retag.run(db, svc, apply=True, limit_usd=1.0))
    rows = _ledger(db)
    assert set(rows) == {OWNER, SASHA}
    assert rows[OWNER].calls == rows[SASHA].calls == 1


def test_the_owners_reprocess_books_the_person_whose_message_it_is(
    fake, db, owner, sasha  # noqa: F811
):
    fake.down = True
    send(_from(SASHA, text="овсянка"))
    assert _ledger(db) == {}  # a call that raised has no usage to book
    fake.down = False
    send(
        _message(
            text="/reprocess",
            entities=[{"type": "bot_command", "offset": 0, "length": 10}],
        )
    )
    assert fake.reached
    _assert_booked(db, fake, SASHA)


def test_a_model_with_no_known_price_is_counted_and_not_guessed(
    fake, db, owner, sasha, monkeypatch  # noqa: F811
):
    svc = tg.telegram_service.openai_service
    monkeypatch.setattr(svc, "model", "openai:gpt-not-on-the-list")
    with attribute_to(SASHA):
        asyncio.run(svc.analyze_food_entry("суп"))
    row = _ledger(db)[SASHA]
    assert (row.calls, row.unpriced_calls, row.cost_usd) == (1, 1, 0)


def test_no_usage_in_the_reply_is_counted_and_not_guessed(
    fake, db, owner, sasha  # noqa: F811
):
    fake.usage = None
    svc = tg.telegram_service.openai_service
    asyncio.run(svc.analyze_food_entry("суп"))
    row = _ledger(db)[NO_PERSON]
    assert (row.calls, row.unpriced_calls, row.cost_usd) == (1, 1, 0)


@pytest.mark.parametrize("cost", [math.nan, math.inf, -1.0])
def test_a_number_that_would_poison_the_sum_is_no_price(
    fake, db, owner, cost  # noqa: F811
):
    usage = SimpleNamespace(cost=cost)
    assert usage_meter.price("openrouter:any", usage) is None
    usage_meter.record("openrouter:any", usage)
    row = _ledger(db)[NO_PERSON]
    assert (row.calls, row.unpriced_calls, row.cost_usd) == (1, 1, 0)


def test_a_true_for_a_cost_is_no_price():
    assert usage_meter.price("openrouter:any", SimpleNamespace(cost=True)) is None


def test_a_reported_price_of_zero_is_a_price(fake, db, owner):  # noqa: F811
    """Not wrongly refused: a free model is no unknown one."""
    usage_meter.record("openrouter:free", SimpleNamespace(cost=0.0))
    row = _ledger(db)[NO_PERSON]
    assert (row.calls, row.unpriced_calls, row.cost_usd) == (1, 0, 0)


def test_the_ledger_counts_with_no_meter_open_and_with_one(
    fake, db, owner, sasha  # noqa: F811
):
    svc = tg.telegram_service.openai_service
    assert usage_meter._current.get() is None
    with attribute_to(SASHA):
        asyncio.run(svc.analyze_food_entry("суп"))
        with usage_meter.metering() as meter:
            asyncio.run(svc.analyze_food_entry("суп"))
    assert _ledger(db)[SASHA].calls == 2
    # The meter shows its own call, and only that one.
    assert meter.calls == 1
    assert meter.usd == pytest.approx(float(CHAT_USD))


def test_a_ledger_that_cannot_write_breaks_nothing_and_says_so(
    fake, db, owner, monkeypatch, caplog  # noqa: F811
):
    def broken():
        raise RuntimeError("the database is away")

    monkeypatch.setattr(usage_ledger, "SessionLocal", broken)
    svc = tg.telegram_service.openai_service
    with (
        caplog.at_level(logging.WARNING, logger="app.services.usage_ledger"),
        usage_meter.metering() as meter,
    ):
        answer = asyncio.run(svc.analyze_food_entry("суп"))
    assert answer
    assert meter.calls == 1
    assert "A model call not in the ledger: RuntimeError" in caplog.text


def test_a_person_is_kept_apart_per_day(fake, db, owner, monkeypatch):  # noqa: F811
    import datetime

    monday, tuesday = datetime.date(2026, 11, 2), datetime.date(2026, 11, 3)
    for day in (monday, monday, tuesday):
        crud_usage_ledger.add(db, telegram_id=SASHA, day=day, cost_usd=0.01)
    crud_usage_ledger.add(db, telegram_id=ZHENYA, day=monday, cost_usd=None)
    assert crud_usage_ledger.of_person(db, SASHA).calls == 3
    assert crud_usage_ledger.of_person(db, SASHA, since=tuesday).calls == 1
    everyone = crud_usage_ledger.by_person(db, since=monday)
    assert everyone[SASHA].cost_usd == Decimal("0.03")
    assert everyone[ZHENYA].unpriced_calls == 1
    assert crud_usage_ledger.total(db).calls == 4
    assert crud_usage_ledger.total(db, since=tuesday).calls == 1
    assert crud_usage_ledger.of_person(db, 999).calls == 0


def test_the_day_is_the_settings_zone_not_the_servers(monkeypatch):
    import datetime
    from zoneinfo import ZoneInfo

    monkeypatch.setattr(settings, "DEFAULT_TIMEZONE", "Pacific/Kiritimati")
    assert (
        usage_ledger.today()
        == datetime.datetime.now(ZoneInfo("Pacific/Kiritimati")).date()
    )


# -- the person reaches where the money is spent -------------------------------------


def _through_the_app(person_update, extra_handler):
    async def steps():
        application = tg.create_bot_application()
        application.add_handler(extra_handler, group=50)
        wire = Wire()
        application.bot._request = (wire, wire)
        await application.initialize()
        await application.start()
        try:
            for body in person_update:
                await application.process_update(Update.de_json(body, application.bot))
            # A block=False handler runs in a task of its own.
            await asyncio.sleep(0.05)
        finally:
            await application.stop()
            await application.shutdown()

    asyncio.run(steps())


def _update(message: dict) -> dict:
    return {"update_id": message["message_id"], "message": message}


def test_the_person_reaches_a_block_false_handler_and_a_thread(
    fake, db, owner, sasha  # noqa: F811
):
    seen = {}

    async def handler(update, context):
        seen["handler"] = current_person()
        seen["thread"] = await asyncio.to_thread(current_person)
        seen["task"] = await asyncio.create_task(asyncio.sleep(0, current_person()))

    _through_the_app(
        [_update(_from(SASHA, text="🍲 Блюда"))],
        TypeHandler(Update, handler, block=False),
    )
    assert seen == {"handler": SASHA, "thread": SASHA, "task": SASHA}


def test_an_update_with_no_person_does_not_inherit_the_last_ones(
    fake, db, owner, sasha  # noqa: F811
):
    """Updates run one after another in one task: the person must be set every time."""
    seen = []

    async def handler(update, context):
        seen.append(current_person())

    nobody = {
        "update_id": 999_001,
        "channel_post": {
            "message_id": 7,
            "date": NOW_TS,
            "chat": {"id": -5, "type": "channel"},
            "text": "x",
        },
    }
    with patch.object(settings, "BOT_ACCESS_MODE", "open"):
        _through_the_app(
            [_update(_from(SASHA, text="🍲 Блюда")), nobody],
            TypeHandler(Update, handler),
        )
    assert seen == [SASHA, NO_PERSON]


def test_attribute_to_nests_and_gives_back():
    assert current_person() == NO_PERSON
    with attribute_to(SASHA):
        assert current_person() == SASHA
        with attribute_to(ZHENYA):
            assert current_person() == ZHENYA
        with attribute_to(None):
            assert current_person() == NO_PERSON
        assert current_person() == SASHA
    assert current_person() == NO_PERSON


# -- no call bypasses the recording path ---------------------------------------------

ALLOWED_FREE = {"models.list", "close", "with_options"}
PAID_SITES = {
    "_create",
    "transcribe",
    "_openrouter_web_search",
    "_openai_web_search",
    "embed_text",
    "embed_texts",
}
SDK_CALL = re.compile(
    r"completions\.create|responses\.create|embeddings\.create|transcriptions\.create"
    r"|AsyncOpenAI\(|\bOpenAI\("
)


def _dotted(node) -> list:
    """``self.client.chat.create`` as its names, root first; [] for anything else."""
    names = []
    while isinstance(node, ast.Attribute):
        names.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        names.append(node.id)
        return names[::-1]
    return []


def _is_client(names: list) -> bool:
    return any("client" in name or name == "_openrouter" for name in names[:2])


def _bypasses(source: str) -> tuple:
    """(functions that call a client and also record, those that call it and do not)."""
    recording, bypass = set(), set()
    for function in ast.walk(ast.parse(source)):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        calls = [
            _dotted(node.func)
            for node in ast.walk(function)
            if isinstance(node, ast.Call)
        ]
        through_client = [
            names
            for names in calls
            if len(names) > 1
            and _is_client(names)
            and ".".join(names[-2:]) not in ALLOWED_FREE
            and names[-1] not in ALLOWED_FREE
            and "client" not in names[-1]
        ]
        if not through_client:
            continue
        if ["meter_usage"] in calls:
            recording.add(function.name)
        else:
            bypass.add(function.name)
    return recording, bypass


def test_every_client_call_in_the_service_goes_through_the_recording_path():
    source = (ROOT / "app/services/openai_service.py").read_text(encoding="utf-8")
    recording, bypass = _bypasses(source)
    assert bypass == set()
    assert recording == PAID_SITES


def test_the_completeness_check_catches_a_bypass_and_passes_a_recorded_call():
    """Control: both ways on sources of known shape, or a zero above means nothing."""
    recording, bypass = _bypasses(
        """
async def sneaky(self):
    return await self.client.chat.completions.create(model="x")

async def also_sneaky(client):
    return await client.responses.create(model="x")

async def honest(self):
    response = await self.client.embeddings.create(model="x")
    meter_usage("x", response.usage)

async def free(self):
    return await self.client.models.list()
"""
    )
    assert bypass == {"sneaky", "also_sneaky"}
    assert recording == {"honest"}


def test_no_sdk_call_lives_outside_the_service():
    roots = [ROOT / "app", ROOT / "celery_app", ROOT / "scripts", ROOT / "bot.py"]
    paths = [
        path
        for root in roots
        for path in ([root] if root.is_file() else root.rglob("*.py"))
    ]
    hits = {
        path.relative_to(ROOT).as_posix()
        for path in paths
        if SDK_CALL.search(path.read_text(encoding="utf-8"))
    }
    assert "app/services/openai_service.py" in hits  # control: the scan does see one
    assert hits == {"app/services/openai_service.py"}


def test_the_ledger_table_has_no_place_for_words():
    columns = {column.name for column in UsageLedger.__table__.columns}
    assert columns == {
        "id",
        "telegram_id",
        "day",
        "calls",
        "cost_usd",
        "unpriced_calls",
        "created_at",
        "updated_at",
    }
    assert not UsageLedger.__table__.foreign_keys
