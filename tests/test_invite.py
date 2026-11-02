"""Inviting a person, closing the door, and their first run, through the real application.

Telegram is a fake wire and the model a fake reader: nothing here reaches either. What
Telegram's own picker and share window do on a real phone no test here can see.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from telegram import Update
from telegram.error import NetworkError

import app.services.access_control as access_control
import app.services.ai_call_log_service as log_service
import app.services.inbound_message_service as inbound_service
import app.services.model_selection as model_selection
import app.services.telegram as tg
from app.core.config import settings
from app.crud.crud_access_grant import crud_access_grant
from app.crud.crud_subscription import crud_subscription
from app.db.base import Base
from app.models import BodyMetric, InboundMessage, Meal, MealSource, User
from app.services import (
    capture,
    diet,
    invites,
    people_screen,
    week_push,
    welcome_screen,
)
from tests.test_capture_router import NOW_TS, NUTRITION, Wire, reader  # noqa: F401

OWNER, LISTED, SASHA, ZHENYA, MIKA = 1001, 2002, 7_000_000_003, 3003, 3004
_ids = iter(range(100, 10_000_000))
MAIN_LABELS = {b.text for row in tg.main_keyboard.keyboard for b in row}


class Phone(Wire):
    """Answers in the asked chat; a chat in ``refuses`` has never pressed Start."""

    def __init__(self, refuses=()):
        super().__init__()
        self.refuses = set(refuses)

    def _result(self, name, params):
        result = super()._result(name, params)
        if isinstance(result, dict) and "chat" in result and "chat_id" in params:
            result["chat"]["id"] = int(params["chat_id"])
        return result

    async def do_request(self, url, method, request_data=None, **kwargs):
        params = dict(request_data.parameters) if request_data else {}
        if (
            url.endswith("/sendMessage")
            and int(params.get("chat_id", 0)) in self.refuses
        ):
            self.calls.append(("refused", params))
            return (
                403,
                json.dumps(
                    {
                        "ok": False,
                        "error_code": 403,
                        "description": "Forbidden: bot can't initiate conversation",
                    }
                ).encode(),
            )
        return await super().do_request(url, method, request_data, **kwargs)

    def to(self, chat: int) -> list:
        return [
            p for n, p in self.calls if n == "sendMessage" and int(p["chat_id"]) == chat
        ]


@pytest.fixture
def Session(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    for module in (tg, inbound_service, log_service, model_selection, access_control):
        monkeypatch.setattr(module, "SessionLocal", factory)
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_IDS", str(OWNER))
    monkeypatch.setattr(settings, "BOT_ACCESS_MODE", "whitelist")
    monkeypatch.setattr(settings, "ALLOWED_TELEGRAM_IDS", str(LISTED))
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "123:fake")
    monkeypatch.setattr(tg, "_schedule_personal_food_save", lambda **_: None)
    return factory


@pytest.fixture
def db(Session):
    session = Session()
    yield session
    session.close()


def _as(person: int, **fields) -> dict:
    return {
        "update_id": next(_ids),
        "message": {
            "message_id": next(_ids),
            "date": NOW_TS,
            "chat": {"id": person, "type": "private"},
            "from": {"id": person, "is_bot": False, "first_name": "x"},
            **fields,
        },
    }


def say(person: int, body: str) -> dict:
    fields = {"text": body}
    if body.startswith("/"):
        length = len(body.split()[0])
        fields["entities"] = [{"type": "bot_command", "offset": 0, "length": length}]
    return _as(person, **fields)


def picked(person: int, chosen: int, request_id: int = 1, **names) -> dict:
    user = {"user_id": chosen, "first_name": "Саша", "username": "sasha_k", **names}
    return _as(person, users_shared={"request_id": request_id, "users": [user]})


def forward(person: int, origin: dict) -> dict:
    return _as(person, text="суп", forward_origin={"date": NOW_TS, **origin})


def from_user(chosen: int, is_bot: bool = False) -> dict:
    sender = {"id": chosen, "is_bot": is_bot, "first_name": "Женя"}
    return {"type": "user", "sender_user": sender}


def contact(person: int, chosen: int | None) -> dict:
    card = {"phone_number": "+10000000000", "first_name": "Мика"}
    if chosen is not None:
        card["user_id"] = chosen
    return _as(person, contact=card)


def tap(person: int, data: str, message_id: int = 4900) -> dict:
    return {
        "update_id": next(_ids),
        "callback_query": {
            "id": "1",
            "from": {"id": person, "is_bot": False, "first_name": "x"},
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


def play(*updates, refuses=(), wire=None) -> Phone:
    """All through one application, so a wait begun by one is seen by the next."""
    wire = wire or Phone(refuses)

    async def steps():
        application = tg.create_bot_application()
        application.bot._request = (wire, wire)
        await application.initialize()
        await application.start()
        try:
            for update in updates:
                await application.process_update(
                    Update.de_json(update, application.bot)
                )
                await asyncio.gather(*list(tg._meal_reads), return_exceptions=True)
        finally:
            await application.stop()
            await application.shutdown()

    asyncio.run(steps())
    return wire


def _markup(params: dict) -> dict:
    markup = params.get("reply_markup")
    return json.loads(markup) if isinstance(markup, str) else (markup or {})


def _keyboard_labels(params: dict) -> set:
    return {
        (b["text"] if isinstance(b, dict) else b)
        for row in _markup(params).get("keyboard", [])
        for b in row
    }


def _labels(params: dict) -> list:
    return [[b["text"] for b in row] for row in _markup(params)["inline_keyboard"]]


def _gives_main_keyboard(params: dict) -> bool:
    return _keyboard_labels(params) == MAIN_LABELS


def _fits(params: dict) -> None:
    rows = _labels(params)
    assert len(rows) <= 5
    assert all(len(row) <= 3 for row in rows)


def _invite(db, telegram_id: int = SASHA, name: str = "Саша") -> None:
    invites.invite(db, invites.Person(telegram_id, name), by=OWNER)


def _nothing_in_the_diary(db) -> None:
    db.expire_all()
    assert db.scalars(select(Meal.id)).all() == []
    assert db.scalars(select(InboundMessage.id)).all() == []


# --- the gate --------------------------------------------------------------------------


def test_a_grant_lets_in_only_in_whitelist_mode(db):
    _invite(db)
    assert access_control.is_user_allowed(SASHA) is True
    assert access_control.is_user_allowed(ZHENYA) is False  # a stranger stays one


def test_a_revoked_grant_lets_nobody_in(db):
    _invite(db)
    invites.close(db, SASHA)
    assert access_control.is_user_allowed(SASHA) is False


def test_closed_mode_is_not_opened_by_a_grant(db, monkeypatch):
    _invite(db)
    monkeypatch.setattr(settings, "BOT_ACCESS_MODE", "closed")
    assert access_control.is_user_allowed(SASHA) is False
    assert access_control.is_user_allowed(OWNER) is True


def test_open_mode_stays_open(db, monkeypatch):
    monkeypatch.setattr(settings, "BOT_ACCESS_MODE", "open")
    assert access_control.is_user_allowed(ZHENYA) is True


def test_the_settings_never_ask_the_database(Session, monkeypatch, caplog):
    def down():
        raise AssertionError("asked the database")

    monkeypatch.setattr(access_control, "SessionLocal", down)
    assert access_control.is_user_allowed(OWNER) is True
    assert access_control.is_user_allowed(LISTED) is True
    monkeypatch.setattr(settings, "BOT_ACCESS_MODE", "closed")
    assert access_control.is_user_allowed(ZHENYA) is False
    assert "Access grant" not in caplog.text


def test_a_database_failure_fails_closed_and_says_so(Session, monkeypatch, caplog):
    def down():
        raise ConnectionError("зюзя")

    monkeypatch.setattr(access_control, "SessionLocal", down)
    with caplog.at_level(logging.ERROR):
        assert access_control.is_user_allowed(SASHA) is False
    assert "Access grant" in caplog.text and "ConnectionError" in caplog.text
    assert "зюзя" not in caplog.text


def test_a_stranger_stays_silent(db):
    wire = play(say(ZHENYA, "/start"), say(ZHENYA, "суп"))
    assert wire.to(ZHENYA) == []
    _nothing_in_the_diary(db)


# --- /invite ---------------------------------------------------------------------------


def test_invite_offers_telegrams_picker(db):
    wire = play(say(OWNER, "/invite"))
    sent = wire.to(OWNER)[0]
    assert sent["text"] == "Нажми кнопку внизу и выбери человека. Отменить — ✖️ Отмена."
    pick, cancel = _markup(sent)["keyboard"]
    assert pick[0]["text"] == "👤 Выбрать человека"
    assert cancel[0]["text"] == "✖️ Отмена"
    assert pick[0]["request_users"] == {
        "request_id": people_screen.REQUEST_ID,
        "user_is_bot": False,
        "max_quantity": 1,
        "request_name": True,
        "request_username": True,
    }


def test_invite_refuses_anyone_but_the_owner(db):
    wire = play(say(LISTED, "/invite"))
    assert [p["text"] for p in wire.to(LISTED)] == [
        "⛔️ Эта команда доступна только владельцу."
    ]


def test_a_person_picked_is_let_in(db, reader):  # noqa: F811
    wire = play(picked(OWNER, SASHA), refuses=[SASHA])
    notice, answer = wire.to(OWNER)
    assert notice["text"] == "<i>✓ Саша: доступ открыт.</i>"
    assert _gives_main_keyboard(notice)
    assert answer["text"] == (
        "Кнопка откроет чат с этим человеком и готовым текстом. Останется отправить. "
        "Ссылка работает только у этого человека: у других бот молчит."
    )
    assert _labels(answer) == [
        ["📤 Написать: Саша"],
        ["3 мес", "6 мес", "✓ 12 мес"],
        ["⬅️ Пользователи"],
    ]
    _fits(answer)
    grant = crud_access_grant.get(db, SASHA)
    assert (grant.display_name, grant.username) == ("Саша", "sasha_k")
    assert grant.granted_by == OWNER and grant.first_opened_at is None
    assert access_control.is_user_allowed(SASHA) is True
    # The door is the id and nothing else: the link holds no token.
    button = _markup(answer)["inline_keyboard"][0][0]
    assert button["text"] == "📤 Написать: Саша"
    url = button["url"]
    assert url.startswith("https://t.me/sasha_k?text=") and "+" not in url
    text = parse_qs(urlparse(url).query)["text"][0]
    assert text.startswith("Привет! Попробуй мой дневник питания")
    assert text.endswith(": t.me/b") and text.count("t.me") == 1
    assert reader.text == [] and reader.image == []
    _nothing_in_the_diary(db)


def test_a_person_with_no_username_gets_the_chat_picker_and_one_link(db):
    wire = play(picked(OWNER, SASHA, username=None), refuses=[SASHA])
    answer = wire.to(OWNER)[1]
    assert "Кнопка предложит выбрать чат" in answer["text"]
    button = _markup(answer)["inline_keyboard"][0][0]
    assert button["text"] == "📤 Отправить приглашение"
    query = parse_qs(urlparse(button["url"]).query)
    assert button["url"].startswith("https://t.me/share/url?")
    assert query["url"] == ["https://t.me/b"]
    # Telegram prints ``url`` above the text: the text does not carry it a second time.
    assert "t.me" not in query["text"][0]


def _months_left(db, person: int = SASHA) -> int:
    db.expire_all()
    sub = crud_subscription.get_by_telegram_id(db, person)
    assert sub.is_active
    return round((sub.end_date - datetime.now()).days / 30.4)


def test_a_picked_person_gets_twelve_months_and_the_words_say_so(db):
    wire = play(picked(OWNER, SASHA), refuses=[SASHA])
    assert _months_left(db) == 12
    answer = wire.to(OWNER)[1]
    assert _labels(answer)[1] == ["3 мес", "6 мес", "✓ 12 мес"]
    url = _markup(answer)["inline_keyboard"][0][0]["url"]
    assert "на 12 месяцев" in parse_qs(urlparse(url).query)["text"][0]


def test_the_term_changes_the_subscription_and_the_words_before_sending(db):
    wire = play(picked(OWNER, SASHA), refuses=[SASHA])
    tapped = _markup(wire.to(OWNER)[1])["inline_keyboard"][1][0]["callback_data"]
    wire = play(tap(OWNER, tapped))
    assert _months_left(db) == 3
    edit = wire.edits()[-1]
    assert _labels(edit)[1] == ["✓ 3 мес", "6 мес", "12 мес"]
    url = _markup(edit)["inline_keyboard"][0][0]["url"]
    words = parse_qs(urlparse(url).query)["text"][0]
    assert "на 3 месяца" in words and "12" not in words
    assert url.startswith("https://t.me/sasha_k?")


def test_the_card_shows_the_date_and_sets_a_new_term(db):
    play(picked(OWNER, SASHA))
    wire = play(tap(OWNER, f"usr:p:{SASHA}"))
    card = wire.edits()[-1]
    assert "🗓 Подписка до " in card["text"]
    assert ["3 мес", "6 мес", "12 мес"] in _labels(card)
    wire = play(tap(OWNER, f"usr:tc:{SASHA}:6"))
    assert "✓ Срок: 6 месяцев, считая от сегодня." in wire.edits()[-1]["text"]
    assert _months_left(db) == 6


def test_no_term_for_someone_outside_or_a_term_the_buttons_do_not_offer(db):
    play(picked(OWNER, SASHA))
    play(tap(OWNER, f"usr:tc:{SASHA}:99"))
    assert _months_left(db) == 12
    play(tap(OWNER, f"usr:x:{SASHA}"))
    play(tap(OWNER, f"usr:tc:{SASHA}:3"))
    db.expire_all()
    assert crud_subscription.get_by_telegram_id(db, SASHA).is_active is False


def test_nobody_else_can_change_a_term(db):
    play(picked(OWNER, SASHA))
    play(tap(SASHA, f"usr:tc:{SASHA}:3"))
    play(tap(SASHA, f"usr:ti:{SASHA}:3"))
    assert _months_left(db) == 12


def test_someone_who_opened_the_bot_gets_the_invitation_directly(db):
    wire = play(picked(OWNER, SASHA))
    assert [p["text"] for p in wire.to(SASHA)] == [
        "👋 Тебя пригласили в дневник питания на 12 месяцев. Нажми /start, чтобы начать."
    ]
    answer = wire.to(OWNER)[1]
    assert _labels(answer) == [["👤 Саша"], ["⬅️ Пользователи"]]
    assert "отправил сам" in answer["text"]


def test_a_refused_invitation_is_normal_and_leaves_the_share(db, caplog):
    with caplog.at_level(logging.INFO):
        wire = play(picked(OWNER, SASHA), refuses=[SASHA])
    assert wire.to(SASHA) == []
    assert ("refused" in [n for n, _ in wire.calls]) is True
    assert "Invitation not delivered: Forbidden" in caplog.text
    assert _labels(wire.to(OWNER)[1])[0] == ["📤 Написать: Саша"]


def test_closed_mode_says_plainly_the_grant_opens_nothing(db, monkeypatch):
    monkeypatch.setattr(settings, "BOT_ACCESS_MODE", "closed")
    wire = play(picked(OWNER, SASHA))
    answer = wire.to(OWNER)[1]
    assert "Приглашение его не откроет" in answer["text"]
    assert _labels(answer) == [["⬅️ Пользователи"]]
    assert wire.to(SASHA) == []
    assert crud_access_grant.is_active(db, SASHA) is True


def test_open_mode_does_not_claim_the_link_is_personal(db, monkeypatch):
    monkeypatch.setattr(settings, "BOT_ACCESS_MODE", "open")
    wire = play(picked(OWNER, SASHA), refuses=[SASHA])
    assert "открыт для всех" in wire.to(OWNER)[1]["text"]
    assert "у других бот молчит" not in wire.to(OWNER)[1]["text"]


def test_a_name_is_escaped_where_html_shows_it(db):
    wire = play(picked(OWNER, SASHA, first_name="<b>Я&Ко"))
    assert wire.to(OWNER)[0]["text"] == "<i>✓ &lt;b&gt;Я&amp;Ко: доступ открыт.</i>"
    hub = play(say(OWNER, "/users")).to(OWNER)[0]
    assert "&lt;b&gt;Я&amp;Ко" in hub["text"]


def test_a_nameless_person_is_still_named():
    assert invites.Person(SASHA).name == str(SASHA)
    assert invites.Person(SASHA, username="sasha_k").name == "sasha_k"
    assert invites.Person(SASHA, "Саша", "К").name == "Саша К"


def test_someone_elses_pick_gets_nothing_and_makes_nothing(db, reader):  # noqa: F811
    wire = play(picked(LISTED, SASHA))
    assert wire.to(LISTED) == [] and wire.to(SASHA) == []
    assert crud_access_grant.get(db, SASHA) is None
    assert crud_subscription.is_active_subscription(db, SASHA) is False
    _nothing_in_the_diary(db)


def test_a_pick_for_another_request_opens_nothing(db, caplog):
    with caplog.at_level(logging.WARNING):
        wire = play(picked(OWNER, SASHA, request_id=9))
    assert crud_access_grant.get(db, SASHA) is None
    assert "unknown request" in caplog.text
    assert _gives_main_keyboard(wire.to(OWNER)[0])


def test_picking_yourself_opens_nothing_and_gives_the_menu_back(db):
    wire = play(picked(OWNER, OWNER))
    assert wire.to(OWNER)[0]["text"] == people_screen.YOURSELF
    assert _gives_main_keyboard(wire.to(OWNER)[0])
    assert crud_access_grant.get(db, OWNER) is None


def test_a_failed_write_says_so_and_gives_the_menu_back(db, monkeypatch, caplog):
    monkeypatch.setattr(crud_subscription, "create_subscription", lambda *_, **__: None)
    with caplog.at_level(logging.ERROR):
        wire = play(picked(OWNER, SASHA))
    only = wire.to(OWNER)
    assert [p["text"] for p in only] == [people_screen.FAILED]
    assert _gives_main_keyboard(only[0])
    assert crud_access_grant.get(db, SASHA) is None  # no pass into a padlock
    assert "Invitation not written: GrantFailed" in caplog.text


@pytest.mark.parametrize("person", [OWNER, LISTED])
def test_the_pickers_labels_never_reach_the_diary(db, reader, person):  # noqa: F811
    wire = play(say(person, "✖️ Отмена"), say(person, "👤 Выбрать человека"))
    assert reader.text == []
    _nothing_in_the_diary(db)
    # The owner's answer gives the menu back; anyone else's tap is nobody's.
    if person == OWNER:
        assert wire.to(OWNER)[0]["text"] == people_screen.CANCELLED
        assert _gives_main_keyboard(wire.to(OWNER)[0])
    else:
        assert wire.to(person) == []


def test_cancel_ends_the_wait(db):
    wire = play(
        say(OWNER, "/invite"), say(OWNER, "✖️ Отмена"), forward(OWNER, from_user(ZHENYA))
    )
    assert _gives_main_keyboard(wire.to(OWNER)[1])
    assert crud_access_grant.get(db, ZHENYA) is None


def test_food_during_the_wait_is_food(db, reader):  # noqa: F811
    play(say(OWNER, "/invite"), say(OWNER, "суп"))
    assert reader.text == ["суп"]


def test_a_command_during_the_wait_ends_it_and_gives_the_menu_back(db):
    wire = play(say(OWNER, "/invite"), say(OWNER, "/users"))
    assert any(_gives_main_keyboard(p) for p in wire.to(OWNER)[1:])
    assert any("Пользователи" in p["text"] for p in wire.to(OWNER)[1:])


# --- the way without the picker --------------------------------------------------------


def test_a_client_that_cannot_pick_is_told_the_other_way(db):
    wire = play(say(OWNER, "/invite"), say(OWNER, "👤 Выбрать человека"))
    assert wire.to(OWNER)[1]["text"] == people_screen.FALLBACK


def test_a_forward_lets_in(db, reader):  # noqa: F811
    wire = play(
        say(OWNER, "/invite"), forward(OWNER, from_user(ZHENYA)), refuses=[ZHENYA]
    )
    assert crud_access_grant.get(db, ZHENYA).display_name == "Женя"
    assert wire.to(OWNER)[1]["text"] == "<i>✓ Женя: доступ открыт.</i>"
    assert _gives_main_keyboard(wire.to(OWNER)[1])
    assert reader.text == []
    _nothing_in_the_diary(db)


def test_a_contact_lets_in(db):
    play(say(OWNER, "/invite"), contact(OWNER, MIKA), refuses=[MIKA])
    assert crud_access_grant.get(db, MIKA).display_name == "Мика"


def test_a_hidden_forward_is_refused_and_the_wait_goes_on(db, reader):  # noqa: F811
    hidden = {"type": "hidden_user", "sender_user_name": "Кто-то"}
    wire = play(
        say(OWNER, "/invite"),
        forward(OWNER, hidden),
        contact(OWNER, MIKA),
        refuses=[MIKA],
    )
    assert wire.to(OWNER)[1]["text"] == people_screen.HIDDEN_ORIGIN
    assert "скрыл" in people_screen.HIDDEN_ORIGIN
    assert reader.text == []
    assert crud_access_grant.get(db, MIKA) is not None


def test_a_contact_not_on_telegram_is_refused(db):
    wire = play(say(OWNER, "/invite"), contact(OWNER, None))
    assert wire.to(OWNER)[1]["text"] == people_screen.HIDDEN_ORIGIN
    assert crud_access_grant.everyone(db) == []


def test_a_forward_from_a_bot_is_refused(db):
    wire = play(say(OWNER, "/invite"), forward(OWNER, from_user(ZHENYA, is_bot=True)))
    assert wire.to(OWNER)[1]["text"] == people_screen.A_BOT
    assert crud_access_grant.get(db, ZHENYA) is None


def test_with_no_wait_a_forward_is_just_a_message(db, reader):  # noqa: F811
    play(forward(OWNER, from_user(ZHENYA)))
    assert crud_access_grant.get(db, ZHENYA) is None
    assert reader.text == ["суп"]


def test_a_wait_runs_out_and_says_so(db, reader, monkeypatch):  # noqa: F811
    monkeypatch.setattr(tg, "INVITE_WAIT_SECONDS", -1)
    wire = play(say(OWNER, "/invite"), forward(OWNER, from_user(ZHENYA)))
    assert wire.to(OWNER)[1]["text"] == people_screen.EXPIRED
    assert _gives_main_keyboard(wire.to(OWNER)[1])
    assert crud_access_grant.get(db, ZHENYA) is None
    assert reader.text == ["суп"]  # it was a message after all


def test_a_forward_by_someone_else_invites_no_one(db, reader):  # noqa: F811
    play(say(OWNER, "/invite"), forward(LISTED, from_user(ZHENYA)))
    assert crud_access_grant.get(db, ZHENYA) is None


# --- closing the door ------------------------------------------------------------------


def test_closing_reports_each_result(db):
    assert invites.close(db, ZHENYA) is invites.Closed.UNKNOWN
    _invite(db)
    assert invites.close(db, SASHA) is invites.Closed.CLOSED
    assert invites.close(db, SASHA) is invites.Closed.ALREADY
    assert invites.close(db, LISTED) is invites.Closed.IN_SETTINGS
    assert invites.close(db, OWNER) is invites.Closed.IN_SETTINGS
    assert invites.reopen(db, ZHENYA, OWNER) is invites.Reopened.UNKNOWN


def test_closed_person_is_stopped_and_their_records_stay(db, reader):  # noqa: F811
    _invite(db)
    play(say(SASHA, "/start"), say(SASHA, "суп"))
    meals = db.scalars(select(Meal.id)).all()
    assert meals

    wire = play(tap(OWNER, f"usr:x:{SASHA}"), say(SASHA, "ещё суп"))
    shown = wire.edits()[0]
    assert shown["text"].startswith("<i>✓ Саша: доступ закрыт. Записи сохранены.</i>")
    assert _labels(shown) == [["↩️ Вернуть"], ["⬅️ Пользователи"]]
    assert wire.to(SASHA) == []  # stopped, and silently
    assert len(reader.text) == 1
    db.expire_all()
    assert db.scalars(select(Meal.id)).all() == meals
    assert crud_subscription.is_active_subscription(db, SASHA) is False


def test_undo_opens_the_door_again(db, reader):  # noqa: F811
    _invite(db)
    wire = play(
        tap(OWNER, f"usr:x:{SASHA}"), tap(OWNER, f"usr:u:{SASHA}"), say(SASHA, "суп")
    )
    shown = wire.edits()[1]
    assert shown["text"].startswith("<i>✓ Саша: доступ снова открыт.</i>")
    assert _labels(shown) == [
        ["⛔ Закрыть доступ"],
        ["3 мес", "6 мес", "12 мес"],
        ["⬅️ Пользователи"],
    ]
    assert reader.text == ["суп"]


def test_grant_sub_opens_the_door_and_puts_the_person_on_the_list(
    db, reader
):  # noqa: F811
    """Two people given /grant_sub met a silent bot and were missing from /users."""
    assert access_control.is_user_allowed(SASHA) is False
    play(say(OWNER, f"/grant_sub {SASHA} 6"), say(SASHA, "суп"))
    assert access_control.is_user_allowed(SASHA) is True
    assert _months_left(db) == 6
    assert reader.text == ["суп"]
    assert str(SASHA) in people_screen.hub(db).text


def test_inviting_again_reactivates(db):
    _invite(db)
    invites.close(db, SASHA)
    play(picked(OWNER, SASHA), refuses=[SASHA])
    db.expire_all()
    assert access_control.is_user_allowed(SASHA) is True
    assert crud_subscription.is_active_subscription(db, SASHA) is True


def test_someone_in_the_settings_cannot_be_closed_by_a_button(db):
    _invite(db, LISTED, "Женя")
    wire = play(tap(OWNER, f"usr:p:{LISTED}"), tap(OWNER, f"usr:x:{LISTED}"))
    card, refused = wire.edits()
    assert "в настройках сервера" in card["text"]
    assert _labels(card) == [["⬅️ Пользователи"]]
    assert "в настройках сервера" in refused["text"]
    db.expire_all()
    assert crud_access_grant.is_active(db, LISTED) is True
    assert access_control.is_user_allowed(LISTED) is True


def test_stale_taps_lead_to_the_current_screen(db, caplog):
    with caplog.at_level(logging.WARNING):
        wire = play(tap(OWNER, f"usr:x:{ZHENYA}"), tap(OWNER, "usr:zzz"))
    assert all("Пользователи" in edit["text"] for edit in wire.edits())
    assert len(wire.edits()) == 2
    assert "unknown or foreign" in caplog.text


def test_a_tap_on_closed_again_is_not_a_crash(db):
    _invite(db)
    wire = play(tap(OWNER, f"usr:x:{SASHA}"), tap(OWNER, f"usr:x:{SASHA}"))
    assert _labels(wire.edits()[1])[0] == ["↩️ Вернуть"]


def test_the_owners_screens_refuse_someone_else(db, caplog):
    _invite(db)
    crud_subscription.create_subscription(db, LISTED, OWNER, months=1)
    with caplog.at_level(logging.WARNING):
        wire = play(tap(LISTED, f"usr:x:{SASHA}"), tap(LISTED, people_screen.INVITE))
    assert wire.edits() == [] and wire.to(LISTED) == []
    assert "unknown or foreign" in caplog.text
    assert crud_access_grant.is_active(db, SASHA) is True
    # /users answers nothing at all; the owner's same command is the known positive.
    assert play(say(LISTED, "/users")).to(LISTED) == []
    assert "<b>👥 Пользователи</b>" in play(say(OWNER, "/users")).to(OWNER)[0]["text"]


def test_the_hub_shows_who_and_how(db):
    _invite(db, SASHA, "Саша")
    _invite(db, ZHENYA, "Женя")
    _invite(db, MIKA, "Мика")
    crud_access_grant.mark_opened(db, SASHA)
    invites.close(db, MIKA)
    hub = play(say(OWNER, "/users")).to(OWNER)[0]
    assert "<b>👥 Пользователи</b>" in hub["text"]
    assert "Саша · нет действий · 0 из 7 дней · $0.00" in hub["text"]
    assert "Женя · ⏳ бот ещё не открыт" in hub["text"]
    assert "Мика · ⛔ доступ закрыт" in hub["text"]
    labels = _labels(hub)
    assert sorted(labels[0] + labels[1]) == sorted(["Саша", "Женя", "Мика", str(OWNER)])
    assert labels[-1] == ["➕ Пригласить"]
    _fits(hub)


def test_with_no_one_invited_the_hub_holds_the_owner_and_invites(db):
    wire = play(say(OWNER, "/users"), tap(OWNER, people_screen.INVITE))
    assert wire.to(OWNER)[1]["text"] == people_screen.PROMPT  # ➕ is the same flow
    assert _labels(wire.to(OWNER)[0]) == [[str(OWNER)], ["➕ Пригласить"]]


def test_the_hub_pages_within_the_row_limits(db):
    for n in range(8):
        _invite(db, 5000 + n, f"Человек с очень длинным именем {n}")
    wire = play(say(OWNER, "/users"), tap(OWNER, "usr:l:1"))
    first, second = wire.to(OWNER)[0], wire.edits()[0]
    _fits(first), _fits(second)
    assert _labels(first)[-2:] == [["‹", "1/2", "›"], ["➕ Пригласить"]]
    assert _labels(second)[-2:] == [["‹", "2/2", "›"], ["➕ Пригласить"]]
    assert all(len(label) <= 12 for label in _labels(first)[0])


def test_the_hub_says_when_closed_mode_makes_grants_moot(db, monkeypatch):
    monkeypatch.setattr(settings, "BOT_ACCESS_MODE", "closed")
    assert "закрыт для всех" in play(say(OWNER, "/users")).to(OWNER)[0]["text"]


# --- what the bot does unasked ---------------------------------------------------------


def _user_with_a_failed_meal(db) -> Meal:
    _invite(db)
    user = db.scalar(select(User).where(User.telegram_id == SASHA))
    meal = Meal(
        user_id=user.id,
        meal_type="snack",
        description="суп",
        calories=0,
        proteins=0,
        fats=0,
        carbohydrates=0,
        meal_time=datetime.now(UTC),
        sent_at=datetime.now(UTC),
        reading_status="failed",
    )
    db.add(meal)
    db.flush()
    db.add(
        MealSource(
            user_id=user.id,
            meal_id=meal.id,
            kind="text",
            text="суп",
            chat_id=SASHA,
            message_id=10,
            sent_at=datetime.now(UTC),
        )
    )
    db.commit()
    return meal


def test_no_read_by_itself_for_someone_closed(db):
    meal = _user_with_a_failed_meal(db)
    assert capture._may_read_by_itself(db, meal) is True  # the control
    invites.close(db, SASHA)
    assert capture._may_read_by_itself(db, meal) is False
    assert capture.owner_may_receive(db, meal.id) is False


def _sweep_a_stuck_meal(db, close: bool) -> Phone:
    meal = _user_with_a_failed_meal(db)
    meal.reading_status = "pending"
    meal.created_at = datetime.now(UTC) - timedelta(days=1)
    db.commit()
    capture.map_message(db, SASHA, 77, meal.id, "pending")
    if close:
        invites.close(db, SASHA)
    wire = Phone()

    async def steps():
        application = tg.create_bot_application()
        application.bot._request = (wire, wire)
        await application.initialize()
        await tg._sweep(application)
        await application.shutdown()

    asyncio.run(steps())
    db.expire_all()
    assert db.get(Meal, meal.id).reading_status != "pending"  # given up on all the same
    return wire


def test_the_sweep_says_nothing_to_someone_closed(db, reader):  # noqa: F811
    wire = _sweep_a_stuck_meal(db, close=True)
    assert wire.edits() == [] and wire.sent() == []
    assert reader.text == []


def test_the_sweep_still_tells_someone_with_the_door_open(db, reader):  # noqa: F811
    wire = _sweep_a_stuck_meal(db, close=False)
    assert [p["message_id"] for p in wire.edits()] == [77]


def test_reprocess_spends_nothing_on_someone_closed(db, reader):  # noqa: F811
    _user_with_a_failed_meal(db)
    invites.close(db, SASHA)
    wire = play(say(OWNER, "/reprocess"))
    assert reader.text == []
    assert wire.to(SASHA) == [] and wire.edits() == []


def test_reprocess_still_reads_someone_with_the_door_open(db, reader):  # noqa: F811
    _user_with_a_failed_meal(db)
    play(say(OWNER, "/reprocess"))
    assert reader.text == ["суп"]


def test_the_week_push_stops_for_someone_closed(db):
    _invite(db)
    user = db.scalar(select(User).where(User.telegram_id == SASHA))
    assert week_push._may_receive(db, user) is True  # the control
    invites.close(db, SASHA)
    assert week_push._may_receive(db, user) is False


def test_the_week_push_stops_in_open_mode_too(db, monkeypatch):
    monkeypatch.setattr(settings, "BOT_ACCESS_MODE", "open")
    _invite(db)
    invites.close(db, SASHA)
    user = db.scalar(select(User).where(User.telegram_id == SASHA))
    assert week_push._may_receive(db, user) is False


# --- the first open --------------------------------------------------------------------


def test_the_first_start_tells_the_owner_once_and_quietly(db):
    _invite(db)
    wire = play(say(SASHA, "/start"), say(SASHA, "/start"))
    told = wire.to(OWNER)
    assert [p["text"] for p in told] == ["👋 Саша теперь в боте."]
    assert told[0]["disable_notification"] is True
    assert crud_access_grant.get(db, SASHA).first_opened_at is not None


def test_a_start_with_no_grant_tells_no_one(db):
    wire = play(say(LISTED, "/start"))
    assert wire.to(OWNER) == []


def test_a_failure_to_tell_only_goes_in_the_log(db, caplog):
    _invite(db)
    with caplog.at_level(logging.WARNING):
        wire = play(say(SASHA, "/start"), refuses=[OWNER])
    assert "First open not told to the owner: Forbidden" in caplog.text
    assert "Дневник питания" in wire.to(SASHA)[0]["text"]


def test_a_broken_record_of_the_first_open_costs_no_start(db, monkeypatch, caplog):
    _invite(db)

    def broken(*_args):
        raise ConnectionError

    monkeypatch.setattr(crud_access_grant, "mark_opened", broken)
    with caplog.at_level(logging.ERROR):
        wire = play(say(SASHA, "/start"))
    assert "First open not recorded: ConnectionError" in caplog.text
    assert "Дневник питания" in wire.to(SASHA)[0]["text"]


# --- the first run ---------------------------------------------------------------------


def test_the_first_start_is_the_welcome(db):
    _invite(db)
    sent = play(say(SASHA, "/start")).to(SASHA)
    assert len(sent) == 1
    assert sent[0]["text"].startswith("<b>👋 Дневник питания</b>\n\n")
    assert (
        "Владелец бота видит, как часто ты пользуешься ботом и сколько стоят запросы."
        in sent[0]["text"]
    )
    assert sent[0]["text"].endswith(diet.QUESTION)
    assert _labels(sent[0]) == [["🍖 Всё", "🥚 Без мяса", "🌱 Растения"], ["Позже"]]
    _fits(sent[0])
    assert "подписк" not in sent[0]["text"].casefold()


@pytest.mark.parametrize(
    "data, answer",
    [("wl:eat:all", "all"), ("wl:eat:plants", "plants"), ("wl:later", None)],
)
def test_an_answer_brings_the_first_record_and_the_menu(db, data, answer):
    _invite(db)
    wire = play(say(SASHA, "/start"), tap(SASHA, data))
    second = wire.to(SASHA)[1]
    assert second["text"] == welcome_screen.FIRST_RECORD
    assert second["text"].startswith("<b>✍️ Первая запись</b>")
    assert _gives_main_keyboard(second)
    assert "editMessageReplyMarkup" in [n for n, _ in wire.calls]  # the buttons went
    db.expire_all()
    user = db.scalar(select(User).where(User.telegram_id == SASHA))
    assert diet.chosen(user.settings) == answer


def test_a_second_tap_changes_the_answer_and_brings_no_second_screen(db):
    _invite(db)
    wire = play(
        say(SASHA, "/start"), tap(SASHA, "wl:eat:all"), tap(SASHA, "wl:eat:plants")
    )
    assert len(wire.to(SASHA)) == 2
    db.expire_all()
    user = db.scalar(select(User).where(User.telegram_id == SASHA))
    assert diet.chosen(user.settings) == "plants"


def test_a_damaged_answer_is_not_saved_and_not_a_crash(db, caplog):
    _invite(db)
    with caplog.at_level(logging.WARNING):
        wire = play(say(SASHA, "/start"), tap(SASHA, "wl:eat:зюзя"), tap(SASHA, "wl:x"))
    assert len(wire.to(SASHA)) == 1
    assert "unknown answer" in caplog.text and "unknown callback" in caplog.text


def test_a_second_start_opens_the_menu(db):
    _invite(db)
    wire = play(say(SASHA, "/start"), say(SASHA, "/start"))
    menu = wire.to(SASHA)[1]
    assert menu["text"] == "Что делаем?"
    assert _gives_main_keyboard(menu)


def test_the_old_words_are_gone_for_someone_active(db):
    _invite(db)
    words = [p["text"] for p in play(say(SASHA, "/start"), say(SASHA, "/start")).sent()]
    assert not any("подписк" in w.casefold() for w in words)


def test_the_owner_never_sees_the_welcome(db):
    menu = play(say(OWNER, "/start")).to(OWNER)[0]
    assert menu["text"] == "Что делаем?"


@pytest.mark.parametrize("kind", ["meal", "weight"])
def test_someone_already_in_the_bot_never_sees_it(db, kind):
    _invite(db)
    user = db.scalar(select(User).where(User.telegram_id == SASHA))
    if kind == "meal":
        db.add(
            Meal(
                user_id=user.id,
                meal_type="snack",
                description="суп",
                calories=0,
                proteins=0,
                fats=0,
                carbohydrates=0,
                meal_time=datetime.now(UTC),
                created_at=_BEFORE,
            )
        )
    else:
        db.add(
            BodyMetric(
                user_id=user.id,
                weight=70.0,
                timestamp=datetime.now(UTC),
                created_at=_BEFORE,
            )
        )
    db.commit()
    menu = play(say(SASHA, "/start")).to(SASHA)[0]
    assert menu["text"] == "Что делаем?"


def test_a_listed_person_without_records_does_see_it(db):
    crud_subscription.create_subscription(db, LISTED, OWNER, months=1)
    assert "Дневник питания" in play(say(LISTED, "/start")).to(LISTED)[0]["text"]


_BEFORE = datetime.now(UTC) - timedelta(days=30)


def _old_meal(user: User) -> Meal:
    return Meal(
        user_id=user.id,
        meal_type="snack",
        description="суп",
        calories=0,
        proteins=0,
        fats=0,
        carbohydrates=0,
        meal_time=_BEFORE,
        created_at=_BEFORE,
    )


def _welcomes(wire: Phone) -> list:
    return [p for p in wire.to(SASHA) if "Владелец бота видит" in p["text"]]


def test_someone_invited_whose_first_word_is_food_still_learns_who_sees_what(
    db,
    reader,  # noqa: F811
):
    _invite(db)
    wire = play(say(SASHA, "омлет"), say(SASHA, "кофе"))
    assert len(_welcomes(wire)) == 1
    assert _labels(_welcomes(wire)[0])  # the question is there to answer
    db.expire_all()
    assert len(db.scalars(select(Meal.id)).all()) == 2  # the food went on as ever
    assert reader.text == ["омлет", "кофе"]
    hints = [p for p in wire.to(SASHA) if p["text"] == welcome_screen.HINT]
    assert len(hints) == 1 and _gives_main_keyboard(hints[0])
    told = [p for p in wire.to(OWNER) if "Саша" in p["text"] or str(SASHA) in p["text"]]
    assert len(told) == 1  # the first open, as if /start had been pressed


def test_a_welcome_telegram_did_not_take_is_still_owed(db, reader):  # noqa: F811
    _invite(db)

    class NoWelcome(Phone):
        refusing = True

        async def do_request(self, url, method, request_data=None, **kwargs):
            params = dict(request_data.parameters) if request_data else {}
            if self.refusing and "Владелец бота видит" in params.get("text", ""):
                body = {"ok": False, "error_code": 400, "description": "Bad Request"}
                return 400, json.dumps(body).encode()
            return await super().do_request(url, method, request_data, **kwargs)

    wire = NoWelcome()
    play(say(SASHA, "омлет"), wire=wire)
    db.expire_all()
    assert len(db.scalars(select(Meal.id)).all()) == 1  # it cost the meal nothing
    assert _welcomes(wire) == []
    wire.refusing = False
    play(say(SASHA, "/start"), wire=wire)
    assert len(_welcomes(wire)) == 1


def test_a_record_from_before_the_invitation_means_they_were_here(
    db,
    reader,  # noqa: F811
):
    _invite(db)
    user = db.scalar(select(User).where(User.telegram_id == SASHA))
    db.add(_old_meal(user))
    db.commit()
    wire = play(say(SASHA, "омлет"), say(SASHA, "/start"))
    assert _welcomes(wire) == []


def test_a_listed_person_who_writes_first_is_as_before(db, reader):  # noqa: F811
    crud_subscription.create_subscription(db, LISTED, OWNER, months=1)
    wire = play(say(LISTED, "омлет"))
    assert not [p for p in wire.to(LISTED) if "Владелец бота видит" in p["text"]]
    assert reader.text == ["омлет"]  # known positive: the message did reach the diary


def _menus(wire: Phone) -> list:
    return [p for p in wire.to(SASHA) if p["text"] == welcome_screen.MENU]


@pytest.mark.parametrize("trouble", ["failed", "time"])
def test_who_skips_the_question_gets_the_menu_once_when_nothing_recorded(
    db,
    reader,  # noqa: F811
    trouble,
):
    _invite(db)
    if trouble == "failed":
        reader.error = ValueError("зюзя")
        first, second = "омлет", "кофе"
    else:
        first, second = "14:30", "15:30"
    wire = play(say(SASHA, "/start"), say(SASHA, first), say(SASHA, second))
    assert len(_menus(wire)) == 1
    assert _gives_main_keyboard(_menus(wire)[0])
    assert not [p for p in wire.to(SASHA) if p["text"] == welcome_screen.HINT]


def test_the_menu_does_not_cost_the_hint_or_a_late_answer(db, reader):  # noqa: F811
    _invite(db)
    wire = play(say(SASHA, "/start"), say(SASHA, "14:30"), say(SASHA, "омлет"))
    assert len(_menus(wire)) == 1
    assert len([p for p in wire.to(SASHA) if p["text"] == welcome_screen.HINT]) == 1
    late = play(tap(SASHA, "wl:eat:plants"))
    assert late.to(SASHA) == []
    db.expire_all()
    user = db.scalar(select(User).where(User.telegram_id == SASHA))
    assert diet.chosen(user.settings) == "plants"


def test_no_menu_for_someone_who_answered_or_a_message_that_says_nothing(
    db,
    reader,  # noqa: F811
):
    _invite(db)
    wire = play(say(SASHA, "/start"), say(SASHA, "спасибо"))
    assert _menus(wire) == []  # silent stays silent
    answered = play(tap(SASHA, "wl:later"), say(SASHA, "14:30"))
    assert _menus(answered) == []


# --- /reprocess and the door -------------------------------------------------------------


def _old_row(db) -> InboundMessage:
    row = InboundMessage(
        telegram_id=SASHA, kind="text", content="борщ", status="failed"
    )
    db.add(row)
    db.commit()
    return row


def test_reprocess_pays_nothing_for_someone_closed_and_keeps_the_row(
    db,
    reader,  # noqa: F811
):
    _invite(db)
    row = _old_row(db)
    invites.close(db, SASHA)
    wire = play(say(OWNER, "/reprocess"))
    assert reader.text == []
    db.expire_all()
    assert db.get(InboundMessage, row.id).status == "failed"
    assert wire.to(SASHA) == []
    assert "Нет сообщений" in wire.to(OWNER)[-1]["text"]
    # «↩️ Вернуть»: the row is there, and now it goes.
    invites.reopen(db, SASHA, OWNER)
    play(say(OWNER, "/reprocess"))
    assert reader.text == ["борщ"]


def test_reprocess_replays_the_row_of_someone_open(db, reader):  # noqa: F811
    _invite(db)
    row = _old_row(db)
    play(say(OWNER, "/reprocess"))
    assert reader.text == ["борщ"]
    db.expire_all()
    assert db.get(InboundMessage, row.id).status == "analyzed"


# --- a read that ends behind a closed door -----------------------------------------------


def _closes_while_reading(db, monkeypatch, error=None) -> None:
    async def analyze(*, kind, payload, input_ref, telegram_id):
        invites.close(db, SASHA)
        if error is not None:
            raise error
        return dict(NUTRITION)

    monkeypatch.setattr(tg, "_analyze_and_parse", analyze)


def _pending_only(wire: Phone) -> None:
    """What the person saw is what they saw before the door closed."""
    assert wire.edits() == []
    said = [p for p in wire.to(SASHA) if "Владелец бота видит" not in p["text"]]
    assert len(said) == 1


def test_a_read_that_ends_behind_a_closed_door_stores_and_says_nothing(db, monkeypatch):
    _invite(db)
    _closes_while_reading(db, monkeypatch)
    wire = play(say(SASHA, "омлет"))
    _pending_only(wire)
    db.expire_all()
    assert db.scalars(select(Meal)).one().reading_status == "read"  # the record stands


def test_a_failed_read_behind_a_closed_door_says_nothing_either(db, monkeypatch):
    _invite(db)
    _closes_while_reading(db, monkeypatch, error=ValueError("зюзя"))
    _pending_only(play(say(SASHA, "омлет")))


def test_the_line_of_a_closed_read_is_settled_by_the_sweep_unsaid(db, monkeypatch):
    _invite(db)
    _closes_while_reading(db, monkeypatch)
    wire = play(say(SASHA, "омлет"))
    _sweep_owed(wire)
    _pending_only(wire)


def _sweep_owed(wire: Phone) -> None:
    async def steps():
        application = tg.create_bot_application()
        application.bot._request = (wire, wire)
        await application.initialize()
        await tg._show_owed(application)
        await application.shutdown()

    asyncio.run(steps())


def test_a_read_with_the_door_open_still_speaks(db, reader):  # noqa: F811
    _invite(db)
    wire = play(say(SASHA, "омлет"))
    assert any(p["text"].startswith("Записано") for p in wire.edits())


def test_a_door_that_cannot_be_read_costs_no_line(
    db, reader, monkeypatch
):  # noqa: F811
    _invite(db)
    answers = iter([True])  # the gate before the read; the blip comes at its end

    def blip(_telegram_id):
        if next(answers, False):
            return True
        raise RuntimeError("no database")

    with monkeypatch.context() as patch:
        patch.setattr(tg, "_may_hear", blip)
        wire = play(say(SASHA, "омлет"))
    assert reader.text == ["омлет"]  # known positive: the read got as far as its end
    assert wire.edits() == []
    _sweep_owed(wire)  # the database is back: the line owed is shown
    assert any(p["text"].startswith("Записано") for p in wire.edits())


def test_a_meal_closed_while_it_waited_its_turn_is_not_paid_for(
    db,
    reader,  # noqa: F811
    monkeypatch,
):
    _invite(db)
    real = capture.read_as_of_now

    def closes(session, job):
        invites.close(db, SASHA)
        return real(session, job)

    monkeypatch.setattr(tg.capture, "read_as_of_now", closes)
    wire = play(say(SASHA, "омлет"))
    assert reader.text == []
    _pending_only(wire)


# --- closing in open mode ----------------------------------------------------------------


def _open_mode_and_closed(db, monkeypatch) -> None:
    monkeypatch.setattr(settings, "BOT_ACCESS_MODE", "open")
    _invite(db)
    assert invites.close(db, SASHA) == invites.Closed.CLOSED


def test_closing_in_open_mode_really_closes(db, monkeypatch):
    _open_mode_and_closed(db, monkeypatch)
    wire = play(say(SASHA, "/start"), say(SASHA, "омлет"), tap(SASHA, "wl:later"))
    assert wire.to(SASHA) == []
    assert not [n for n, _p in wire.calls if n == "editMessageText"]
    db.expire_all()
    assert db.scalars(select(Meal.id)).all() == []


def test_reopening_in_open_mode_opens(db, monkeypatch):
    _open_mode_and_closed(db, monkeypatch)
    assert access_control.is_user_allowed(SASHA) is False
    invites.reopen(db, SASHA, OWNER)
    assert access_control.is_user_allowed(SASHA) is True
    assert "Дневник питания" in play(say(SASHA, "/start")).to(SASHA)[0]["text"]


def test_closing_one_in_open_mode_leaves_everyone_else(db, monkeypatch):
    _open_mode_and_closed(db, monkeypatch)
    assert access_control.is_user_allowed(ZHENYA) is True
    assert play(say(ZHENYA, "/start")).to(ZHENYA)  # known positive of the silence above


def test_open_mode_stays_open_when_the_database_cannot_say(
    Session, monkeypatch, caplog
):
    monkeypatch.setattr(settings, "BOT_ACCESS_MODE", "open")

    def broken():
        raise RuntimeError("no database")

    monkeypatch.setattr(access_control, "SessionLocal", broken)
    with caplog.at_level(logging.ERROR):
        assert access_control.is_user_allowed(SASHA) is True
    assert "Access grant of" in caplog.text and "RuntimeError" in caplog.text


def test_the_open_mode_words_tell_the_truth_about_closing(db, monkeypatch):
    monkeypatch.setattr(settings, "BOT_ACCESS_MODE", "open")
    wire = play(say(OWNER, "/invite"), picked(OWNER, SASHA), refuses=[SASHA])
    words = wire.to(OWNER)[-1]["text"]
    assert "открыт для всех" in words
    assert "закрыть доступ можно" in words
    assert "доступ здесь ничего не меняет" not in words


# --- a moment of Telegram during the wait --------------------------------------------------


class Outage(Phone):
    """The owner's wait begins, and from then on every message to them dies on the way."""

    async def do_request(self, url, method, request_data=None, **kwargs):
        params = dict(request_data.parameters) if request_data else {}
        waiting = any(n == "sendMessage" for n, _p in self.calls)
        if (
            waiting
            and url.endswith("/sendMessage")
            and int(params.get("chat_id", 0)) == OWNER
        ):
            self.calls.append(("lost", params))
            raise NetworkError("Bad Gateway")
        return await super().do_request(url, method, request_data, **kwargs)


