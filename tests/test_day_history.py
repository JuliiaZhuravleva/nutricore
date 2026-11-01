"""The day list's way in and out, section 6 ``O1``, ``O1a``, ``O1b``, ``O13a``.

On real rows, through the real application at the wire. ``reader`` stands in for the
model, so nothing here reaches the OpenAI client.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from telegram import Update

from app.models import Meal, User
from app.services import meal_card, stats_screen
from app.services import telegram as tg
from app.services.card_delivery import CardDelivery
from app.services.numbers import SHOW_NUMBERS
from app.services.picture_renderer import PictureRenderer
from tests.test_capture_router import (  # noqa: F401 (fixtures)
    NOW_TS,
    OWNER,
    PHOTO,
    Session,
    Wire,
    _message,
    _voice,
    _with_app,
    db,
    owner,
    photo,
    reader,
    send,
    text,
)
from tests.test_dishes import (  # noqa: F401 (fixtures)
    _data,
    _dish,
    _eat,
    _labels,
    _no_screen_from_another_test,
    _on,
    _shown,
    hard_day,
    tap,
)

ZONE = ZoneInfo("Asia/Tbilisi")  # the owner's: see the fixture in test_capture_router
SUN = "20261025"


def _at(day: date, hour=9, minute=0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=ZONE).astimezone(
        UTC
    )


def _today() -> date:
    return datetime.now(ZONE).date()


def _plain(db, owner, name, when, **columns) -> Meal:
    """A meal with a live dish, the way the day list reads it."""
    meal, _item = _eat(db, owner, _dish(db, owner, name), when, **columns)
    return meal


def _meal_on_day(db, owner) -> list:
    db.expire_all()
    return list(db.query(Meal).filter(Meal.user_id == owner.id).order_by(Meal.id))


def _local_day(meal) -> date:
    moment = meal.meal_time
    if moment.tzinfo is None:  # SQLite drops it
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(ZONE).date()


def _ask(db, owner) -> dict | None:
    db.expire_all()
    return (db.get(User, owner.id).settings or {}).get("ask")


# --- O1a, the empty day -------------------------------------------------------


def test_an_empty_day_says_so_and_offers_to_add_to_it(db, owner):
    shown = _shown(f"ml:dl:{SUN}:t")
    assert shown["text"] == "<b>🍽 Приёмы, вс 25 октября</b>\nВ этом дне записей нет."
    assert _labels(shown) == [
        ["➕ Приём в этот день"],
        ["‹ сб 24", "пн 26 ›"],
        ["⬅️ Назад: сегодня"],
    ]
    assert _data(shown) == [
        [f"ml:da:{SUN}:t"],
        ["ml:dl:20261024:t", "ml:dl:20261026:t"],
        ["st:t"],
    ]


def test_control_a_day_with_a_meal_offers_no_add(db, owner):
    _plain(db, owner, "суп", datetime(2026, 10, 25, 5, 0, tzinfo=UTC))
    assert "➕ Приём в этот день" not in sum(_labels(_shown(f"ml:dl:{SUN}:t")), [])


# --- O1b, the wait for it ----------------------------------------------------


@pytest.mark.parametrize("origin", ["t", "y", "w"])
def test_the_add_asks_with_the_day_named_and_a_way_back(db, owner, origin):
    shown = _shown(f"ml:da:{SUN}:{origin}")
    assert shown["text"] == (
        "<b>➕ Приём, вс 25 октября</b>\n"
        "Напиши или пришли, что было. Запись будет в этом дне, без времени."
    )
    assert _labels(shown) == [["⬅️ Приёмы дня"]]
    assert _data(shown) == [[f"ml:dl:{SUN}:{origin}"]]
    ask = _ask(db, owner)
    assert ask["kind"] == meal_card.DAY_ADD == "day_add"
    assert ask["day"] == "2026-10-25"


def _only_new_meal(db, owner) -> Meal:
    meals = _meal_on_day(db, owner)
    assert len(meals) == 1, meals
    return meals[0]


def test_the_next_text_is_a_meal_of_that_day_without_a_time(db, owner, reader):
    tap(f"ml:da:{SUN}:t")
    send(text("суп с курицей"))
    meal = _only_new_meal(db, owner)
    assert _local_day(meal) == date(2026, 10, 25)
    assert meal.time_precision == "day_only"
    assert meal.time_source == "edited"  # a hand's: nothing the read says moves it
    assert _ask(db, owner) is None
    shown = _shown(f"ml:dl:{SUN}:t")
    assert shown["text"].splitlines()[1].startswith("без времени · ")
    assert sum(_labels(shown), [])[0] == "без времени"


def test_the_next_photo_is_a_meal_of_that_day_without_a_time(db, owner, reader):
    tap(f"ml:da:{SUN}:t")
    send(photo(caption="утром"))
    meal = _only_new_meal(db, owner)
    assert _local_day(meal) == date(2026, 10, 25)
    assert meal.time_precision == "day_only"
    assert meal.photos == ["big"]


def test_a_voice_said_later_does_not_move_it_off_the_day(db, owner, reader):
    tap(f"ml:da:{SUN}:t")
    send(_voice())
    meal = _only_new_meal(db, owner)
    assert _local_day(meal) == date(2026, 10, 25)
    assert meal.time_precision == "day_only"


def test_a_dish_already_known_by_name_lands_on_that_day_too(db, owner, reader):
    _dish(db, owner, "омлет")
    tap(f"ml:da:{SUN}:t")
    send(text("омлет"))
    meal = _only_new_meal(db, owner)
    assert _local_day(meal) == date(2026, 10, 25)
    assert meal.time_precision == "day_only"
    assert reader.text == []  # an exact name goes by no read


def test_time_words_do_not_move_it_and_are_not_the_dish(db, owner, reader):
    tap(f"ml:da:{SUN}:t")
    send(text("вчера вечером суп"))
    meal = _only_new_meal(db, owner)
    assert _local_day(meal) == date(2026, 10, 25)
    assert meal.time_precision == "day_only"
    assert "вчера" not in (meal.description or "")


def test_the_answer_retires_the_asking_keyboard(db, owner, reader):
    tap(f"ml:da:{SUN}:t", 4900)
    wire = send(text("суп"))
    assert any(
        name == "editMessageReplyMarkup" and params["message_id"] == 4900
        for name, params in wire.calls
    )


def test_control_only_the_next_one_goes_to_that_day(db, owner, reader):
    tap(f"ml:da:{SUN}:t")
    send(text("суп"), text("чай с мятой"))
    first, second = _meal_on_day(db, owner)
    assert _local_day(first) == date(2026, 10, 25)
    assert _local_day(second) != date(2026, 10, 25)
    assert second.time_precision == "exact"


def test_going_back_closes_the_wait(db, owner, reader):
    tap(f"ml:da:{SUN}:t")
    assert _ask(db, owner) is not None
    tap(f"ml:dl:{SUN}:t")
    assert _ask(db, owner) is None
    send(text("суп"))
    assert _only_new_meal(db, owner).time_precision == "exact"


def test_a_menu_label_or_a_command_is_no_answer(db, owner, reader):
    tap(f"ml:da:{SUN}:t")
    entity = {"type": "bot_command", "offset": 0, "length": 6}
    send(text("/start", entities=[entity]))
    assert _meal_on_day(db, owner) == []
    assert _ask(db, owner) is None
    tap(f"ml:da:{SUN}:t")
    send(text("⭐ Избранное"))
    assert _meal_on_day(db, owner) == []
    assert _ask(db, owner) is None


def test_an_old_wait_is_no_wait(db, owner, reader):
    tap(f"ml:da:{SUN}:t")
    ask = _ask(db, owner)
    user = db.get(User, owner.id)
    old = datetime.now(UTC) - meal_card.ASK_TTL - timedelta(minutes=1)
    user.settings = {**user.settings, "ask": {**ask, "at": old.isoformat()}}
    db.commit()
    send(text("суп"))
    meal = _only_new_meal(db, owner)
    assert meal.time_precision == "exact"
    assert _local_day(meal) != date(2026, 10, 25)


def test_with_no_subscription_nothing_is_written(db, owner, reader, monkeypatch):
    tap(f"ml:da:{SUN}:t")

    async def locked(_id):
        return False

    monkeypatch.setattr(tg, "check_subscription", locked)
    wire = send(text("суп"))
    assert _meal_on_day(db, owner) == []
    assert any(tg.LOCK_TEXT == said for said in wire.seen())


def test_the_same_message_delivered_again_is_one_meal(db, owner, reader):
    tap(f"ml:da:{SUN}:t")
    message = text("суп")
    send(message)
    tap(f"ml:da:{SUN}:t")
    send(message)
    assert len(_meal_on_day(db, owner)) == 1


def test_a_day_ahead_cannot_be_added_to(db, owner, reader):
    ahead = (_today() + timedelta(days=2)).strftime("%Y%m%d")
    tap(f"ml:da:{ahead}:t")
    assert _ask(db, owner) is None
    tap(f"ml:da:{_today():%Y%m%d}:t")  # control: today can
    assert _ask(db, owner) is not None


def test_a_broken_day_is_no_wait_and_no_crash(db, owner, reader):
    tap("ml:da:20261345:t")
    assert _ask(db, owner) is None


# --- O13a, the week's way in --------------------------------------------------


def test_the_week_opens_the_newest_day_with_a_meal(db, owner):
    newest = _today() - timedelta(days=2)
    _plain(db, owner, "суп", _at(_today() - timedelta(days=4)))
    _plain(db, owner, "щи", _at(newest))
    shown = _shown("st:w")
    assert _labels(shown)[-2:] == [
        ["🔢 Цифры", "🍽 Приёмы по дням"],
        ["Сегодня", "Вчера", "✓ 7 дней"],
    ]
    data = _on(shown, "🍽 Приёмы по дням")
    assert data == f"ml:dl:{newest:%Y%m%d}:w"
    day = _shown(data)
    assert f"{stats_screen._card_title(newest)}</b>" in day["text"]
    assert _on(day, "⬅️ Назад: 7 дней") == "st:w"
    earlier = _shown(_on(day, f"‹ {meal_card._day_label(newest - timedelta(days=1))}"))
    assert "⬅️ Назад: 7 дней" in sum(_labels(earlier), [])  # kept while walking


def test_the_week_with_no_meal_has_no_way_in(db, owner):
    assert "🍽 Приёмы по дням" not in sum(_labels(_shown("st:w")), [])


def test_with_numbers_always_on_the_row_holds_only_the_way_in(db, owner):
    _plain(db, owner, "суп", _at(_today() - timedelta(days=1)))
    user = db.get(User, owner.id)
    user.settings = {**(user.settings or {}), SHOW_NUMBERS: True}
    db.commit()
    assert _labels(_shown("st:w"))[-2:] == [
        ["🍽 Приёмы по дням"],
        ["Сегодня", "Вчера", "✓ 7 дней"],
    ]


def test_the_sundays_message_has_no_button(db, owner):
    """It keeps its own week, days after it was sent; the tabs are not on it either."""
    monday = _today() - timedelta(days=_today().weekday() + 7)
    _plain(db, owner, "суп", _at(monday + timedelta(days=1)))
    summary = _shown(f"st:{stats_screen.SUMMARY}:{monday:%Y%m%d}")
    assert "🍽 Приёмы по дням" not in sum(_labels(summary), [])
    assert "7 дней" not in sum(_labels(summary), [])  # control: it is that message


# --- 10.3, the day list, line by line --------------------------------------------


def test_the_walk_crosses_a_month_both_ways(db, owner):
    first = _shown("ml:dl:20261001:y")
    assert first["text"].startswith("<b>🍽 Приёмы, чт 1 октября</b>")
    assert _on(first, "‹ ср 30") == "ml:dl:20260930:y"
    assert _on(first, "пт 2 ›") == "ml:dl:20261002:y"
    last = _shown("ml:dl:20260930:y")
    assert last["text"].startswith("<b>🍽 Приёмы, ср 30 сентября</b>")
    assert _on(last, "чт 1 ›") == "ml:dl:20261001:y"
    assert _on(last, "⬅️ Назад: вчера") == "st:y"


def test_no_step_past_today(db, owner):
    today, yesterday = _today(), _today() - timedelta(days=1)
    on_today = sum(_labels(_shown(f"ml:dl:{today:%Y%m%d}:t")), [])
    assert not [label for label in on_today if label.endswith("›")]
    assert [label for label in on_today if label.startswith("‹")]
    # Control: the day before does step, to today.
    before = sum(_labels(_shown(f"ml:dl:{yesterday:%Y%m%d}:t")), [])
    assert f"{meal_card._day_label(today)} ›" in before


def test_a_hard_day_reads_the_same_with_no_comment(db, owner, hard_day):
    day = date(2026, 10, 30)
    _plain(db, owner, "хлопья", _at(day, 4, 10))
    _plain(db, owner, "чипсы, три пачки", _at(day, 23, 40))
    photo_only = Meal(user_id=owner.id, meal_time=_at(day, 13, 20), photos=["P"])
    db.add(photo_only)
    db.commit()
    shown = _shown("ml:dl:20261030:t")
    assert shown["text"] == "\n".join(
        [
            "<b>🍽 Приёмы, пт 30 октября</b>",
            "4:10 · хлопья",
            "13:20 · 📷 фото сохранено",
            "23:40 · чипсы, три пачки",
        ]
    )
    assert _labels(shown)[0] == ["4:10", "13:20", "23:40"]


def test_control_on_an_ordinary_day_the_hint_is_there(db, owner):
    _plain(db, owner, "суп", _at(date(2026, 10, 30), 13, 20))
    assert "Нажми на время" in _shown("ml:dl:20261030:t")["text"]


def _tap_on_photo(data: str):
    box = {}

    async def steps(application, wire):
        box["wire"] = wire
        message = _message()
        await application.process_update(
            Update.de_json(
                {
                    "update_id": message["message_id"],
                    "callback_query": {
                        "id": "1",
                        "from": message["from"],
                        "chat_instance": "c",
                        "data": data,
                        "message": {
                            "message_id": 4900,
                            "date": NOW_TS,
                            "chat": message["chat"],
                            "photo": PHOTO,
                            "caption": "x",
                        },
                    },
                },
                application.bot,
            )
        )

    asyncio.run(_with_app(steps))
    return box["wire"]


def test_from_a_picture_card_the_list_comes_as_text_and_the_picture_goes(db, owner):
    _plain(db, owner, "суп", _at(date(2026, 10, 26)))
    wire = _tap_on_photo("ml:dl:20261026:t")
    assert wire.edits() == []  # a photo cannot be edited into text
    assert wire.sent()[-1]["text"].startswith("<b>🍽 Приёмы, пн 26 октября</b>")
    assert [p["message_id"] for n, p in wire.calls if n == "deleteMessage"] == [4900]


def test_from_the_text_list_back_comes_a_picture_and_the_text_goes(
    db, owner, tmp_path, monkeypatch
):
    for name in ("in", "out"):
        (tmp_path / name).mkdir()
    (tmp_path / "heartbeat").write_text(str(int(datetime.now().timestamp())))
    delivery = CardDelivery(PictureRenderer(tmp_path), fast_wait=0.01)
    monkeypatch.setattr(tg, "_card_delivery", delivery)
    plain = Wire._result

    def with_photos(self, name, params):
        if name != "sendPhoto":
            return plain(self, name, params)
        chat = {"id": 42, "type": "private"}
        return {"message_id": 6000, "date": NOW_TS, "chat": chat, "photo": PHOTO}

    monkeypatch.setattr(Wire, "_result", with_photos)
    wire = tap("st:t")
    assert [n for n, _p in wire.calls if n == "editMessageText"] == []
    assert [p["message_id"] for n, p in wire.calls if n == "deleteMessage"] == [4900]
    assert [n for n, _p in wire.calls if n == "sendPhoto"] == ["sendPhoto"]
    assert json.loads(next((tmp_path / "in").glob("*.json")).read_text())


def test_control_with_no_renderer_the_tab_edits_in_place(db, owner):
    wire = tap("st:t")
    assert len(wire.edits()) == 1
    assert [n for n, _p in wire.calls if n == "deleteMessage"] == []
