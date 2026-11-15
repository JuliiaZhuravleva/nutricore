"""Taps inside Telegram HTML widgets (Bot API 10.4 ``widget_query``): python-telegram-bot
21.9 does not know the field, so it arrives in ``update.api_kwargs``. These tests walk a
raw update through ``Update.de_json`` and the real application: the field is there, the
router gets it, the access gate treats its sender like any other sender."""

import asyncio
import logging
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from telegram import Update
from telegram.error import BadRequest, NetworkError
from telegram.ext import ApplicationHandlerStop

from app.core.config import settings
from app.models.action_event import ActionEvent
from app.services import access_control, action_journal
from app.services import telegram as tg
from app.services import widget_queries
from tests.test_capture_router import (  # noqa: F401 (fixtures)
    Session,
    _with_app,
    db,
    owner,
    reader,
)

PERSON = 4242


def _raw(person=PERSON, data="dy:r", **more) -> dict:
    """The shape in the Bot API guide: id, from, chat, message_id, data."""
    query = {
        "id": "8123456789012345678",
        "from": {"id": person, "is_bot": False, "first_name": "x"},
        "chat": {"id": person, "type": "private", "first_name": "x"},
        "message_id": 77,
        "chat_instance": "901234567890123456",
        "data": data,
        **more,
    }
    return {"update_id": 100000001, "widget_query": query}


@pytest.fixture(autouse=True)
def _routes(monkeypatch):
    routes: dict = {}
    monkeypatch.setattr(widget_queries, "_routes", routes)
    monkeypatch.setattr(widget_queries, "_warned_malformed", False)
    return routes


class FakeBot:
    """Records do_api_request calls; ``fail`` is raised instead of answering."""

    def __init__(self, fail=None):
        self.calls = []
        self.fail = fail
        self.sent = []

    async def do_api_request(self, endpoint, api_kwargs=None, **kw):
        self.calls.append((endpoint, dict(api_kwargs or {})))
        if self.fail is not None:
            raise self.fail
        return True

    async def send_message(self, *args, **kwargs):
        self.sent.append(args)


# -- the field arrives, the router gets it ---------------------------------------------


def test_de_json_keeps_the_unknown_field_in_api_kwargs():
    update = Update.de_json(_raw(), None)
    assert "widget_query" in update.api_kwargs  # the premise: PTB 21.9 has no field
    assert update.effective_user is None  # why the gates needed person_of
    query = widget_queries.query_of(update)
    assert query == widget_queries.WidgetQuery(
        id="8123456789012345678",
        from_id=PERSON,
        chat_id=PERSON,
        message_id=77,
        data="dy:r",
        type=None,
    )
    assert widget_queries.person_of(update) == PERSON


def test_other_updates_are_no_widget_query():
    update = Update.de_json(
        {
            "update_id": 1,
            "message": {
                "message_id": 1,
                "date": 1,
                "chat": {"id": 1, "type": "private"},
                "text": "суп",
            },
        },
        None,
    )
    assert widget_queries.query_of(update) is None
    assert widget_queries.person_of(update) is None


@pytest.mark.parametrize(
    "broken",
    [
        {"widget_query": "x"},
        {"widget_query": {"id": "1"}},  # no sender
        {"widget_query": {"from": {"id": 1}, "data": "a"}},  # no id
        {"widget_query": {"id": "1", "from": {"id": "x"}, "data": "a"}},
        {"widget_query": {"id": "1", "from": {"id": 1}, "data": 5}},
    ],
)
def test_a_malformed_query_is_none_and_logged_once(broken, caplog):
    update = Update.de_json({"update_id": 1, **broken}, None)
    with caplog.at_level(logging.WARNING):
        assert widget_queries.query_of(update) is None
        assert widget_queries.query_of(update) is None
    assert widget_queries.person_of(update) is None
    assert len([r for r in caplog.records if "widget_query" in r.message]) == 1


def test_the_router_gets_a_query_through_the_application(
    owner, reader, monkeypatch  # noqa: F811
):
    monkeypatch.setattr(settings, "BOT_ACCESS_MODE", "open")
    seen = []

    async def tapped(update, context):
        seen.append(widget_queries.query_of(update))

    widget_queries.register("dy", tapped)

    async def steps(application, wire):
        await application.process_update(Update.de_json(_raw(), application.bot))

    asyncio.run(_with_app(steps))
    assert [q.data for q in seen] == ["dy:r"]