def test_a_forward_during_an_outage_lets_in_and_stays_out_of_the_diary(
    db,
    reader,  # noqa: F811
    monkeypatch,
    caplog,
):
    monkeypatch.setattr(tg, "SHOW_PAUSE_SECONDS", 0)
    wire = Outage()
    with caplog.at_level(logging.WARNING):
        play(say(OWNER, "/invite"), forward(OWNER, from_user(ZHENYA)), wire=wire)
    assert access_control.is_user_allowed(ZHENYA) is True
    _nothing_in_the_diary(db)
    assert reader.text == []
    assert [n for n, _p in wire.calls if n == "lost"]  # the outage did happen
    assert "Invitation reply not sent" in caplog.text


@pytest.mark.parametrize(
    "origin",
    [
        {"type": "hidden_user", "sender_user_name": "Женя"},
        from_user(ZHENYA, is_bot=True),
    ],
)
def test_a_refusal_lost_in_an_outage_keeps_the_forward_out_of_the_diary(
    db,
    reader,  # noqa: F811
    monkeypatch,
    origin,
):
    monkeypatch.setattr(tg, "SHOW_PAUSE_SECONDS", 0)
    wire = Outage()
    play(say(OWNER, "/invite"), forward(OWNER, origin), wire=wire)
    assert [n for n, _p in wire.calls if n == "lost"]
    _nothing_in_the_diary(db)
    assert reader.text == []


