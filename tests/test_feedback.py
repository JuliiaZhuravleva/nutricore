"""💬 Фидбэк through the real application. Telegram is a fake wire, the local whisper a
fake server on the loopback, the model a fake reader: nothing here reaches any of them.

What a real phone does with a forward, a collapsed quote or the command menu no test here
can see.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import re
from types import SimpleNamespace

import pytest
from openai.resources.audio.transcriptions import AsyncTranscriptions
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from telegram import Update

import app.services.access_control as access_control
import app.services.ai_call_log_service as log_service
import app.services.inbound_message_service as inbound_service
import app.services.model_selection as model_selection
import app.services.telegram as tg
import app.services.usage_ledger as usage_ledger
from app.core.config import settings
from app.crud.crud_action_event import crud_action_event
from app.crud.crud_feedback import crud_feedback
from app.crud.crud_subscription import crud_subscription
from app.db.base import Base
from app.models import (
    ActionEvent,
    BodyMetric,
    Feedback,
    Meal,
    MealSource,
    UsageLedger,
    User,
)
from app.models.action_event import BUTTON, CORRECTED, TEXT
from app.services import (
    action_journal,
    admin_notify,
    explain_screen,
    feedback_inbox,
    feedback_screen,
    invites,
    meal_card,
    settings_screen,
    voice,
)
from tests.test_capture_router import NOW_TS, NUTRITION, Wire
from tests.test_capture_voice import FakeWhisper

OWNER, SASHA, ZHENYA = 1001, 3003, 3004
OGG = b"OggS\x00\x02" + b"\x00" * 40
PHOTO = [
    {"file_id": "small", "file_unique_id": "s", "width": 90, "height": 90},
    {"file_id": "big", "file_unique_id": "b", "width": 900, "height": 900},
]
VOICE = {"file_id": "v1", "file_unique_id": "vu", "duration": 3, "file_size": 1000}
_ids = iter(range(100, 10_000_000))


class Desk(Wire):
    """Answers in the asked chat. ``refuse(name, params)`` names a call Telegram turns
    down: a description, or None to let it through."""

    file_bytes = OGG

    def __init__(self, refuse=None):
        super().__init__()
        self.refuse = refuse or (lambda _name, _params: None)

    def _result(self, name, params):
        if name == "forwardMessage":
            self.next_id += 1
            return {
                "message_id": self.next_id,
                "date": NOW_TS,
                "chat": {"id": int(params["chat_id"]), "type": "private"},
            }
        result = super()._result(name, params)
        if isinstance(result, dict) and "chat" in result and "chat_id" in params:
            result["chat"]["id"] = int(params["chat_id"])
        return result

    async def do_request(self, url, method, request_data=None, **kwargs):
        name = url.rsplit("/", 1)[-1]
        params = dict(request_data.parameters) if request_data else {}
        said = self.refuse(name, params)
        if said is not None:
            self.calls.append(("refused:" + name, params))
            body = {"ok": False, "error_code": 400, "description": said}
            return 400, json.dumps(body).encode()
        return await super().do_request(url, method, request_data, **kwargs)

    def to(self, chat: int, name: str = "sendMessage") -> list:
        return [p for n, p in self.calls if n == name and int(p["chat_id"]) == chat]

    def names(self, chat: int) -> list:
        return [
            n for n, p in self.calls if "chat_id" in p and int(p["chat_id"]) == chat
        ]


def play(*steps, refuse=None) -> Desk:
    """One application, so a wait begun by one is seen by the next. A callable among the
    steps runs between two updates."""
    wire = Desk(refuse)

    async def go():
        application = tg.create_bot_application()
        application.bot._request = (wire, wire)
        await application.initialize()
        await application.start()
        try:
            for step in steps:
                if callable(step):
                    step()
                    continue
                await application.process_update(Update.de_json(step, application.bot))
                await asyncio.gather(*list(tg._meal_reads), return_exceptions=True)
        finally:
            await application.stop()
            await application.shutdown()

    asyncio.run(go())
    return wire


def _who(person: int) -> dict:
    return {
        SASHA: {
            "id": SASHA,
            "is_bot": False,
            "first_name": "Саша",
            "username": "sasha_k",
        },
        ZHENYA: {"id": ZHENYA, "is_bot": False, "first_name": "Женя"},
        OWNER: {"id": OWNER, "is_bot": False, "first_name": "Мика"},
    }[person]


def said(person: int = SASHA, **fields) -> dict:
    return {
        "update_id": next(_ids),
        "message": {
            "message_id": next(_ids),
            "date": NOW_TS,
            "chat": {"id": person, "type": "private"},
            "from": _who(person),
            **fields,
        },
    }


def words(body: str, person: int = SASHA) -> dict:
    return said(person, text=body)


def command(person: int = SASHA, name: str = "/feedback") -> dict:
    entity = {"type": "bot_command", "offset": 0, "length": len(name)}
    return said(person, text=name, entities=[entity])


def tap(data: str, person: int = SASHA, message_id: int = 4900) -> dict:
    return {
        "update_id": next(_ids),
        "callback_query": {
            "id": "1",
            "from": _who(person),
            "chat_instance": "c",
            "data": data,
            "message": {
                "message_id": message_id,
                "date": NOW_TS,
                "chat": {"id": person, "type": "private"},
                "text": "x",
            },
        },
    }


@pytest.fixture
def Session(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    for module in (
        tg,
        inbound_service,
        log_service,
        model_selection,
        usage_ledger,
        action_journal,
        access_control,
    ):
        monkeypatch.setattr(module, "SessionLocal", factory)
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_IDS", str(OWNER))
    monkeypatch.setattr(settings, "BOT_ACCESS_MODE", "whitelist")
    monkeypatch.setattr(settings, "ALLOWED_TELEGRAM_IDS", f"{SASHA},{ZHENYA}")
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "123:fake")
    monkeypatch.setattr(settings, "CUSTOM_EMOJI", None)
    monkeypatch.setattr(settings, "WHISPER_URL", "")
    monkeypatch.setattr(tg, "_schedule_personal_food_save", lambda **_: None)
    tg._card_delivery._screens.clear()
    return factory


@pytest.fixture
def db(Session):
    session = Session()
    for person in (SASHA, ZHENYA, OWNER):
        session.add(User(telegram_id=person, settings={"timezone": "Asia/Tbilisi"}))
    session.commit()
    crud_subscription.create_subscription(
        session, telegram_id=SASHA, granted_by_telegram_id=OWNER
    )
    yield session
    session.close()


@pytest.fixture
def model(monkeypatch):
    """No model is reached: what it was asked stays here."""
    asked = SimpleNamespace(text=[], image=[])

    async def analyze(*, kind, payload, input_ref, telegram_id):
        asked.text.append(payload)
        return dict(NUTRITION)

    async def resolve(payload, *, telegram_id, input_ref, caption=None, more_images=()):
        asked.image.append(payload)
        raise RuntimeError("the model is not up in this test")

    monkeypatch.setattr(tg, "_analyze_and_parse", analyze)
    monkeypatch.setattr(tg, "resolve_meal_nutrition", resolve)
    return asked


@pytest.fixture
def whisper(monkeypatch):
    fake = FakeWhisper()
    monkeypatch.setattr(settings, "WHISPER_URL", fake.url)
    yield fake
    fake.close()


@pytest.fixture
def paid(monkeypatch):
    """The SDK's own door. Nothing should reach it from here."""
    state = SimpleNamespace(calls=[])

    async def create(self, **kwargs):
        state.calls.append(kwargs)
        usage = SimpleNamespace(input_tokens=1000, output_tokens=100)
        return SimpleNamespace(text="суп с паид", usage=usage)

    monkeypatch.setattr(AsyncTranscriptions, "create", create)
    return state