def test_an_unknown_prefix_is_answered_empty_and_logged(caplog):
    bot = FakeBot()
    update = Update.de_json(_raw(data="zz:1"), bot)
    with caplog.at_level(logging.WARNING):
        asyncio.run(widget_queries.dispatch(update, SimpleNamespace(bot=bot)))
    assert bot.calls == [
        ("answerWidgetQuery", {"widget_query_id": "8123456789012345678"})
    ]
    assert any("zz" in r.message for r in caplog.records)


def test_dispatch_ignores_every_other_update():
    bot = FakeBot()
    update = Update.de_json({"update_id": 1}, bot)
    asyncio.run(widget_queries.dispatch(update, SimpleNamespace(bot=bot)))
    assert bot.calls == []


def test_a_failing_route_still_answers_the_tap():
    async def broken(update, context):
        raise RuntimeError("boom")

    widget_queries.register("dy", broken)
    bot = FakeBot()
    update = Update.de_json(_raw(), bot)
    asyncio.run(widget_queries.dispatch(update, SimpleNamespace(bot=bot)))
    assert [c[0] for c in bot.calls] == ["answerWidgetQuery"]


def test_the_application_asks_telegram_for_widget_queries():
    assert "widget_query" in tg.ALLOWED_UPDATES
    for old in ("message", "edited_message", "callback_query"):
        assert old in tg.ALLOWED_UPDATES


# -- the gates: whitelist mode ----------------------------------------------------------


@pytest.fixture
def journal(Session, monkeypatch):  # noqa: F811
    monkeypatch.setattr(action_journal, "SessionLocal", Session)


def _rows(db):  # noqa: F811
    db.expire_all()
    return list(db.scalars(select(ActionEvent)).all())


def _tap_through_app(person):
    seen = []

    async def route(update, context):
        seen.append(widget_queries.query_of(update))

    widget_queries.register("dy", route)

    async def steps(application, wire):
        await application.process_update(
            Update.de_json(_raw(person=person), application.bot)
        )

    asyncio.run(_with_app(steps))
    return seen


def test_an_allowed_person_reaches_the_router_and_leaves_no_row(
    db, owner, reader, journal, monkeypatch  # noqa: F811
):
    monkeypatch.setattr(settings, "BOT_ACCESS_MODE", "whitelist")
    monkeypatch.setattr(settings, "ALLOWED_TELEGRAM_IDS", str(PERSON))
    seen = _tap_through_app(PERSON)
    assert [q.from_id for q in seen] == [PERSON]
    assert _rows(db) == []  # a widget tap is no action of the journal's closed lists


def test_a_stranger_is_dropped_before_the_router_and_leaves_nothing(
    db, owner, reader, journal, monkeypatch  # noqa: F811
):
    monkeypatch.setattr(settings, "BOT_ACCESS_MODE", "whitelist")
    monkeypatch.setattr(settings, "ALLOWED_TELEGRAM_IDS", "")
    seen = _tap_through_app(7007)
    assert seen == []
    assert _rows(db) == []


def test_the_gate_drops_a_stranger_with_no_reply_and_passes_an_allowed_one(
    monkeypatch,
):
    monkeypatch.setattr(settings, "BOT_ACCESS_MODE", "whitelist")
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_IDS", "1")
    monkeypatch.setattr(settings, "ALLOWED_TELEGRAM_IDS", "2")
    bot = FakeBot()
    context = SimpleNamespace(bot=bot)
    with pytest.raises(ApplicationHandlerStop):
        asyncio.run(
            access_control.access_gate(Update.de_json(_raw(person=999), bot), context)
        )
    allowed = Update.de_json(_raw(person=2), bot)
    assert asyncio.run(access_control.access_gate(allowed, context)) is None
    assert bot.sent == [] and bot.calls == []


# -- answer / edit_data -----------------------------------------------------------------


def test_answer_sends_answerWidgetQuery_with_data():
    bot = FakeBot()
    assert asyncio.run(widget_queries.answer(bot, "q1", '{"p":1}')) is True
    assert bot.calls == [
        ("answerWidgetQuery", {"widget_query_id": "q1", "data": '{"p":1}'})
    ]