def test_someone_with_no_subscription_keeps_the_old_answer(db):
    sent = play(say(LISTED, "/start")).to(LISTED)
    assert "нужна подписка" in sent[0]["text"]


def test_the_conversation_goes_on_after_the_welcome(db):
    _invite(db)
    wire = play(
        say(SASHA, "/start"),
        say(SASHA, "⚙️ Настройки"),
        say(SASHA, "/start"),
        say(SASHA, "⚙️ Настройки"),
    )
    assert sum("Настройки" in p["text"] for p in wire.to(SASHA)) == 2


def test_the_hint_comes_once_after_the_first_record(db, reader):  # noqa: F811
    _invite(db)
    wire = play(
        say(SASHA, "/start"),
        tap(SASHA, "wl:eat:all"),
        say(SASHA, "омлет"),
        say(SASHA, "кофе"),
    )
    hints = [p for p in wire.to(SASHA) if p["text"] == welcome_screen.HINT]
    assert len(hints) == 1
    assert hints[0]["text"] == (
        "Дальше так же: фото, текст или голосовое.\n\n"
        "💬 Вопросы и пожелания: /feedback\n"
        "ℹ️ Объяснения: ⚙️ Настройки → ℹ️ Как это работает"
    )
    # The card above it has just said «Записано».
    assert "Записано" not in hints[0]["text"]
    assert _gives_main_keyboard(hints[0])
    assert reader.text == ["омлет", "кофе"]