def _labels(markup) -> list:
    if isinstance(markup, str):
        markup = json.loads(markup)
    if isinstance(markup, dict):
        return [[b["text"] for b in row] for row in markup["inline_keyboard"]]
    return [[b.text for b in row] for row in markup.inline_keyboard]


def _datas(markup) -> list:
    markup = json.loads(markup) if isinstance(markup, str) else markup
    return [[b["callback_data"] for b in row] for row in markup["inline_keyboard"]]


def _within_limits(labels: list) -> None:
    assert len(labels) <= 6
    assert all(len(row) <= 3 for row in labels)


def _rows(db) -> list:
    db.expire_all()
    return list(db.scalars(select(Feedback).order_by(Feedback.id)))


def _meals(db, person: int = SASHA) -> list:
    db.expire_all()
    return list(
        db.scalars(
            select(Meal)
            .join(User, Meal.user_id == User.id)
            .where(User.telegram_id == person)
        )
    )


FOOD_DATA = "fb:food:{}"


# --- the screens ----------------------------------------------------------------------


def test_the_prompt_says_the_designs_words_and_has_its_two_buttons(db):
    params = play(command()).to(SASHA)[0]
    assert params["text"] == (
        "<b>💬 Фидбэк</b>\n\n"
        "Напиши или наговори, что было непонятно и чего не хватает. Можно приложить "
        "скриншот.\n\n"
        "Следующее сообщение уйдёт владельцу бота, а не в дневник. С ним уйдут твои "
        "последние 20 действий: время и какие кнопки нажаты, без текста записей."
    )
    assert _labels(params["reply_markup"]) == [["👁 Что уйдёт", "✖️ Отмена"]]


def test_the_row_in_settings_opens_it_and_the_hub_keeps_its_budget(db):
    hub = settings_screen.open_view(db, SASHA)
    labels = _labels(hub.markup)
    assert labels[3] == ["🕐 Пояс", "💊 Рутины", "💬 Фидбэк"]
    _within_limits(labels)
    wire = play(tap("set:open"), tap(_datas_of(hub, "💬 Фидбэк")))
    assert wire.edits()[-1]["text"].startswith("<b>💬 Фидбэк</b>")


def _datas_of(screen, label: str) -> str:
    for row in screen.markup.inline_keyboard:
        for button in row:
            if button.text == label:
                return button.callback_data
    raise AssertionError(label)


@pytest.mark.parametrize("nutrients", [True, False])
def test_the_row_in_how_it_works_opens_it_and_every_page_keeps_the_budget(
    db, nutrients
):
    # 🔔 Напоминания (stage 6) made it three pages with the vitamins, two without.
    count = 3 if nutrients else 2
    pages = [explain_screen.list_screen(p, nutrients=nutrients) for p in range(count)]
    for page in pages:
        _within_limits(_labels(page.markup))
    for page in pages[:-1]:
        assert "💬 Фидбэк" not in sum(_labels(page.markup), [])
    assert "💬 Фидбэк" in sum(_labels(pages[-1].markup), [])
    assert _datas_of(pages[-1], "💬 Фидбэк") == feedback_screen.OPEN
    assert _labels(explain_screen.list_screen(count, nutrients=nutrients).markup) == (
        _labels(pages[0].markup)
    )  # no page past the last: it comes back to the first


def test_the_preview_is_the_owners_words_with_a_named_back(db):
    crud_action_event.add(db, telegram_id=SASHA, kind=CORRECTED, label="grams")
    crud_action_event.add(db, telegram_id=SASHA, kind=BUTTON, label="set")
    db.commit()
    wire = play(tap("fb:see"))
    screen = wire.edits()[0]
    assert screen["text"].startswith(
        "<b>👁 Что уйдёт</b>\n\n<blockquote expandable>Последние действия\n"
    )
    assert "✏️ вес изменён" in screen["text"]
    assert _labels(screen["reply_markup"]) == [["⬅️ Фидбэк"]]
    assert _datas(screen["reply_markup"]) == [["fb:open"]]

    told = play(command(), words("всё понятно"))
    owner = told.to(OWNER)[0]["text"]
    for line in screen["text"].split("\n")[3:]:
        assert line.removesuffix("</blockquote>") in owner