def test_answer_without_data_sends_none():
    bot = FakeBot()
    assert asyncio.run(widget_queries.answer(bot, "q1", None)) is True
    assert bot.calls == [("answerWidgetQuery", {"widget_query_id": "q1"})]


def test_answer_counts_bytes_not_letters(caplog):
    bot = FakeBot()
    assert asyncio.run(widget_queries.answer(bot, "q1", "я" * 512)) is True  # 1024 B
    with caplog.at_level(logging.WARNING):
        assert asyncio.run(widget_queries.answer(bot, "q1", "я" * 513)) is False
    assert len(bot.calls) == 1
    assert any("1024" in r.message for r in caplog.records)


def test_answer_failure_is_false_not_a_raise(caplog):
    bot = FakeBot(fail=BadRequest("invalid widget query id"))
    with caplog.at_level(logging.WARNING):
        assert asyncio.run(widget_queries.answer(bot, "q1", None)) is False
    assert caplog.records


def test_edit_data_sends_editMessageWidgetData():
    bot = FakeBot()
    assert asyncio.run(widget_queries.edit_data(bot, 5, 77, '{"b":1}')) is True
    assert bot.calls == [
        (
            "editMessageWidgetData",
            {"chat_id": 5, "message_id": 77, "chat_data": '{"b":1}'},
        )
    ]


def test_edit_data_not_modified_is_success():
    bot = FakeBot(fail=BadRequest("message is not modified"))
    assert asyncio.run(widget_queries.edit_data(bot, 5, 77, "x")) is True


def test_edit_data_another_400_is_a_logged_failure(caplog):
    bot = FakeBot(fail=BadRequest("invalid widget identifier specified"))
    with caplog.at_level(logging.WARNING):
        assert asyncio.run(widget_queries.edit_data(bot, 5, 77, "x")) is False
    assert any("editMessageWidgetData" in r.message for r in caplog.records)


def test_edit_data_a_network_error_is_a_failure_too():
    bot = FakeBot(fail=NetworkError("down"))
    assert asyncio.run(widget_queries.edit_data(bot, 5, 77, "x")) is False


def test_edit_data_oversize_is_refused_without_a_call(caplog):
    bot = FakeBot()
    with caplog.at_level(logging.WARNING):
        assert asyncio.run(widget_queries.edit_data(bot, 5, 77, "x" * 1025)) is False
    assert bot.calls == []
    assert any("1024" in r.message for r in caplog.records)


# -- the handler must not shadow its group ------------------------------------------------


def test_the_widget_handler_matches_only_widget_queries(owner, reader):  # noqa: F811
    async def steps(application, wire):
        handler = next(
            h
            for h in application.handlers[-3]
            if isinstance(h, widget_queries.WidgetQueryHandler)
        )
        assert handler.check_update(Update.de_json(_raw(), application.bot))
        plain = Update.de_json(
            {
                "update_id": 1,
                "message": {
                    "message_id": 1,
                    "date": 0,
                    "chat": {"id": PERSON, "type": "private"},
                    "from": {"id": PERSON, "is_bot": False, "first_name": "x"},
                    "text": "hi",
                },
            },
            application.bot,
        )
        assert not handler.check_update(plain)
        # invite_intake shares the group and must still see a private message
        others = [h for h in application.handlers[-3] if h is not handler]
        assert others and any(h.check_update(plain) for h in others)

    asyncio.run(_with_app(steps))


def test_edit_state_tells_a_refusal_from_a_lost_edit():
    """The caller sends the older form only for a refusal: a lost edit may have
    arrived, a 400 never draws."""
    refused = FakeBot(fail=BadRequest("Input_media_widget_not_allowed"))
    lost = FakeBot(fail=NetworkError("down"))
    same = FakeBot(fail=BadRequest("message is not modified"))
    run = asyncio.run
    assert run(widget_queries.edit_state(refused, 5, 77, "x")) == widget_queries.REFUSED
    assert run(widget_queries.edit_state(lost, 5, 77, "x")) == widget_queries.FAILED
    assert run(widget_queries.edit_state(same, 5, 77, "x")) == widget_queries.OK
    assert run(widget_queries.edit_state(FakeBot(), 5, 77, "x")) == widget_queries.OK
    assert run(widget_queries.edit_state(FakeBot(), 5, 77, "x" * 1025)) == "failed"
