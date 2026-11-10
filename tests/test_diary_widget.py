"""«🗒 Дневник дня» (/diary): the day as a widget inside a message.

The day's rows, the HTML under Telegram's 4 KB, the text twin, ‹ › and the send: a
widget, or text when widgets are off, the day does not fit or Telegram refuses one.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import random
import re
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from telegram import Update
from telegram.error import TimedOut

import app.services.access_control as access_control
import app.services.action_journal as action_journal
import app.services.ai_call_log_service as log_service
import app.services.inbound_message_service as inbound_service
import app.services.model_selection as model_selection
import app.services.telegram as tg
import app.services.usage_ledger as usage_ledger
from app.core.config import settings
from app.db.base import Base
from app.models.meal import Meal
from app.models.meal_item import MealItem
from app.models.personal_food import PersonalFood
from app.models.user import User
from app.services import diary_widget as dw
from tests.test_capture_router import NOW_TS, Wire

ME = 9101
ZONE = ZoneInfo("Asia/Tbilisi")
TZ = {"timezone": "Asia/Tbilisi"}


def _today() -> date:
    return datetime.now(UTC).astimezone(ZONE).date()


def _at(day: date, hour: int, minute: int = 0) -> datetime:
    return (
        datetime.combine(day, datetime.min.time(), tzinfo=ZONE)
        .replace(hour=hour, minute=minute)
        .astimezone(UTC)
    )


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
        access_control,
        usage_ledger,
        action_journal,
    ):
        monkeypatch.setattr(module, "SessionLocal", factory)
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_IDS", str(ME))
    monkeypatch.setattr(settings, "BOT_ACCESS_MODE", "whitelist")
    monkeypatch.setattr(settings, "ALLOWED_TELEGRAM_IDS", "")
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "123:fake")
    monkeypatch.setattr(settings, "DIARY_WIDGET", True)
    return factory


@pytest.fixture
def db(Session):
    session = Session()
    yield session
    session.close()


@pytest.fixture
def me(db):
    user = User(telegram_id=ME, username="me", settings=dict(TZ))
    db.add(user)
    db.commit()
    return user


def _meal(db, user, when, name, *, groups=(), items=(), photos=None, **columns):
    meal = Meal(
        user_id=user.id,
        description=name,
        meal_time=when,
        sent_at=when,
        nutrients={"foods": [name], "food_groups": list(groups)},
        photos=photos,
        **columns,
    )
    db.add(meal)
    db.flush()
    for position, (item_name, kcal, dish_id) in enumerate(items):
        db.add(
            MealItem(
                meal_id=meal.id,
                origin_meal_id=meal.id,
                position=position,
                name=item_name,
                base_calories=kcal,
                personal_food_id=dish_id,
            )
        )
    db.commit()
    return meal


def _dish(db, user, name) -> PersonalFood:
    dish = PersonalFood(
        user_id=user.id,
        canonical_name=name,
        per_100g_calories=100,
        per_100g_proteins=10,
        per_100g_fats=5,
        per_100g_carbs=10,
        numbers_basis="per_100",
    )
    db.add(dish)
    db.commit()
    return dish


def _data(html: str) -> dict:
    """The day the widget carries, read back out of its script."""
    raw = re.search(r"const D=(\{.*?\}),G=", html).group(1)
    return json.loads(raw)


# --- the day's rows --------------------------------------------------------------------


def test_a_row_holds_time_emoji_marks_and_its_dishes(db, me):
    tofu = _dish(db, me, "тофу")
    day = _today() - timedelta(days=1)
    meal = _meal(
        db,
        me,
        _at(day, 22, 49),
        "Сэндвич с тофу",
        groups=("veg", "anchor", "legumes"),
        items=(("кетчуп", 20, None), ("сэндвич с тофу", 450, tofu.id)),
        photos=[{"file_id": "f"}],
    )
    row = dw.row(db, meal, ZONE)
    assert row.clock == "22:49" and row.part == 2
    assert row.emoji == "🥪"  # the biggest dish's, not the first one's
    assert row.flags == dw.SAVED | dw.PHOTO
    assert row.groups == ("legumes", "anchor", "veg")  # every screen's order
    assert [name for _e, name in row.dishes] == ["кетчуп", "сэндвич с тофу"]


def test_parts_of_the_day_and_a_meal_without_a_time_goes_last(db, me):
    day = _today() - timedelta(days=1)
    _meal(db, me, _at(day, 12), "без времени суп", time_precision="day_only")
    _meal(db, me, _at(day, 4), "сырок")
    _meal(db, me, _at(day, 17, 59), "блинчики")
    _meal(db, me, _at(day, 18), "твикс")
    rows = dw.rows_of(db, me, day, ZONE)
    assert [(r.name, r.part) for r in rows] == [
        ("сырок", 0),
        ("блинчики", 1),
        ("твикс", 2),
        ("без времени суп", 3),
    ]
    assert rows[-1].clock == ""


def test_an_unread_meal_is_marked_and_a_deleted_one_is_not_there(db, me):
    day = _today() - timedelta(days=1)
    _meal(db, me, _at(day, 9), "фото", reading_status="pending")
    _meal(db, me, _at(day, 10), "ушло", deleted_at=_at(day, 11))
    rows = dw.rows_of(db, me, day, ZONE)
    assert len(rows) == 1 and rows[0].flags & dw.UNREAD


def test_counts_name_what_was_there_never_what_was_not(db, me):
    day = _today() - timedelta(days=1)
    _meal(db, me, _at(day, 9), "a", groups=("anchor", "veg"))
    _meal(db, me, _at(day, 13), "b", groups=("anchor",))
    rows = dw.rows_of(db, me, day, ZONE)
    assert dw.counts(rows) == [("anchor", 2), ("veg", 1)]
    assert dw.caption(rows, "Понедельник 9 ноября").splitlines()[1] == (
        "⚓ опора ×2 · 🥦 овощи"
    )


# --- the widget ------------------------------------------------------------------------

_DROPPED = re.compile(
    r"opacity|color-mix|:has\(|:focus|@font-face|oklch|sticky|overflow:auto|"
    r"\d+vh|<input|<form|<label|padding-block|margin-inline|line-clamp"
)


def _rows(n: int, name: str = "Спагетти с шампиньонами и сыром") -> list:
    return [
        dw.Row(
            f"{8 + i % 14}:{i % 60:02d}",
            i % 3,
            "🍝",
            f"{name} {i}",
            dw.PHOTO,
            ("anchor", "veg"),
            tuple((f"{i}", f"блюдо номер {i} {j}") for j in range(6)),
        )
        for i in range(n)
    ]


def test_a_full_day_fits_and_uses_nothing_the_widget_profile_drops():
    html = dw.widget(_rows(14), "Понедельник 9 ноября", "на 22:50")
    assert html is not None
    assert dw.packed(html) <= dw.BUDGET < dw.LIMIT
    assert "\x00" not in html
    assert not _DROPPED.search(html), _DROPPED.search(html)
    data = _data(html)
    assert data["n"] == "14 записей" and data["f"] == 0  # many: one part open at a time
    opened = data["p"][0][1][0][5]
    assert len(opened) == dw.DISHES + 1 and opened[-1] == ["…", "ещё 2"]


def _noise(rng, n: int) -> str:
    return "".join(rng.choice("абвгдежзиклмнопрстуфхцчшщэюя ") for _ in range(n))


def test_a_day_too_big_drops_the_dishes_first_then_goes_as_text(monkeypatch):
    rng = random.Random(7)  # words that do not compress, as real names barely do
    rows = [
        dw.Row(
            "9:00",
            i % 3,
            "🍽",
            _noise(rng, 30),
            0,
            (),
            tuple(("🍽", _noise(rng, 40)) for _ in range(4)),
        )
        for i in range(24)
    ]
    full = _data(dw.widget(rows[:6], "x", ""))
    assert len(full["p"][0][1][0][5]) == 4  # a small day keeps every dish
    shrunk = dw.widget(rows, "x", "")
    assert shrunk is not None and dw.packed(shrunk) <= dw.BUDGET
    assert all(len(r[5]) < 4 for p in _data(shrunk)["p"] for r in p[1])
    huge = [dataclasses.replace(r, name=_noise(rng, 120)) for r in rows]
    cut = _data(dw.widget(huge, "x", ""))
    assert all(len(r[2]) <= 18 and r[5] == [] for p in cut["p"] for r in p[1])
    monkeypatch.setattr(dw, "BUDGET", 1500)
    assert dw.widget(huge, "x", "") is None
    text = dw.text(huge * 3, "Понедельник 9 ноября")
    assert len(text) <= dw.TEXT_MAX and text.splitlines()[-1] == "…"


def test_a_part_too_long_for_600_px_goes_as_text():
    rows = [dw.Row("19:00", 2, "🍽", f"ужин {i}", 0, (), ()) for i in range(9)]
    assert dw.widget(rows[: dw.PART_ROWS], "x", "") is not None
    assert dw.widget(rows, "x", "") is None


@pytest.mark.parametrize("name", ["</script><b>x\x00", "<!--<script>", "a\u2028b"])
def test_a_name_cannot_end_or_swallow_the_script_or_carry_a_nul(name):
    row = dw.Row("9:00", 1, "🍽", dw._cut(name), 0, (), ())
    html = dw.widget([row], "x", "")
    script = html[html.index("const D=") : html.rindex("</script>")]
    assert "<" not in script and "\u2028" not in script and "\x00" not in html
    assert _data(html)["p"][0][1][0][2] == dw._cut(name)


def test_no_calories_anywhere():
    rows = _rows(3)
    html = dw.widget(rows, "x", "")
    for shown in (html, dw.caption(rows, "x"), dw.text(rows, "x")):
        assert "ккал" not in shown and "kcal" not in shown


def test_an_empty_day_says_so():
    data = _data(dw.widget([], "Понедельник 9 ноября", ""))
    assert data["p"] == [] and data["e"] == dw.EMPTY
    assert dw.text([], "Понедельник 9 ноября").endswith(dw.EMPTY)
    assert dw.caption([], "Понедельник 9 ноября") == "🗒 Дневник · Понедельник 9 ноября"


def test_every_group_has_a_colour_pair():
    assert set(dw.COLOURS) == set(dw.GROUP_KEYS)
    css = dw._css()
    for i in range(len(dw.GROUP_KEYS)):
        assert f".g{i}{{background:light-dark(" in css


# --- ‹ › -------------------------------------------------------------------------------


def test_today_goes_back_only_and_yesterday_comes_back_to_today():
    today = date(2026, 11, 10)
    (row,) = dw.markup(today, today).inline_keyboard
    assert [b.callback_data for b in row] == ["dy:d:20261109"]
    (row,) = dw.markup(today - timedelta(days=1), today).inline_keyboard
    assert [b.text for b in row] == ["‹ вс 8 ноя", "Сегодня ›"]
    assert row[1].callback_data == "dy:d:20261110"


@pytest.mark.parametrize(
    "data",
    [
        "dy:d:20261111",
        "dy:d:2026111",
        "dy:d:2026-1-1",
        "dy:d:x",
        "dy:x:20261109",
        "dy:d:20240101",
    ],
)
def test_a_tap_asks_only_for_a_day_it_may(data):
    assert dw.parse(data, date(2026, 11, 10)) is None


def test_a_tap_for_yesterday_is_read():
    assert dw.parse("dy:d:20261109", date(2026, 11, 10)) == date(2026, 11, 9)


# --- the send --------------------------------------------------------------------------


class Telegram(Wire):
    """Answers like Telegram; ``fail`` makes a method fail: a description is Telegram's
    400, an exception is raised as the network would."""

    def __init__(self, **fail):
        super().__init__()
        self.fail = fail

    async def do_request(self, url, method, request_data=None, **kwargs):
        name = url.rsplit("/", 1)[-1]
        failure = self.fail.get(name)
        if failure is None:
            return await super().do_request(url, method, request_data, **kwargs)
        self.calls.append((name, dict(request_data.parameters) if request_data else {}))
        if isinstance(failure, Exception):
            raise failure
        body = {"ok": False, "error_code": 400, "description": failure}
        return 400, json.dumps(body).encode()


TOO_LARGE = "Bad Request: INPUT_MEDIA_WIDGET_TOO_LARGE"


def _run(update: dict, wire: Telegram) -> Telegram:
    async def steps():
        application = tg.create_bot_application()
        application.bot._request = (wire, wire)
        await application.initialize()
        await application.start()
        try:
            await application.process_update(Update.de_json(update, application.bot))
        finally:
            await application.stop()
            await application.shutdown()

    asyncio.run(steps())
    return wire


def _from_me(**fields) -> dict:
    return {
        "message_id": 700,
        "date": NOW_TS,
        "chat": {"id": ME, "type": "private"},
        "from": {"id": ME, "is_bot": False, "first_name": "x"},
        **fields,
    }


def _command(wire=None) -> Telegram:
    message = _from_me(
        text="/diary", entities=[{"type": "bot_command", "offset": 0, "length": 6}]
    )
    return _run({"update_id": 1, "message": message}, wire or Telegram())


def _tap(data: str, wire=None) -> Telegram:
    query = {
        "id": "q",
        "from": {"id": ME, "is_bot": False, "first_name": "x"},
        "chat_instance": "c",
        "data": data,
        "message": {
            "message_id": 4900,
            "date": NOW_TS,
            "chat": {"id": ME, "type": "private"},
            "text": "x",
        },
    }
    return _run({"update_id": 2, "callback_query": query}, wire or Telegram())


def _named(wire, name) -> list:
    return [params for n, params in wire.calls if n == name]


def test_diary_sends_todays_widget_with_its_caption_and_buttons(db, me):
    _meal(
        db,
        me,
        datetime.now(UTC) - timedelta(minutes=5),
        "Овсянка с ягодами",
        groups=("grain",),
    )
    wire = _command()
    (sent,) = _named(wire, "sendWidget")
    assert int(sent["chat_id"]) == ME
    assert "Овсянка с ягодами" in sent["html"]
    assert sent["caption"].startswith("🗒 Дневник · ") and "1 запись" in sent["caption"]
    markup = sent["reply_markup"]
    keyboard = (json.loads(markup) if isinstance(markup, str) else markup)[
        "inline_keyboard"
    ]
    yesterday = _today() - timedelta(days=1)
    assert [b["callback_data"] for b in keyboard[0]] == [f"dy:d:{yesterday:%Y%m%d}"]
    assert not _named(wire, "sendMessage")


def test_a_refused_widget_comes_as_text(db, me, caplog):
    _meal(db, me, datetime.now(UTC) - timedelta(minutes=5), "Овсянка с ягодами")
    wire = Telegram(sendWidget=TOO_LARGE)
    with caplog.at_level("WARNING"):
        _command(wire)
    assert _named(wire, "sendWidget")  # control: it was tried
    (text,) = _named(wire, "sendMessage")
    assert "Овсянка с ягодами" in text["text"] and text["text"].startswith("🗒 Дневник")
    assert "Diary widget refused" in caplog.text
    assert "input_media_widget_too_large" in caplog.text.lower()


def test_widgets_off_send_text_and_never_try(db, me, monkeypatch):
    monkeypatch.setattr(settings, "DIARY_WIDGET", False)
    _meal(db, me, datetime.now(UTC) - timedelta(minutes=5), "Овсянка с ягодами")
    wire = _command()
    assert not _named(wire, "sendWidget")
    (text,) = _named(wire, "sendMessage")
    assert "Овсянка с ягодами" in text["text"]


def test_back_sends_that_day_and_the_tapped_one_leaves(db, me):
    yesterday = _today() - timedelta(days=1)
    _meal(db, me, _at(yesterday, 13), "Суп вчерашний")
    wire = _tap(f"dy:d:{yesterday:%Y%m%d}")
    (sent,) = _named(wire, "sendWidget")
    assert "Суп вчерашний" in sent["html"]
    (gone,) = _named(wire, "deleteMessage")
    assert int(gone["message_id"]) == 4900


def test_a_tap_for_a_day_ahead_sends_nothing(db, me, caplog):
    tomorrow = _today() + timedelta(days=1)
    with caplog.at_level("WARNING"):
        wire = _tap(f"dy:d:{tomorrow:%Y%m%d}")
    assert not _named(wire, "sendWidget") and not _named(wire, "deleteMessage")
    assert "no such day" in caplog.text
    (answer,) = _named(wire, "answerCallbackQuery")
    assert answer["text"] == "Этот день не открыть"


def test_someone_shut_out_gets_the_refusal_not_a_diary(db, me, monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_IDS", "1")
    monkeypatch.setattr(tg, "check_subscription", _never)
    wire = _command()
    assert not _named(wire, "sendWidget")
    wire = _tap(f"dy:d:{_today():%Y%m%d}")
    assert not _named(wire, "sendWidget")


async def _never(_telegram_id):
    return False


def test_a_timed_out_widget_is_not_sent_again_as_text(db, me, caplog):
    _meal(db, me, datetime.now(UTC) - timedelta(minutes=5), "Овсянка с ягодами")
    with caplog.at_level("WARNING"):
        wire = _command(Telegram(sendWidget=TimedOut()))
    assert _named(wire, "sendWidget")  # control: it was tried
    assert not _named(wire, "sendMessage")  # the day may be there already: no twin
    assert "timed out, not sent again" in caplog.text


def test_when_even_the_text_fails_the_person_hears_it_and_nothing_escapes(
    db, me, caplog
):
    _meal(db, me, datetime.now(UTC) - timedelta(minutes=5), "Овсянка с ягодами")
    wire = Telegram(sendWidget=TOO_LARGE)
    wire.fail["sendMessage"] = "Bad Request: chat not found"
    with caplog.at_level("WARNING"):
        _command(wire)
    texts = [p["text"] for p in _named(wire, "sendMessage")]
    assert texts[0].startswith("🗒 Дневник") and texts[1] == tg.DIARY_FAILED
    assert "Diary not sent, not even as text" in caplog.text
    assert "the failure note did not go either" in caplog.text


def test_a_failed_send_after_a_tap_says_so(db, me):
    wire = Telegram(sendWidget=TOO_LARGE, sendMessage="Bad Request: chat not found")
    _tap(f"dy:d:{_today() - timedelta(days=1):%Y%m%d}", wire)
    (answer,) = _named(wire, "answerCallbackQuery")
    assert answer["text"] == tg.DIARY_FAILED and answer["show_alert"] in (True, "true")


def test_a_second_quick_tap_finds_the_day_gone_and_sends_nothing(db, me, caplog):
    wire = Telegram(deleteMessage="Bad Request: message to delete not found")
    with caplog.at_level("INFO"):
        _tap(f"dy:d:{_today() - timedelta(days=1):%Y%m%d}", wire)
    assert _named(wire, "deleteMessage")  # control: it tried to take the old day
    assert not _named(wire, "sendWidget") and not _named(wire, "sendMessage")
    assert "another tap already took this day" in caplog.text


def test_an_old_day_that_cannot_be_deleted_still_opens_the_new_one(db, me):
    wire = Telegram(deleteMessage="Bad Request: message can't be deleted")
    _tap(f"dy:d:{_today() - timedelta(days=1):%Y%m%d}", wire)
    assert _named(wire, "sendWidget")