def test_the_hint_comes_to_someone_who_wrote_before_answering(db, reader):  # noqa: F811
    _invite(db)
    wire = play(say(SASHA, "/start"), say(SASHA, "омлет"))
    assert [p for p in wire.to(SASHA) if p["text"] == welcome_screen.HINT]


def test_no_hint_when_the_read_failed(db, reader):  # noqa: F811
    reader.error = ValueError("зюзя")
    _invite(db)
    wire = play(say(SASHA, "/start"), say(SASHA, "омлет"))
    assert not [p for p in wire.to(SASHA) if p["text"] == welcome_screen.HINT]


def test_no_hint_for_the_owner_or_anyone_already_in(db, reader):  # noqa: F811
    _invite(db)
    user = db.scalar(select(User).where(User.telegram_id == SASHA))
    db.add(_old_meal(user))
    db.commit()
    wire = play(say(OWNER, "омлет"), say(SASHA, "омлет"))
    assert not [p for p in wire.sent() if p["text"] == welcome_screen.HINT]


def test_a_lost_hint_costs_nothing(db, reader, caplog):  # noqa: F811
    _invite(db)

    class NoHint(Phone):
        async def do_request(self, url, method, request_data=None, **kwargs):
            params = dict(request_data.parameters) if request_data else {}
            if params.get("text") == welcome_screen.HINT:
                return 403, b'{"ok":false,"error_code":403,"description":"Forbidden"}'
            return await super().do_request(url, method, request_data, **kwargs)

    wire = NoHint()

    async def steps():
        application = tg.create_bot_application()
        application.bot._request = (wire, wire)
        await application.initialize()
        await application.start()
        for update in (say(SASHA, "/start"), say(SASHA, "омлет")):
            await application.process_update(Update.de_json(update, application.bot))
            await asyncio.gather(*list(tg._meal_reads), return_exceptions=True)
        await application.stop()
        await application.shutdown()

    with caplog.at_level(logging.WARNING):
        asyncio.run(steps())
    assert "First record hint not sent: Forbidden" in caplog.text
    assert db.scalars(select(Meal.id)).all()


def test_feedback_named_by_the_hint_is_there(db):
    _invite(db)
    words = play(say(SASHA, "/feedback")).to(SASHA)[0]["text"]
    assert words.startswith("<b>💬 Фидбэк</b>")
    assert "подписк" not in words.casefold()


# --- every string --------------------------------------------------------------------


def test_every_new_string_is_gender_neutral():
    words = " ".join(
        value
        for module in (people_screen, welcome_screen)
        for value in vars(module).values()
        if isinstance(value, str)
    ).casefold()
    assert "дневник питания" in words  # the control: the strings were read
    for gendered in ("сделала", "поняла", "записала", "приглашён ", "приглашена"):
        assert gendered not in words