def test_the_preview_shows_only_ones_own(db):
    crud_action_event.add(db, telegram_id=ZHENYA, kind=CORRECTED, label="grams")
    db.commit()
    text = play(tap("fb:see")).edits()[0]["text"]
    assert "вес изменён" not in text
    # The tap itself is in the journal: Саша's, and there.
    assert "👆 💬 Фидбэк" in text


def test_cancel_goes_to_the_settings_and_the_diary_is_the_diary_again(db, model):
    wire = play(command(), tap("fb:x"), words("суп 200 г"))
    hub = wire.edits()[0]
    assert hub["text"].startswith("<i>✓ Фидбэк отменён</i>\n\n<b>⚙️ Настройки</b>")
    assert _rows(db) == []
    assert len(_meals(db)) == 1


def test_a_stale_button_leads_to_the_current_screen(db, caplog):
    with caplog.at_level(logging.WARNING):
        wire = play(tap("fb:nonsense"))
    assert wire.edits()[0]["text"].startswith("<b>⚙️ Настройки</b>")
    assert "unknown callback" in caplog.text


# --- the wait -------------------------------------------------------------------------


def test_the_wait_takes_the_next_message_ahead_of_the_diary(db, model):
    wire = play(command(), words("не понятно, где итог"))
    rows = _rows(db)
    assert [(r.telegram_id, r.kind, r.text) for r in rows] == [
        (SASHA, "text", "не понятно, где итог")
    ]
    assert _meals(db) == []
    assert model.text == []
    last = wire.to(SASHA)[-1]
    assert last["text"] == "<i>✓ Отправлено. Спасибо!</i>"
    assert _labels(last["reply_markup"]) == [["✅ ОК"], ["↩️ Это была еда: записать"]]
    assert _datas(last["reply_markup"]) == [["ok:close"], [f"fb:food:{rows[0].id}"]]


def test_only_the_next_message_goes_the_one_after_is_food(db, model):
    play(command(), words("про кнопки"), words("суп 200 г"))
    assert [r.text for r in _rows(db)] == ["про кнопки"]
    assert len(_meals(db)) == 1


def test_the_wait_is_ahead_of_the_typed_slot(db, model):
    def open_slot():
        db.expire_all()
        user = db.scalar(select(User).where(User.telegram_id == SASHA))
        user.settings = {**user.settings, meal_card.ASK: {"kind": "qty"}}
        db.commit()

    play(command(), open_slot, words("это для владельца"))
    assert [r.text for r in _rows(db)] == ["это для владельца"]
    assert meal_card.has_slot(db, SASHA)  # nobody answered it


def test_the_handlers_stand_in_that_order(Session):
    groups = tg.create_bot_application().handlers
    names = ("invite_intake", "feedback_intake", "meal_slot_text", "capture_message")
    where = {
        h.callback.__name__: g
        for g, hs in groups.items()
        for h in hs
        if getattr(getattr(h, "callback", None), "__name__", None) in names
    }
    assert (
        where["invite_intake"]
        < where["feedback_intake"]
        < where["meal_slot_text"]
        < where["capture_message"]
    )


def test_the_wait_is_a_quarter_of_an_hour():
    assert tg.FEEDBACK_WAIT_SECONDS == 15 * 60


def test_an_expired_wait_passes_the_message_on_to_the_diary(db, model, monkeypatch):
    monkeypatch.setattr(tg, "FEEDBACK_WAIT_SECONDS", -1)
    wire = play(command(), words("суп 200 г"))
    assert _rows(db) == []
    assert len(_meals(db)) == 1
    assert "Отправлено" not in " ".join(p["text"] for p in wire.to(SASHA))


@pytest.mark.parametrize(
    "label",
    [
        "⭐ Избранное",
        "📊 Статистика",
        "⚙️ Настройки",
        "🍲 Блюда",
        "🍽 Добавить прием пищи",
    ],
)
def test_a_menu_button_cancels_at_once_and_does_its_own_work(db, model, label):
    wire = play(command(), words(label), words("суп 200 г"))
    assert _rows(db) == []
    assert len(wire.to(SASHA)) >= 2  # the button answered
    assert len(_meals(db)) == 1  # and the next message was food again


def test_every_menu_label_is_known_to_the_wait():
    main = {b.text for row in tg.main_keyboard.keyboard for b in row}
    assert (main | set(tg._OLD_LOCK_LABELS)) <= tg._FEEDBACK_LEAVES


def test_a_word_that_is_only_like_a_label_is_still_feedback(db):
    play(command(), words("Обед"))
    assert [r.text for r in _rows(db)] == ["Обед"]


def test_another_command_cancels_it(db):
    wire = play(command(), command(name="/start"), words("суп"))
    assert _rows(db) == []
    assert not [p for p in wire.to(SASHA) if "Отправлено" in p["text"]]


def test_a_slash_inside_words_does_not_cancel(db):
    play(command(), words("а что делает /start?"))
    assert [r.text for r in _rows(db)] == ["а что делает /start?"]


def test_another_inline_tap_cancels_it(db, model):
    play(command(), tap("set:open"), words("суп 200 г"))
    assert _rows(db) == []
    assert len(_meals(db)) == 1


def test_the_look_keeps_the_wait_and_back_starts_it_again(db):
    play(command(), tap("fb:see"), words("первое"))
    play(tap("fb:see"), tap("fb:open"), words("второе"))
    assert [r.text for r in _rows(db)] == ["первое", "второе"]


def test_what_cannot_go_is_named_and_the_wait_stays(db):
    sticker = {
        "file_id": "s",
        "file_unique_id": "su",
        "width": 1,
        "height": 1,
        "is_animated": False,
        "is_video": False,
        "type": "regular",
    }
    wire = play(command(), said(sticker=sticker), words("вот словами"))
    assert feedback_screen.UNSUPPORTED in [p["text"] for p in wire.to(SASHA)]
    assert [r.text for r in _rows(db)] == ["вот словами"]


def test_the_rest_of_an_album_goes_with_it_not_into_the_diary(db, model):
    one = said(photo=PHOTO, media_group_id="77", caption="вот так")
    two = said(photo=PHOTO, media_group_id="77")
    play(command(), one, two, said(photo=PHOTO, media_group_id="78"))
    assert [r.kind for r in _rows(db)] == ["photo", "photo"]
    assert len(_meals(db)) == 1  # another album, later: food


def test_a_locked_person_can_still_write(db):
    wire = play(command(ZHENYA), words("а почему закрыто", ZHENYA))
    assert [r.telegram_id for r in _rows(db)] == [ZHENYA]
    assert wire.to(OWNER)


def test_a_row_that_cannot_be_saved_is_said_and_nothing_is_sent(db, monkeypatch):
    def no(*_a, **_k):
        raise RuntimeError("no database")

    monkeypatch.setattr(crud_feedback, "create", no)
    wire = play(command(), words("про кнопки"))
    assert wire.to(SASHA)[-1]["text"] == feedback_screen.NOT_SAVED
    assert wire.to(OWNER) == []


def test_the_prompt_loses_its_buttons_once_answered(db):
    wire = play(command(), words("про кнопки"))
    cleared = [p for n, p in wire.calls if n == "editMessageReplyMarkup"]
    assert len(cleared) == 1 and int(cleared[0]["chat_id"]) == SASHA


# --- the row comes first --------------------------------------------------------------


def test_the_saved_row_survives_every_send_failing(db):
    def everything(name, _params):
        if name in ("sendMessage", "forwardMessage", "editMessageReplyMarkup"):
            return "Forbidden: no"
        return None

    # The screen itself has to get through for the wait to begin.
    wire = play(
        command(),
        lambda: setattr(wire_box, "armed", True),
        words("про кнопки"),
        refuse=lambda name, p: everything(name, p) if wire_box.armed else None,
    )
    row = _rows(db)[0]
    assert row.text == "про кнопки"
    assert row.sent_at is None
    assert "refused:sendMessage" in [n for n, _p in wire.calls]


wire_box = SimpleNamespace(armed=False)


@pytest.fixture(autouse=True)
def _unarmed():
    wire_box.armed = False


def test_a_delivered_row_is_marked_sent(db):
    play(command(), words("про кнопки"))
    assert _rows(db)[0].sent_at is not None


def test_no_admins_leaves_it_undelivered(db, monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_IDS", "")
    play(command(), words("про кнопки"))
    assert _rows(db)[0].sent_at is None


# --- the owner's copy -----------------------------------------------------------------


def test_the_owners_copy_has_the_head_the_words_the_journal_and_the_card(db):
    play(tap("set:open"))
    wire = play(command(), words("Не понятно, где итог"))
    owner = wire.to(OWNER)
    assert len(owner) == 1
    text = owner[0]["text"]
    first, _, rest = text.partition("\n\n")
    assert first.startswith("<b>💬 Фидбэк · Саша · @sasha_k · ")
    assert rest.startswith("«Не понятно, где итог»\n\n<blockquote expandable>")
    assert text.endswith("</blockquote>")
    assert "👆 ⚙️ Настройки" in text
    assert _labels(owner[0]["reply_markup"]) == [["👤 Саша"]]
    assert _datas(owner[0]["reply_markup"]) == [[f"usr:c:{SASHA}"]]
    assert owner[0]["parse_mode"] == "HTML"
    assert wire.to(OWNER, "forwardMessage") == []


def test_no_username_means_no_at(db):
    text = play(command(ZHENYA), words("а", ZHENYA)).to(OWNER)[0]["text"]
    assert "Женя · " in text
    assert "@" not in text


def test_the_photo_is_forwarded_first_and_the_caption_is_the_words(db):
    message = said(photo=PHOTO, caption="вот тут непонятно")
    wire = play(command(), message)
    assert wire.names(OWNER)[:2] == ["forwardMessage", "sendMessage"]
    forwarded = wire.to(OWNER, "forwardMessage")[0]
    assert int(forwarded["from_chat_id"]) == SASHA
    assert forwarded["message_id"] == message["message"]["message_id"]
    text = wire.to(OWNER)[0]["text"]
    assert "📷 фото (само сообщение — выше)" in text
    assert "«вот тут непонятно»" in text
    row = _rows(db)[0]
    assert (row.kind, row.file_id) == ("photo", "big")


def test_a_screenshot_sent_as_a_file_counts_as_a_photo(db):
    document = {
        "file_id": "doc",
        "file_unique_id": "d",
        "mime_type": "image/png",
        "file_size": 1000,
    }
    wire = play(command(), said(document=document))
    assert (_rows(db)[0].kind, _rows(db)[0].file_id) == ("photo", "doc")
    assert wire.to(OWNER, "forwardMessage")


def test_every_person_word_is_escaped(db):
    evil = 'a <b>&</blockquote><a href="x">'
    who = {**_who(SASHA), "first_name": "<i>Са&ша"}
    message = words(evil)
    message["message"]["from"] = who
    text = play(command(), message).to(OWNER)[0]["text"]
    assert "«a &lt;b&gt;&amp;&lt;/blockquote&gt;" in text
    assert "&lt;i&gt;Са&amp;ша" in text
    assert text.count("<blockquote") == 1 and text.count("</blockquote>") == 1
    assert "<i>" not in text and "<a " not in text


def test_refused_html_still_delivers_the_words_plain(db):
    def only_html(name, params):
        if name == "sendMessage" and params.get("parse_mode") and wire_box.armed:
            return "Bad Request: can't parse entities"
        return None

    wire = play(
        command(),
        lambda: setattr(wire_box, "armed", True),
        words("про <кнопки>"),
        refuse=only_html,
    )
    plain = [p for p in wire.to(OWNER) if "parse_mode" not in p]
    assert len(plain) == 1
    assert "«про <кнопки>»" in plain[0]["text"]
    assert "Последние действия" in plain[0]["text"]
    assert _labels(plain[0]["reply_markup"]) == [["👤 Саша"]]
    assert _rows(db)[0].sent_at is not None


def _row(**fields) -> SimpleNamespace:
    base = dict(
        telegram_id=SASHA,
        kind="text",
        text=None,
        transcript=None,
        journal=None,
        was_food_at=None,
    )
    return SimpleNamespace(**{**base, **fields})


def _parts(row) -> list:
    from datetime import UTC, datetime
    from zoneinfo import ZoneInfo

    person = invites.Person(SASHA, "Саша", None, "sasha_k")
    return feedback_screen.owner_parts(
        row, person, datetime(2026, 10, 26, 10, 8, tzinfo=UTC), ZoneInfo("Asia/Tbilisi")
    )


def _plain_units(part) -> int:
    return feedback_screen.units(part.plain)


JOURNAL = "\n".join(f"14:{m:02d} 👆 ⚙️ Настройки" for m in range(20))


def test_the_journal_gives_way_first_oldest_lines_first():
    room = 4096 - 400
    parts = _parts(_row(text="с" * room, journal=JOURNAL))
    assert len(parts) == 1
    assert _plain_units(parts[0]) <= 4096
    assert f"«{'с' * room}»" in parts[0].plain
    assert "14:19" in parts[0].plain  # the newest stays
    assert "14:00" not in parts[0].plain  # the oldest went
    assert "14:19" in parts[0].html and "14:00" not in parts[0].html


def test_words_at_the_full_limit_are_never_cut_and_the_journal_may_go():
    text = "щ" * 4096
    parts = _parts(_row(text=text, journal=JOURNAL))
    assert all(_plain_units(p) <= 4096 for p in parts)
    joined = "".join(p.plain for p in parts)
    assert joined.count("щ") == 4096
    assert parts[-1].markup is not None
    assert all(p.markup is None for p in parts[:-1])


def test_a_long_transcript_goes_whole_and_emoji_count_double():
    text = "😀" * 5000
    parts = _parts(_row(kind="voice", transcript=text, journal=JOURNAL))
    assert len(parts) == 3
    assert all(_plain_units(p) <= 4096 for p in parts)
    assert "".join(p.plain for p in parts).count("😀") == 5000
    assert "🎙 голосовое" in parts[0].plain


def test_no_journal_at_all_is_no_empty_quote():
    parts = _parts(_row(text="а", journal=""))
    assert "blockquote" not in parts[0].html
    assert "Последние" not in parts[0].plain


# --- notify_admins and its new sibling ------------------------------------------------


class _Bot:
    def __init__(self, fails=()):
        self.fails = set(fails)
        self.calls = []

    async def forward_message(self, chat_id, from_chat_id, message_id):
        self.calls.append(("forward", chat_id))
        if ("forward", chat_id) in self.fails:
            raise RuntimeError("no")

    async def send_message(self, chat_id, text, **_kwargs):
        self.calls.append(("send", chat_id))
        if ("send", chat_id) in self.fails:
            raise RuntimeError("no")


_PART = [admin_notify.Part("<b>x</b>", "x")]


def _deliver(monkeypatch, bot, admins: str = "1,2", forward=(9, 9)) -> bool:
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_IDS", admins)
    return asyncio.run(admin_notify.deliver_to_admins(_PART, forward=forward, bot=bot))


def test_one_admin_with_a_whole_copy_is_enough(monkeypatch):
    bot = _Bot({("send", 1)})
    assert _deliver(monkeypatch, bot) is True
    assert ("send", 2) in bot.calls  # the failure stopped nobody


def test_a_lost_forward_is_no_whole_copy_and_the_words_still_go(monkeypatch):
    bot = _Bot({("forward", 1)})
    assert _deliver(monkeypatch, bot, admins="1") is False
    assert bot.calls.count(("send", 1)) == 2  # the warning, and the part


def test_nobody_reached_is_false_and_never_raises(monkeypatch):
    bot = _Bot({("send", 1), ("send", 2), ("forward", 1), ("forward", 2)})
    assert _deliver(monkeypatch, bot) is False


def test_no_admins_is_false(monkeypatch):
    assert _deliver(monkeypatch, _Bot(), admins="") is False


def test_notify_admins_still_makes_its_own_bot_and_counts(monkeypatch):
    made = []

    class Own(_Bot):
        def __init__(self, token):
            super().__init__({("send", 1)})
            made.append(self)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

    monkeypatch.setattr(admin_notify, "Bot", Own)
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_IDS", "1,2")
    assert asyncio.run(admin_notify.notify_admins("x")) is True
    assert len(made) == 1
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_IDS", "1")
    assert asyncio.run(admin_notify.notify_admins("x")) is False


def test_a_client_that_cannot_start_is_false_for_both(monkeypatch):
    class Broken:
        def __init__(self, _token):
            pass

        async def __aenter__(self):
            raise RuntimeError("no network")

        async def __aexit__(self, *_):
            return False

    monkeypatch.setattr(admin_notify, "Bot", Broken)
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_IDS", "1")
    assert asyncio.run(admin_notify.notify_admins("x")) is False
    assert asyncio.run(admin_notify.deliver_to_admins(_PART)) is False


# --- the voice ------------------------------------------------------------------------


def test_a_voice_is_forwarded_and_heard_for_free_with_the_general_prompt(
    db, whisper, paid
):
    whisper.answers("не понятно, где итог <b>")
    wire = play(command(), said(voice=VOICE))
    assert wire.names(OWNER)[:2] == ["forwardMessage", "sendMessage"]
    text = wire.to(OWNER)[0]["text"]
    assert "🎙 голосовое (само сообщение — выше)" in text
    assert "«не понятно, где итог &lt;b&gt;»" in text
    assert _rows(db)[0].transcript == "не понятно, где итог <b>"
    assert _rows(db)[0].sent_at is not None
    raw = whisper.calls[0]["raw"]
    assert voice.GENERAL_PROMPT.encode() in raw
    assert voice.FOOD_PROMPT.encode() not in raw
    assert paid.calls == []
    assert db.scalars(select(UsageLedger)).all() == []


@pytest.mark.parametrize("status", [422, 500])
def test_a_voice_nobody_heard_goes_without_words(db, whisper, paid, status):
    whisper.refuses(status)
    wire = play(command(), said(voice=VOICE))
    assert whisper.calls  # the service was asked
    assert wire.to(OWNER, "forwardMessage")
    assert "Без расшифровки." in wire.to(OWNER)[0]["text"]
    assert "«" not in wire.to(OWNER)[0]["text"]
    assert _rows(db)[0].transcript is None
    assert _rows(db)[0].sent_at is not None
    assert paid.calls == []
    assert db.scalars(select(UsageLedger)).all() == []


def test_with_no_local_service_nothing_is_paid_for_and_the_voice_goes(db, paid):
    wire = play(command(), said(voice=VOICE))
    assert wire.to(OWNER, "forwardMessage")
    assert "Без расшифровки." in wire.to(OWNER)[0]["text"]
    assert paid.calls == []
    assert db.scalars(select(UsageLedger)).all() == []


def test_a_download_that_fails_costs_the_words_only(db, whisper):
    def no_file(name, _params):
        return "Bad Request: file is too big" if name == "getFile" else None

    wire = play(command(), said(voice=VOICE), refuse=no_file)
    assert whisper.calls == []
    assert "Без расшифровки." in wire.to(OWNER)[0]["text"]
    assert _rows(db)[0].sent_at is not None


def test_the_delivery_hands_no_paid_door_to_the_hearing(db, monkeypatch):
    seen = {}

    async def hear(audio, **kwargs):
        seen.update(kwargs)
        raise voice.NotHeard()

    monkeypatch.setattr(voice, "hear", hear)
    play(command(), said(voice=VOICE))
    assert seen == {"local_only": True, "prompt": voice.GENERAL_PROMPT}
    assert "paid" not in inspect.signature(tg._feedback_heard).parameters


def test_hear_with_no_paid_door_is_not_heard_and_its_default_prompt_is_foods():
    assert inspect.signature(voice.hear).parameters["prompt"].default == (
        voice.FOOD_PROMPT
    )

    async def run():
        with pytest.raises(voice.NotHeard):
            await voice.hear(OGG)

    settings_url = settings.WHISPER_URL
    settings.WHISPER_URL = ""
    try:
        asyncio.run(run())
    finally:
        settings.WHISPER_URL = settings_url


# --- ↩️ Это была еда -------------------------------------------------------------------


def test_text_that_was_food_goes_through_the_real_capture(db, model):
    wire_one = play(command(), words("суп 200 г"))
    assert _meals(db) == []
    row = _rows(db)[0]
    wire = play(tap(FOOD_DATA.format(row.id)))
    meals = _meals(db)
    assert len(meals) == 1
    assert model.text == ["суп 200 г"]
    assert _rows(db)[0].was_food_at is not None
    assert wire_one.to(OWNER)
    # The owner has it already, and is told.
    assert wire.to(OWNER)[0]["text"] == (
        "↩️ Саша: то сообщение оказалось едой, записано в дневник."
    )
    done = [p for p in wire.edits() if p["message_id"] == 4900]
    assert [p["text"] for p in done] == ["<i>✓ Записано в дневник.</i>"]
    assert "reply_markup" not in done[0]


def test_a_photo_that_was_food_is_a_meal_with_its_photo(db, model):
    original = said(photo=PHOTO, caption="обед")
    play(command(), original)
    wire = play(tap(FOOD_DATA.format(_rows(db)[0].id)))
    source = db.scalar(select(MealSource))
    assert source is not None
    assert source.message_id == original["message"]["message_id"]
    assert source.kind == "photo"
    assert len(_meals(db)) == 1
    assert _rows(db)[0].was_food_at is not None
    assert wire.to(OWNER)


def test_a_voice_that_was_food_is_a_meal_with_its_voice(db, model, whisper, paid):
    whisper.answers("суп и хлеб")
    original = said(voice=VOICE)
    play(command(), original)
    asked_before = len(whisper.calls)
    play(tap(FOOD_DATA.format(_rows(db)[0].id)))
    source = db.scalar(select(MealSource))
    assert source is not None and source.kind == "voice"
    assert source.message_id == original["message"]["message_id"]
    assert len(_meals(db)) == 1
    assert _rows(db)[0].was_food_at is not None
    # Now it is the diary's own read: the food prompt, as for any voice.
    assert len(whisper.calls) == asked_before + 1
    assert voice.FOOD_PROMPT.encode() in whisper.calls[-1]["raw"]


def test_the_diarys_voice_is_the_one_door_that_may_pay_the_control(
    db, model, whisper, paid
):
    """The known positive of the «nothing paid» checks above: the fake door is live."""
    whisper.refuses(500)
    play(command(), said(voice=VOICE))
    assert paid.calls == []
    play(tap(FOOD_DATA.format(_rows(db)[0].id)))
    assert len(paid.calls) == 1


def test_a_second_tap_writes_nothing_and_tells_nobody(db, model):
    play(command(), words("суп 200 г"))
    row = _rows(db)[0]
    data = FOOD_DATA.format(row.id)
    play(tap(data))
    stamped = _rows(db)[0].was_food_at
    wire = play(tap(data))
    assert len(_meals(db)) == 1
    assert _rows(db)[0].was_food_at == stamped
    assert wire.to(OWNER) == []
    answer = [p for n, p in wire.calls if n == "answerCallbackQuery"][0]
    assert answer["text"] == feedback_screen.FOOD_ALREADY


def test_a_message_that_became_food_before_its_copy_went_never_reaches_the_owner(
    db, model, monkeypatch
):
    delivered = tg._feedback_deliver

    async def not_yet(*_args):
        return None

    monkeypatch.setattr(tg, "_feedback_deliver", not_yet)
    play(command(), words("суп 200 г"))
    row = _rows(db)[0]
    assert row.sent_at is None
    wire = play(tap(FOOD_DATA.format(row.id)))
    assert wire.to(OWNER) == []  # no follow-up about a copy nobody has
    assert _rows(db)[0].was_food_at is not None

    # The delivery that comes after finds a record, and owner screens hold no records.
    monkeypatch.setattr(tg, "_feedback_deliver", delivered)
    late = Desk()

    async def go():
        application = tg.create_bot_application()
        application.bot._request = (late, late)
        await application.initialize()
        person = invites.Person(SASHA, "Саша", None, "sasha_k")
        await tg._feedback_deliver(application.bot, row.id, person, SASHA, 1)
        await application.shutdown()

    asyncio.run(go())
    assert [n for n, _p in late.calls if n.startswith("send")] == []
    assert _rows(db)[0].sent_at is None
    assert crud_feedback.count_undelivered(db, None) == 0  # and no resend is offered
    assert (
        crud_feedback.count_was_food(db, None) == 1
    )  # only the count says it was there


def test_delivery_is_still_the_rule_for_a_message_that_stayed_feedback(
    db, model, monkeypatch
):
    # The control of the one above: same path, no tap, and the words arrive.
    wire = play(command(), words("суп 200 г"))
    assert any("суп 200 г" in p["text"] for p in wire.to(OWNER))


def test_a_voice_that_became_food_is_not_paid_for_on_the_way_to_nobody(
    db, model, monkeypatch, whisper
):
    async def not_yet(*_args):
        return None

    with monkeypatch.context() as patch:
        patch.setattr(tg, "_feedback_deliver", not_yet)
        play(command(), said(voice=VOICE))
    row = _rows(db)[0]
    crud_feedback.mark_food(db, row.id)
    heard = []

    async def listens(*args):
        heard.append(args)

    monkeypatch.setattr(tg, "_feedback_heard", listens)
    late = Desk()

    async def go():
        application = tg.create_bot_application()
        application.bot._request = (late, late)
        await application.initialize()
        person = invites.Person(SASHA, "Саша", None, "sasha_k")
        await tg._feedback_deliver(application.bot, row.id, person, SASHA, 1)
        await application.shutdown()

    asyncio.run(go())
    assert heard == []
    assert late.to(OWNER) == [] and late.to(OWNER, "sendVoice") == []


def _copy_time(wire: Desk) -> str:
    return re.search(r"\d\d:\d\d", wire.to(OWNER)[0]["text"]).group()


def test_one_message_has_one_time_for_the_owner_wherever_it_is_seen(
    db, model, monkeypatch
):
    monkeypatch.setattr(settings, "DEFAULT_TIMEZONE", "Asia/Tbilisi")
    user = db.scalar(select(User).where(User.telegram_id == SASHA))
    user.settings = {"timezone": "America/New_York"}
    db.commit()
    wire = play(command(), words("суп 200 г"))
    shown = _copy_time(wire)
    row = _rows(db)[0]
    screen = feedback_inbox.view(db)
    assert f"в {shown}" in screen.text
    again = feedback_inbox.resend(db, row.id, 0, 0)
    assert shown in again.parts[0].html
    # The ledger's zone is not the answer, or this proves nothing: seven hours apart.
    ledger = feedback_inbox.when(row.created_at, feedback_inbox.zone())
    assert shown not in ledger


def test_the_ledger_screens_keep_the_ledgers_zone(monkeypatch):
    monkeypatch.setattr(settings, "DEFAULT_TIMEZONE", "Asia/Tbilisi")
    assert str(feedback_inbox.zone()) == "Asia/Tbilisi"


def test_a_question_at_the_food_button_is_food_with_a_meal_behind_the_confirmation(
    db, model
):
    body = "а почему суп не посчитался?"
    play(command(), words(body))
    row = _rows(db)[0]
    wire = play(tap(FOOD_DATA.format(row.id)))
    assert len(_meals(db)) == 1
    assert model.text == [body]
    assert _rows(db)[0].was_food_at is not None
    assert [p for p in wire.edits() if p["text"] == feedback_screen.FOOD_DONE]


def test_a_stop_word_at_the_food_button_is_food_too(db, model):
    play(command(), words("спасибо"))
    row = _rows(db)[0]
    play(tap(FOOD_DATA.format(row.id)))
    assert len(_meals(db)) == 1
    assert _rows(db)[0].was_food_at is not None


def test_the_same_question_written_to_the_diary_is_still_no_meal(db, model):
    # A plain message keeps its old meaning: the button alone says otherwise.
    play(words("а почему суп не посчитался?"), words("спасибо"))
    assert _meals(db) == []


def test_a_time_with_no_meal_is_not_confirmed_and_stays_the_owners_to_read(db, model):
    play(command(), words("14:30"))
    row = _rows(db)[0]
    wire = play(tap(FOOD_DATA.format(row.id)))
    assert _meals(db) == []
    assert _rows(db)[0].was_food_at is None
    assert wire.edits() == []
    assert wire.to(OWNER) == []
    answer = [p for n, p in wire.calls if n == "answerCallbackQuery"][0]
    assert answer["text"] == feedback_screen.FOOD_NOT_WRITTEN
    assert answer["show_alert"] is True
    assert crud_feedback.count_was_food(db, None) == 0
    # Asked again, the same words are no more written than they were.
    second = play(tap(FOOD_DATA.format(row.id)))
    assert _rows(db)[0].was_food_at is None
    assert second.edits() == []


def test_a_weight_at_the_food_button_is_written_and_confirmed(db, model):
    play(command(), words("70.5"))
    row = _rows(db)[0]
    play(tap(FOOD_DATA.format(row.id)))
    assert db.scalars(select(BodyMetric.weight)).all() == [70.5]
    assert _rows(db)[0].was_food_at is not None


def test_a_refused_replay_keeps_the_button_and_marks_nothing(db, model, monkeypatch):
    play(command(), words("суп 200 г"))
    row = _rows(db)[0]

    def broken(*_a, **_k):
        raise RuntimeError("no database")

    with monkeypatch.context() as patch:
        patch.setattr(tg.capture, "born", broken)
        wire = play(tap(FOOD_DATA.format(row.id)))
    assert _rows(db)[0].was_food_at is None
    assert wire.edits() == []
    assert wire.to(OWNER) == []
    answer = [p for n, p in wire.calls if n == "answerCallbackQuery"][0]
    assert answer["text"] == feedback_screen.FOOD_FAILED
    assert answer["show_alert"] is True
    # The button stayed, and on a working database it still saves the food.
    play(tap(FOOD_DATA.format(row.id)))
    assert len(_meals(db)) == 1
    assert _rows(db)[0].was_food_at is not None


def test_someone_elses_id_finds_nothing_moves_nothing_and_says_so(db, model, caplog):
    play(command(), words("суп 200 г"))
    row = _rows(db)[0]
    with caplog.at_level(logging.WARNING):
        wire = play(tap(FOOD_DATA.format(row.id), person=ZHENYA))
    assert "unknown or foreign" in caplog.text
    assert _meals(db) == [] and _meals(db, ZHENYA) == []
    assert _rows(db)[0].was_food_at is None
    answers = [p for n, p in wire.calls if n == "answerCallbackQuery"]
    assert answers[0]["text"] == feedback_screen.NOT_YOURS
    assert "суп" not in json.dumps(wire.calls, ensure_ascii=False)


def test_a_locked_person_gets_told_and_nothing_is_written(db, model):
    play(command(ZHENYA), words("суп 200 г", ZHENYA))
    row = _rows(db)[0]
    wire = play(tap(FOOD_DATA.format(row.id), person=ZHENYA))
    assert _meals(db, ZHENYA) == []
    assert _rows(db)[0].was_food_at is None
    answer = [p for n, p in wire.calls if n == "answerCallbackQuery"][0]
    assert answer["text"] == feedback_screen.FOOD_LOCKED


def test_the_owner_can_leave_feedback_too(db, model):
    wire = play(command(OWNER), words("сам проверяю", OWNER))
    row = _rows(db)[0]
    assert row.telegram_id == OWNER and row.sent_at is not None
    copies = [p for p in wire.to(OWNER) if "💬 Фидбэк ·" in p["text"]]
    assert len(copies) == 1
    assert "«сам проверяю»" in copies[0]["text"]
    follow = play(tap(FOOD_DATA.format(row.id), person=OWNER))
    assert len(_meals(db, OWNER)) == 1
    assert "оказалось едой" in follow.to(OWNER)[-1]["text"]


# --- what the journal says about it ---------------------------------------------------


def test_the_journal_keeps_no_word_of_it(db):
    play(command(), words("секретный суп"))
    rows = db.scalars(select(ActionEvent).where(ActionEvent.telegram_id == SASHA)).all()
    assert [r.kind for r in rows][-1] == TEXT
    assert "секретный" not in json.dumps([repr(r.label) for r in rows])
    assert "секретный" not in (_rows(db)[0].journal or "")


def test_the_buttons_name_reads_in_the_journal(db):
    assert action_journal.describe(BUTTON, "fb") == "👆 💬 Фидбэк"


# --- the command menu -----------------------------------------------------------------


def _start(wire) -> None:
    async def go():
        application = tg.create_bot_application()
        application.bot._request = (wire, wire)
        await application.initialize()
        await tg._set_commands(application)
        await application.shutdown()

    asyncio.run(go())


def test_the_command_menu_holds_the_daily_few():
    wire = Desk()
    _start(wire)
    sent = [p for n, p in wire.calls if n == "setMyCommands"]
    assert len(sent) == 1
    raw = sent[0]["commands"]
    commands = json.loads(raw) if isinstance(raw, str) else raw
    names = [c["command"] if isinstance(c, dict) else c.command for c in commands]
    assert names == ["start", "feedback"]


def test_a_refused_menu_is_logged_and_the_start_goes_on(monkeypatch, caplog):
    wire = Desk(lambda name, _p: "Bad Request: no" if name == "setMyCommands" else None)
    loaded = []

    async def emoji(_application):
        loaded.append(True)

    monkeypatch.setattr(tg, "_load_custom_emoji", emoji)

    async def go():
        application = tg.create_bot_application()
        application.bot._request = (wire, wire)
        await application.initialize()
        await tg._on_start(application)
        await tg._on_stop(application)
        await application.shutdown()

    with caplog.at_level(logging.WARNING):
        asyncio.run(go())
    assert "Command menu not set: BadRequest" in caplog.text
    assert loaded == [True]
