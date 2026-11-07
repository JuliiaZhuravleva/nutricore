"""🔗 Объединить, ➖ Отделить, 📋 Прислано, 🔁 Прочитать заново, 🏷 Группы and an edited
message: M1c, M8, M8a, M8b, M11, M13, M14, through the real handlers.

Every test sends a real ``Update`` into ``create_bot_application()``. The model is a
fake, so what is asserted is what would reach Telegram and what the rows hold. No
network, no paid call.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from telegram import Update
from telegram.error import BadRequest

import bot as bot_entry
from app import main as app_main
from app.core.config import settings
from app.crud.crud_meal_item import crud_meal_item
from app.models import Meal, MealItem, MealMessage, MealSource, RecordEvent, User
from app.services import (
    capture,
    close_row,
    explain_screen,
    food_groups,
    meal_card,
    meal_items,
    next_step,
)
from app.services import telegram as tg
from tests.test_capture_failures import _tap_update, _toasts
from tests.test_capture_router import (  # noqa: F401 (fixtures)
    CHAT,
    NOW,
    NOW_TS,
    NUTRITION,
    OWNER,
    Session,
    Wire,
    _message,
    _with_app,
    clock,
    db,
    owner,
    photo,
    reader,
    send,
    text,
)

STALE_MERGED = "<i>Этот приём объединён с приёмом пн 26 октября, 13:20.</i>"
STALE_DELETED = "<i>Этого приёма уже нет, вот сегодняшний день.</i>"
M11 = (
    "Сообщение изменено. В дневнике пока прежняя запись. "
    "Кнопка «🔁 Прочитать заново» прочитает новый текст."
)
M13_HELP = (
    "Здесь твои сообщения, как они пришли. Исправления меняют приём, а сообщения "
    "остаются.\n"
    "«🔁 Прочитать заново» читает их ещё раз, твои исправления остаются.\n"
    "«➖ Отделить» делает сообщение отдельным приёмом."
)
M13_FIRST_LINE = (
    "Здесь твои сообщения, как они пришли. Исправления меняют приём, а сообщения "
    "остаются."
)
M14 = (
    "<b>🏷 Группы в этой записи</b>\n"
    "Можно отметить, что было на тарелке. Это видно только на карточках дня и недели."
)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(settings, "CUSTOM_EMOJI", None)
    monkeypatch.setattr(capture, "ALBUM_WAIT_SECONDS", 0.05)
    numbers = itertools.count(5000, 500)
    start = Wire.__init__

    def go_on(self):
        start(self)
        self.next_id = next(numbers)

    monkeypatch.setattr(Wire, "__init__", go_on)
    monkeypatch.setattr(tg._card_delivery, "_screens", {})


@pytest.fixture(autouse=True)
def _hint_is_old(db, owner):
    owner.settings = {**(owner.settings or {}), "capture_hint_seen": True}
    db.commit()


async def _drained() -> None:
    while tg._meal_reads:
        await asyncio.gather(*list(tg._meal_reads))


def _run(steps) -> Wire:
    box = {}

    async def wrapped(application, wire):
        box["wire"] = wire
        await steps(application, wire)
        await _drained()

    asyncio.run(_with_app(wrapped))
    return box["wire"]


def tap(data: str, message_id: int = 4900) -> Wire:
    tg._card_delivery._screens.clear()

    async def steps(application, _wire):
        await application.process_update(_tap_update(application, data, message_id))

    return _run(steps)


def edited(message: dict) -> Wire:
    """A real ``edited_message`` update through the application."""

    async def steps(application, _wire):
        update = Update.de_json(
            {
                "update_id": message["message_id"] + 900000,
                "edited_message": {**message, "edit_date": NOW_TS + 300},
            },
            application.bot,
        )
        assert update.edited_message is not None and update.message is None
        await application.process_update(update)

    return _run(steps)


def _keyboard(params: dict) -> list:
    markup = params.get("reply_markup")
    if markup is None:
        return []
    markup = json.loads(markup) if isinstance(markup, str) else markup
    return markup["inline_keyboard"]


def _datas(params: dict) -> list:
    return [[b["callback_data"] for b in row] for row in _keyboard(params)]


def _labels(params: dict) -> list:
    return [[b["text"] for b in row] for row in _keyboard(params)]


def _screen(wire: Wire) -> dict:
    return wire.edits()[0]


def _at(*foods, **extra) -> dict:
    return {**NUTRITION, "foods": list(foods), **extra}


def _meal_from(db, reader, words: str, *foods, ago: int = 0, **numbers) -> Meal:
    """A real text message, read by the fake model. ``ago``: minutes before NOW."""
    reader.answer = _at(*(foods or (words,)), **numbers)
    send(text(words, date=NOW_TS - ago * 60))
    db.expire_all()
    return db.scalars(select(Meal).order_by(Meal.id.desc())).first()


def _only_source(db, meal_id: int) -> MealSource:
    return db.scalars(
        select(MealSource).where(MealSource.origin_meal_id == meal_id)
    ).first()


def _names(db, meal) -> list:
    db.expire_all()
    return [item.name for item in crud_meal_item.live(db, meal.id)]


def _get(db, meal_id: int) -> Meal:
    db.expire_all()
    return db.get(Meal, meal_id)


def _totals(meal: Meal) -> tuple:
    return (meal.calories, meal.proteins, meal.fats, meal.carbohydrates)


def _budget(params: dict) -> None:
    """Section 2.2: at most four action rows and a back, label budgets, 64 bytes."""
    rows = _keyboard(params)
    assert len(rows) <= 5
    limit = {1: 34, 2: 20, 3: 13}
    for row in rows:
        assert len(row) <= 3
        for button in row:
            assert len(button["text"]) <= limit[len(row)], button["text"]
            assert len(button["callback_data"].encode()) <= 64


# --- M1c: the row that joins nothing by itself ------------------------------------


def test_m1c_a_meal_soon_after_another_offers_the_join_and_joins_nothing(
    db, owner, reader
):
    first = _meal_from(db, reader, "суп", ago=20)
    reader.answer = _at("сыр")
    wire = send(text("сыр", date=NOW_TS))
    db.expire_all()
    second = db.scalars(select(Meal).order_by(Meal.id.desc())).first()
    line = wire.sent()[-1]
    assert line["text"] == "Записано: сыр.\nСчитаю…"
    assert _labels(line)[-1] == ["🔗 Объединить с пн 26 окт, 13:00"]
    assert _datas(line)[-1] == [f"ml:j:{first.id}:{second.id}:r"]
    _budget(line)
    assert second.merged_into_id is None
    assert _get(db, first.id).merged_into_id is None


def test_m1c_two_hours_later_there_is_no_row(db, owner, reader):
    _meal_from(db, reader, "суп", ago=120)
    reader.answer = _at("сыр")
    line = send(text("сыр", date=NOW_TS)).sent()[-1]
    assert not any("ml:j:" in data for row in _datas(line) for data in row)
    # The control: 89 minutes is inside the window.
    reader.answer = _at("чай")
    line = send(text("чай", date=NOW_TS + 89 * 60)).sent()[-1]
    assert any("ml:j:" in data for row in _datas(line) for data in row)


def test_m1c_the_very_first_meal_has_no_row(db, owner, reader):
    reader.answer = _at("сыр")
    line = send(text("сыр", date=NOW_TS)).sent()[-1]
    assert len(_datas(line)) == 1


def test_m1c_after_a_photo_read_the_line_carries_the_row(
    db, owner, reader, monkeypatch
):
    monkeypatch.setattr(tg, "_download_image", AsyncMock(return_value="data:x"))
    first = _meal_from(db, reader, "суп", ago=20)
    reader.answer = _at("сыр")
    wire = send(photo(date=NOW_TS))
    db.expire_all()
    second = db.scalars(select(Meal).order_by(Meal.id.desc())).first()
    assert wire.edits()[-1]["text"] == "Записано: сыр."
    assert [f"ml:j:{first.id}:{second.id}:r"] in _datas(wire.edits()[-1])


def test_m1c_stays_at_every_mode(db, owner, reader):
    owner.settings = {**owner.settings, next_step.ENERGY: next_step.SIMPLE}
    db.commit()
    first = _meal_from(db, reader, "суп", ago=20)
    reader.answer = _at("сыр")
    line = send(text("сыр", date=NOW_TS)).sent()[-1]
    second = db.scalars(select(Meal).order_by(Meal.id.desc())).first()
    assert [f"ml:j:{first.id}:{second.id}:r"] in _datas(line)


# --- M8: join ---------------------------------------------------------------------


def _two(db, reader) -> tuple:
    first = _meal_from(db, reader, "рис", ago=20)
    second = _meal_from(db, reader, "сыр", ago=0)
    return first, second


def test_m8_the_earlier_meal_survives_with_both_lists_in_one_message(db, owner, reader):
    first, second = _two(db, reader)
    wire = tap(f"ml:j:{first.id}:{second.id}:r")
    db.expire_all()
    first, second = db.get(Meal, first.id), db.get(Meal, second.id)
    screen = _screen(wire)
    assert screen["message_id"] == 4900
    assert screen["text"] == ("Объединено с приёмом пн 26 октября, 13:00: рис и сыр.")
    source = _only_source(db, second.id)
    assert _datas(screen) == [
        [close_row.CALLBACK],
        [f"ml:z:{source.id}:r", f"ml:o:{first.id}:r"],
    ]
    assert _labels(screen) == [[close_row.LABEL], ["➖ Отделить", "✏️ Исправить"]]
    assert second.merged_into_id == first.id
    assert second.meal_time is not None
    assert source.meal_id == first.id
    assert _names(db, first) == ["рис", "сыр"]
    assert first.calories == 2 * NUTRITION["calories"]
    event = db.scalars(select(RecordEvent).where(RecordEvent.kind == "join")).one()
    assert event.meal_id == first.id
    assert event.after["join"]["from"] == second.id
    assert not reader.text[2:]  # nothing is read for a join


def test_m8_the_survivor_is_the_earlier_whichever_way_the_ids_go(db, owner, reader):
    first, second = _two(db, reader)
    tap(f"ml:j:{second.id}:{first.id}:r")
    assert _get(db, second.id).merged_into_id == first.id
    assert _get(db, first.id).merged_into_id is None


def test_m8_a_pair_already_joined_says_so_and_writes_nothing(db, owner, reader):
    first, second = _two(db, reader)
    tap(f"ml:j:{first.id}:{second.id}:r")
    count = db.query(RecordEvent).count()
    wire = tap(f"ml:j:{first.id}:{second.id}:r")
    assert _toasts(wire) == ["Уже вместе"]
    assert not wire.edits()
    db.expire_all()
    assert db.query(RecordEvent).count() == count


def test_m8_the_target_gone_shows_the_card_with_the_notice(db, owner, reader):
    first, second = _two(db, reader)
    meal_items.delete_meal(db, owner.id, first.id)
    wire = tap(f"ml:j:{first.id}:{second.id}:r")
    assert capture.TARGET_GONE in _screen(wire)["text"]
    assert f"ml:d:{second.id}:r" in str(_datas(_screen(wire)))
    assert _get(db, second.id).merged_into_id is None


def test_m8_both_gone_is_today(db, owner, reader):
    first, second = _two(db, reader)
    meal_items.delete_meal(db, owner.id, first.id)
    meal_items.delete_meal(db, owner.id, second.id)
    wire = tap(f"ml:j:{first.id}:{second.id}:r")
    assert STALE_DELETED in _screen(wire)["text"]


def test_m8_a_foreign_meal_is_todays_card_and_a_warning(db, owner, reader, caplog):
    from app.models import User

    first, _second = _two(db, reader)
    stranger = User(telegram_id=777, settings={})
    db.add(stranger)
    db.commit()
    other = meal_items.save_meal(
        db,
        meal_items.MealCreate(description="чужое", meal_time=NOW, sent_at=NOW),
        stranger.id,
    )
    with caplog.at_level(logging.WARNING):
        wire = tap(f"ml:j:{first.id}:{other.id}:r")
    assert "unknown or foreign" in caplog.text
    assert "рис" not in _screen(wire)["text"]
    assert _get(db, other.id).merged_into_id is None
    # Known-positive: the same tap with this person's own meal joins.
    tap(f"ml:j:{first.id}:{_second.id}:r")
    assert _get(db, _second.id).merged_into_id == first.id


def test_m8_a_tap_on_a_merged_meal_opens_the_survivor_with_the_notice(
    db, owner, reader
):
    first = _meal_from(db, reader, "рис", ago=0)
    second = _meal_from(db, reader, "сыр", ago=-10)
    tap(f"ml:j:{first.id}:{second.id}:r")
    wire = tap(f"ml:o:{second.id}:t")
    screen = _screen(wire)
    assert STALE_MERGED in screen["text"]
    assert f"ml:d:{first.id}" in str(_datas(screen))
    # Known-positive: a meal deleted, not merged, keeps the other notice.
    meal_items.delete_meal(db, owner.id, first.id)
    assert STALE_DELETED in _screen(tap(f"ml:o:{first.id}:t"))["text"]


def test_m8_the_surviving_line_is_retouched_silently(db, owner, reader):
    first = _meal_from(db, reader, "рис", ago=20)
    line = db.scalars(
        select(MealMessage).where(
            MealMessage.meal_id == first.id, MealMessage.view != "source"
        )
    ).one()
    second = _meal_from(db, reader, "сыр", ago=0)
    wire = tap(f"ml:j:{first.id}:{second.id}:r")
    edits = {p["message_id"]: p["text"] for p in wire.edits()}
    assert edits[line.message_id] == "Записано: рис и сыр."
    assert not wire.sent()


# --- M8a: the round trip ----------------------------------------------------------


def _three(db, reader) -> tuple:
    a = _meal_from(db, reader, "рис", ago=40, calories=100.0)
    b = _meal_from(db, reader, "сыр", ago=20, calories=200.0)
    c = _meal_from(db, reader, "чай", ago=0, calories=30.0)
    return a, b, c


def test_the_join_round_trip(db, owner, reader):
    """Join, join, separate the first: times, sources and totals of all restored,
    typed fixes kept."""
    a, b, c = _three(db, reader)
    a_id, b_id, c_id = a.id, b.id, c.id
    times = {m.id: m.meal_time for m in (a, b, c)}
    totals = {m.id: _totals(m) for m in (a, b, c)}
    tap(f"ml:j:{a_id}:{b_id}:r")
    tap(f"ml:j:{a_id}:{c_id}:r")
    assert _names(db, _get(db, a_id)) == ["рис", "сыр", "чай"]
    assert _get(db, a_id).calories == 330.0

    # A typed fix on the second meal's dish, while it is inside the first.
    cheese = next(i for i in crud_meal_item.live(db, a_id) if i.name == "сыр")
    meal_items.set_quantity(db, owner.id, cheese.id, 2.0)
    assert _get(db, a_id).calories == 530.0

    source_b = _only_source(db, b_id)
    wire = tap(f"ml:z:{source_b.id}:r")
    screen = _screen(wire)
    assert screen["text"] == (
        "Отделено: сыр. Теперь это отдельный приём, пн 26 октября, 13:00."
    )
    _budget(screen)
    db.expire_all()
    a, b, c = db.get(Meal, a_id), db.get(Meal, b_id), db.get(Meal, c_id)
    assert b.deleted_at is None and b.merged_into_id is None
    assert b.meal_time == times[b_id]
    assert source_b.meal_id == b_id
    assert _names(db, b) == ["сыр"]
    only = crud_meal_item.live(db, b_id)[0]
    assert only.factor == 2.0 and only.by_user  # the fix stayed
    assert b.calories == 400.0  # its own, with the fix
    assert _names(db, a) == ["рис", "чай"]
    assert a.calories == 130.0
    assert c.merged_into_id == a_id and c.deleted_at is not None

    # The second one back too: the first is exactly what it was.
    source_c = _only_source(db, c_id)
    tap(f"ml:z:{source_c.id}:r")
    db.expire_all()
    a, c = db.get(Meal, a_id), db.get(Meal, c_id)
    assert _totals(a) == totals[a_id]
    assert _totals(c) == totals[c_id]
    assert a.meal_time == times[a_id] and c.meal_time == times[c_id]
    assert _names(db, a) == ["рис"]
    assert _names(db, c) == ["чай"]
    assert a.description == "рис"


def test_the_round_trip_of_plates(db, owner, reader):
    """Two plates: what the model read of each stays with each."""
    a = _meal_from(db, reader, "обед", "рис", "курица", ago=30, calories=500.0)
    b = _meal_from(db, reader, "ужин", "хлеб", "чипсы", ago=0, calories=300.0)
    assert a.numbers_basis == b.numbers_basis == "plate"
    before = (_totals(a), a.plate_totals, _totals(b), b.plate_totals)
    a_id, b_id = a.id, b.id
    tap(f"ml:j:{a_id}:{b_id}:r")
    assert _get(db, a_id).calories == 800.0
    tap(f"ml:z:{_only_source(db, b_id).id}:r")
    a, b = _get(db, a_id), _get(db, b_id)
    assert (_totals(a), a.plate_totals, _totals(b), b.plate_totals) == before
    assert a.numbers_basis == b.numbers_basis == "plate"
    assert _names(db, a) == ["рис", "курица"]
    assert _names(db, b) == ["хлеб", "чипсы"]


def test_a_plate_on_a_meal_of_items_comes_off_clean(db, owner, reader):
    a = _meal_from(db, reader, "рис", ago=30, calories=100.0)
    b = _meal_from(db, reader, "ужин", "хлеб", "чипсы", ago=0, calories=300.0)
    a_id, b_id = a.id, b.id
    tap(f"ml:j:{a_id}:{b_id}:r")
    assert _get(db, a_id).calories == 400.0
    tap(f"ml:z:{_only_source(db, b_id).id}:r")
    a = _get(db, a_id)
    assert a.numbers_basis == "items" and a.calories == 100.0
    assert not a.plate_totals


def test_nothing_joined_says_so(db, owner, reader):
    first, second = _two(db, reader)
    wire = tap(f"ml:z:{_only_source(db, second.id).id}:r")
    assert _toasts(wire) == ["Уже отдельно"]
    assert not wire.edits()
    # Known-positive: the same button once they are joined does separate.
    tap(f"ml:j:{first.id}:{second.id}:r")
    wire = tap(f"ml:z:{_only_source(db, second.id).id}:r")
    assert _screen(wire)["text"].startswith("Отделено: сыр.")
    # And again it is over.
    assert _toasts(tap(f"ml:z:{_only_source(db, second.id).id}:r")) == ["Уже отдельно"]


def test_m8a_offers_the_way_back_and_a_delete(db, owner, reader):
    first, second = _two(db, reader)
    tap(f"ml:j:{first.id}:{second.id}:r")
    screen = _screen(tap(f"ml:z:{_only_source(db, second.id).id}:r"))
    item = crud_meal_item.live(db, second.id)[0]
    assert _datas(screen) == [
        [f"ml:j:{first.id}:{second.id}:r"],
        [f"ml:i:{item.id}:r", f"ml:d:{second.id}:r"],
    ]
    assert _labels(screen) == [
        ["🔗 Объединить с пн 26 окт, 13:00"],
        ["✏️ Исправить", "🗑 Удалить"],
    ]


def test_a_reply_joined_with_its_own_message_comes_back(db, owner, reader):
    """M5: the ➖ Отделить of a corrective message, never a new way of joining."""
    first = _meal_from(db, reader, "рис", ago=5)
    line = db.scalars(
        select(MealMessage).where(
            MealMessage.meal_id == first.id, MealMessage.view != "source"
        )
    ).one()
    reader.answer = _at("сыр")
    wire = send(
        text(
            "ещё сыр",
            date=NOW_TS,
            reply_to_message={
                "message_id": line.message_id,
                "date": NOW_TS,
                "chat": {"id": CHAT, "type": "private"},
                "text": "x",
            },
        )
    )
    assert wire.sent()[-1]["text"].startswith("Добавлено к приёму")
    data = _datas(wire.sent()[-1])[1][0]
    assert data.startswith("ml:z:")
    screen = _screen(tap(data))
    assert screen["text"].startswith("Отделено: сыр.")
    assert _names(db, _get(db, first.id)) == ["рис"]
    assert len(_names(db, _get(db, first.id))) == 1


def test_a_second_message_after_a_separation_replies_to_the_meal_it_means(
    db, owner, reader
):
    first, second = _two(db, reader)
    source = _only_source(db, second.id)
    tap(f"ml:j:{first.id}:{second.id}:r")
    tap(f"ml:z:{source.id}:r")
    row = db.scalars(
        select(MealMessage).where(MealMessage.message_id == source.message_id)
    ).one()
    assert row.meal_id == second.id


def test_a_chain_of_joins_comes_apart_one_at_a_time(db, owner, reader):
    """c into b, b into a: each comes back with what was its own."""
    a, b, c = _three(db, reader)
    a_id, b_id, c_id = a.id, b.id, c.id
    tap(f"ml:j:{b_id}:{c_id}:r")
    tap(f"ml:j:{a_id}:{b_id}:r")
    assert _names(db, _get(db, a_id)) == ["рис", "сыр", "чай"]
    assert _get(db, c_id).merged_into_id == a_id
    tap(f"ml:z:{_only_source(db, c_id).id}:r")
    assert _names(db, _get(db, c_id)) == ["чай"]
    assert _names(db, _get(db, a_id)) == ["рис", "сыр"]
    tap(f"ml:z:{_only_source(db, b_id).id}:r")
    assert _names(db, _get(db, b_id)) == ["сыр"]
    assert _names(db, _get(db, a_id)) == ["рис"]
    assert _get(db, a_id).calories == 100.0


def test_a_replaced_dish_goes_back_with_its_meal(db, owner, reader):
    """A fix made while joined belongs to the dish's own meal."""
    first, second = _two(db, reader)
    tap(f"ml:j:{first.id}:{second.id}:r")
    cheese = next(i for i in crud_meal_item.live(db, first.id) if i.name == "сыр")
    change = meal_items.replace_item(db, owner.id, cheese.id, name="творог")
    assert change.status == meal_items.DONE
    tap(f"ml:z:{_only_source(db, second.id).id}:r")
    assert _names(db, _get(db, second.id)) == ["творог"]
    assert _names(db, _get(db, first.id)) == ["рис"]


def test_a_dish_added_while_joined_stays_with_the_survivor(db, owner, reader):
    first, second = _two(db, reader)
    tap(f"ml:j:{first.id}:{second.id}:r")
    meal_items.add_item(db, owner.id, first.id, name="салат")
    tap(f"ml:z:{_only_source(db, second.id).id}:r")
    assert _names(db, _get(db, first.id)) == ["рис", "салат"]
    assert _names(db, _get(db, second.id)) == ["сыр"]


def test_a_deleted_survivor_separates_nothing(db, owner, reader):
    first, second = _two(db, reader)
    tap(f"ml:j:{first.id}:{second.id}:r")
    meal_items.delete_meal(db, owner.id, first.id)
    wire = tap(f"ml:z:{_only_source(db, second.id).id}:r")
    assert STALE_DELETED in _screen(wire)["text"]
    assert _get(db, second.id).merged_into_id == first.id


@pytest.fixture
def same_refused(monkeypatch):
    """Telegram's own refusal of an edit that changes nothing, across every wire of
    the test: «message is not modified». Each refusal is kept."""
    shown, refused = {}, []
    original = Wire.do_request

    async def do_request(self, url, method, request_data=None, **kwargs):
        name = url.rsplit("/", 1)[-1]
        params = dict(request_data.parameters) if request_data else {}
        if name == "editMessageText":
            mid = params.get("message_id")
            now = (params.get("text"), params.get("reply_markup"))
            if shown.get(mid) == now:
                refused.append(mid)
                body = {
                    "ok": False,
                    "error_code": 400,
                    "description": "Bad Request: message is not modified: specified "
                    "new message content and reply markup are exactly the same",
                }
                return 400, json.dumps(body).encode()
            shown[mid] = now
        code, body = await original(self, url, method, request_data, **kwargs)
        if name == "sendMessage":
            mid = json.loads(body)["result"]["message_id"]
            shown[mid] = (params.get("text"), params.get("reply_markup"))
        return code, body

    monkeypatch.setattr(Wire, "do_request", do_request)
    return refused


def test_separate_after_a_join_logs_no_failed_edit_of_its_own_line(
    db, owner, reader, same_refused, caplog
):
    """Persona finding: ➖ re-drew the separated meal's own line as it already was, and
    the refusal was logged as «Original line not edited: BadRequest»."""
    first, second = _two(db, reader)
    tap(f"ml:j:{first.id}:{second.id}:r")
    with caplog.at_level(logging.WARNING):
        wire = tap(f"ml:z:{_only_source(db, second.id).id}:r")
    assert _screen(wire)["text"].startswith("Отделено: сыр.")
    assert same_refused  # the known positive: an edit that changed nothing was asked
    assert "Original line not edited" not in caplog.text


def test_a_line_that_cannot_be_edited_is_still_logged(caplog):
    """The control: any other refusal of the retouch is a warning, as before."""
    bot = MagicMock()
    bot.edit_message_text = AsyncMock(side_effect=BadRequest("Message can't be edited"))
    retouch = meal_card.Retouch(7, "x", None)
    with caplog.at_level(logging.WARNING):
        asyncio.run(tg._retouch(bot, CHAT, retouch))
    assert "Original line not edited: BadRequest" in caplog.text
    caplog.clear()
    bot.edit_message_text = AsyncMock(side_effect=BadRequest("Message is not modified"))
    with caplog.at_level(logging.WARNING):
        asyncio.run(tg._retouch(bot, CHAT, retouch))
    assert caplog.text == ""


def test_someone_elses_source_is_refused(db, owner, reader, caplog):
    wire = None
    with caplog.at_level(logging.WARNING):
        wire = tap("ml:z:999:r")
    assert "unknown or foreign" in caplog.text
    assert wire.edits()


# --- N7, M8b ----------------------------------------------------------------------


def test_n7_row_two_and_the_join_appears_only_with_a_neighbour(db, owner, reader):
    alone = _meal_from(db, reader, "обед", "рис", "курица", ago=60 * 40)
    screen = _screen(tap(f"ml:mo:{alone.id}:t"))
    src, gr = f"ml:src:{alone.id}:t", f"ml:gr:{alone.id}:t"
    assert [src, gr] in _datas(screen)
    assert f"ml:jl:{alone.id}:t" not in str(_datas(screen))
    assert _labels(screen)[0] == ["⭐ Весь приём", "🔗 Объединить"][:1]
    _budget(screen)

    near = _meal_from(db, reader, "сыр", ago=60 * 39)
    screen = _screen(tap(f"ml:mo:{alone.id}:t"))
    assert _datas(screen)[0] == [f"ml:fm:{alone.id}:1:t", f"ml:jl:{alone.id}:t"]
    assert _labels(screen)[0] == ["⭐ Весь приём", "🔗 Объединить"]
    assert _labels(screen)[1] == ["📋 Прислано", "🏷 Группы"]
    assert _labels(screen)[-1] == ["⬅️ К приёму"]
    assert screen["text"].startswith("<b>🍽 Ещё: приём ")
    _budget(screen)

    tap(f"ml:j:{alone.id}:{near.id}:t")
    screen = _screen(tap(f"ml:mo:{alone.id}:t"))
    assert _labels(screen)[0] == ["⭐ Весь приём", "➖ Отделить"]
    sid = _only_source(db, near.id).id
    assert _datas(screen)[0][1] == f"ml:z:{sid}:t"


def test_n7_with_one_dish_has_no_star(db, owner, reader):
    first, second = _two(db, reader)
    screen = _screen(tap(f"ml:mo:{first.id}:t"))
    assert _labels(screen)[0] == ["🔗 Объединить"]


def test_m8b_lists_the_neighbours_and_joins_the_one_chosen(db, owner, reader):
    a = _meal_from(db, reader, "рис", ago=200)
    b = _meal_from(db, reader, "хлеб", ago=100)
    c = _meal_from(db, reader, "чипсы", ago=0)
    far = _meal_from(db, reader, "старое", ago=60 * 30)
    screen = _screen(tap(f"ml:jl:{b.id}:t"))
    assert screen["text"] == (
        "<b>🔗 С каким приёмом объединить?</b>\n"
        "пн 26 октября, 10:00 · рис\n"
        "пн 26 октября, 13:20 · чипсы"
    )
    assert _labels(screen) == [["10:00 · рис", "13:20 · чипсы"], ["⬅️ К приёму"]]
    assert _datas(screen) == [
        [f"ml:j:{b.id}:{a.id}:t", f"ml:j:{b.id}:{c.id}:t"],
        [f"ml:o:{b.id}:t"],
    ]
    assert str(far.id) not in str(_datas(screen)[0])
    _budget(screen)
    tap(f"ml:j:{b.id}:{c.id}:t")
    assert _get(db, c.id).merged_into_id == b.id


def test_a_meal_with_no_source_has_no_way_back_so_is_never_the_later_one(
    db, owner, reader
):
    """A legacy meal: ➖ Отделить takes a source, and it has none."""
    legacy = meal_items.save_meal(
        db,
        meal_items.MealCreate(
            description="старое",
            meal_time=NOW + timedelta(minutes=10),
            sent_at=NOW + timedelta(minutes=10),
        ),
        owner.id,
    )
    live = _meal_from(db, reader, "рис", ago=0)
    assert f"ml:j:{live.id}:{legacy.id}" not in str(
        _datas(_screen(tap(f"ml:jl:{live.id}:t")))
    )
    # The known-positive: as the earlier one it may be joined to.
    earlier = meal_items.save_meal(
        db,
        meal_items.MealCreate(
            description="ещё старее",
            meal_time=NOW - timedelta(minutes=10),
            sent_at=NOW - timedelta(minutes=10),
        ),
        owner.id,
    )
    assert f"ml:j:{live.id}:{earlier.id}" in str(
        _datas(_screen(tap(f"ml:jl:{live.id}:t")))
    )
    # And the tap itself refuses the later one, whatever an old button says.
    wire = tap(f"ml:j:{live.id}:{legacy.id}:t")
    assert _get(db, legacy.id).merged_into_id is None
    assert wire.edits()


# --- M13 ---------------------------------------------------------------------------


def _joined_three(db, reader) -> tuple:
    a, b, c = _three(db, reader)
    tap(f"ml:j:{a.id}:{b.id}:t")
    tap(f"ml:j:{a.id}:{c.id}:t")
    return a, b, c


def test_m13_lists_what_came_and_writes_nothing(db, owner, reader):
    a, b, c = _joined_three(db, reader)
    count = db.query(RecordEvent).count()
    wire = tap(f"ml:src:{a.id}:t")
    screen = _screen(wire)
    assert screen["text"] == (
        "<b>📋 Что прислано</b>\n"
        "1. ✏️ «рис», 12:40\n"
        "2. ✏️ «сыр», 13:00\n"
        "3. ✏️ «чай», 13:20\n"
        f"{M13_HELP}"
    )
    sid_b, sid_c = _only_source(db, b.id).id, _only_source(db, c.id).id
    assert _labels(screen) == [
        ["➖ Отделить №2", "➖ Отделить №3"],
        ["🔁 Прочитать заново"],
        ["⬅️ К приёму"],
    ]
    assert _datas(screen) == [
        [f"ml:z:{sid_b}:t", f"ml:z:{sid_c}:t"],
        [f"ml:rr:{a.id}:t"],
        [f"ml:o:{a.id}:t"],
    ]
    _budget(screen)
    db.expire_all()
    assert db.query(RecordEvent).count() == count


def test_m13_a_read_photo_says_so_and_can_be_shown(db, owner, reader, monkeypatch):
    monkeypatch.setattr(tg, "_download_image", AsyncMock(return_value="data:x"))
    reader.answer = _at("суп")
    send(photo())
    meal = db.scalars(select(Meal).order_by(Meal.id.desc())).first()
    screen = _screen(tap(f"ml:src:{meal.id}:t"))
    assert "1. 📷 Фото, 13:20 · оценка по фото\n" in screen["text"]
    assert ["📷 Показать фото", "🔁 Прочитать заново"] in _labels(screen)
    assert [f"ml:sf:{meal.id}", f"ml:rr:{meal.id}:t"] in _datas(screen)

    wire = tap(f"ml:sf:{meal.id}")
    shown = [p for n, p in wire.calls if n == "sendPhoto"]
    assert [p["photo"] for p in shown] == ["big"]
    assert shown[0]["disable_notification"] is True
    assert not wire.edits()
    assert _toasts(wire) == [None]


def test_m13_a_dead_photo_is_a_toast(db, owner, reader, monkeypatch):
    monkeypatch.setattr(tg, "_download_image", AsyncMock(return_value="data:x"))
    reader.answer = _at("суп")
    send(photo())
    meal = db.scalars(select(Meal).order_by(Meal.id.desc())).first()
    start = Wire.do_request

    async def refuse(self, url, method, request_data=None, **kw):
        if url.endswith("sendPhoto"):
            return (
                400,
                json.dumps(
                    {"ok": False, "description": "Bad Request: wrong file identifier"}
                ).encode(),
            )
        return await start(self, url, method, request_data, **kw)

    monkeypatch.setattr(Wire, "do_request", refuse)
    wire = tap(f"ml:sf:{meal.id}")
    assert _toasts(wire) == ["Это фото больше недоступно в Telegram"]


def test_m13_a_network_blip_does_not_call_the_photo_dead(
    db, owner, reader, monkeypatch
):
    monkeypatch.setattr(tg, "_download_image", AsyncMock(return_value="data:x"))
    reader.answer = _at("суп")
    send(photo())
    meal = db.scalars(select(Meal).order_by(Meal.id.desc())).first()
    start = Wire.do_request

    async def blip(self, url, method, request_data=None, **kw):
        if url.endswith("sendPhoto"):
            return 502, b"{}"
        return await start(self, url, method, request_data, **kw)

    monkeypatch.setattr(Wire, "do_request", blip)
    wire = tap(f"ml:sf:{meal.id}")
    assert _toasts(wire) == ["Не получилось, попробуй ещё раз"]


def test_m13_a_voice_shows_what_was_heard(db, owner, reader):
    from app.models import MealReading

    meal = meal_items.save_meal(
        db,
        meal_items.MealCreate(description="бананы", meal_time=NOW, sent_at=NOW),
        owner.id,
    )
    db.add(
        MealSource(
            user_id=owner.id,
            meal_id=meal.id,
            origin_meal_id=meal.id,
            kind="voice",
            chat_id=CHAT,
            message_id=777,
            sent_at=NOW,
            file_id="v",
        )
    )
    db.add(
        MealReading(
            meal_id=meal.id,
            source_ids=[],
            kind="transcript",
            output={"transcript": "Два <банана>."},
            status=meal_card.HEARD,
            created_at=NOW,
        )
    )
    db.commit()
    screen = _screen(tap(f"ml:src:{meal.id}:t"))
    assert (
        "1. 🎙 Голосовое, 13:20\n"
        "<blockquote expandable>Два &lt;банана&gt;.</blockquote>\n"
    ) in screen["text"]


def test_m13h_at_little_strength(db, owner, reader):
    meal = _meal_from(db, reader, "рис", ago=0)
    owner.settings = {**owner.settings, next_step.ENERGY: next_step.SIMPLE}
    db.commit()
    screen = _screen(tap(f"ml:src:{meal.id}:t"))
    assert screen["text"] == (
        f"<b>📋 Что прислано</b>\n1. ✏️ «рис», 13:20\n{M13_FIRST_LINE}"
    )
    assert _labels(screen) == [["⬅️ К приёму"]]


def test_m13a_nothing_kept(db, owner, reader):
    meal = meal_items.save_meal(
        db,
        meal_items.MealCreate(description="старое", meal_time=NOW, sent_at=NOW),
        owner.id,
    )
    screen = _screen(tap(f"ml:src:{meal.id}:t"))
    assert screen["text"] == (
        "<b>📋 Что прислано</b>\nСообщение при этой записи не сохранено."
    )
    assert _datas(screen) == [[f"ml:o:{meal.id}:t"]]


def test_m13_an_album_offers_the_split_in_place_of_the_row(
    db, owner, reader, monkeypatch
):
    monkeypatch.setattr(tg, "_download_image", AsyncMock(return_value="data:x"))
    reader.answer = _at("суп", "хлеб")

    def shot(name):
        size = {"file_id": name, "file_unique_id": name, "width": 9, "height": 9}
        return _message(photo=[size], media_group_id="a1")

    async def steps(application, _wire):
        for name in ("one", "two"):
            message = shot(name)
            await application.process_update(
                Update.de_json(
                    {"update_id": message["message_id"], "message": message},
                    application.bot,
                )
            )

    _run(steps)
    meal = db.scalars(select(Meal).order_by(Meal.id)).first()
    screen = _screen(tap(f"ml:src:{meal.id}:t"))
    assert [f"ml:zq:{meal.id}:t"] in _datas(screen)
    assert ["➖ Разделить"] in _labels(screen)
    assert "ml:z:" not in str(_datas(screen))
    assert "1. 📷 Фото" in screen["text"] and "2. 📷 Фото" in screen["text"]


def test_an_album_with_a_meal_joined_to_it_does_not_split_from_under_it(
    db, owner, reader, monkeypatch
):
    monkeypatch.setattr(tg, "_download_image", AsyncMock(return_value="data:x"))
    reader.answer = _at("суп", "хлеб")

    def shot(name):
        size = {"file_id": name, "file_unique_id": name, "width": 9, "height": 9}
        return _message(photo=[size], media_group_id="a1", date=NOW_TS - 600)

    async def steps(application, _wire):
        for name in ("one", "two"):
            message = shot(name)
            await application.process_update(
                Update.de_json(
                    {"update_id": message["message_id"], "message": message},
                    application.bot,
                )
            )

    _run(steps)
    album = db.scalars(select(Meal).order_by(Meal.id)).first()
    later = _meal_from(db, reader, "сыр", ago=0)
    tap(f"ml:j:{album.id}:{later.id}:t")
    screen = _screen(tap(f"ml:src:{album.id}:t"))
    assert "ml:zq:" not in str(_datas(screen))
    assert f"ml:z:{_only_source(db, later.id).id}:t" in str(_datas(screen))
    # A stale ➖ Разделить finds the sources screen, and splits nothing.
    wire = tap(f"ml:za:{album.id}")
    assert "<b>📋 Что прислано</b>" in _screen(wire)["text"]
    assert _names(db, _get(db, album.id)) == ["суп", "хлеб", "сыр"]


# --- 🔁 Прочитать заново -----------------------------------------------------------


def _reread(db, reader, meal_id: int, foods: tuple, **numbers) -> Wire:
    """Opens M13 on a message, then taps the button there: what the person does."""
    tap(f"ml:src:{meal_id}:t", 4800)
    reader.answer = _at(*foods, **numbers)
    return tap(f"ml:rr:{meal_id}:t", 4800)


def test_read_again_replaces_what_was_read_and_keeps_what_was_typed(db, owner, reader):
    meal = _meal_from(db, reader, "обед", "рис", "курица", ago=0, calories=500.0)
    rice = next(i for i in crud_meal_item.live(db, meal.id) if i.name == "рис")
    change = meal_items.replace_item(db, owner.id, rice.id, name="гречка")
    assert change.status == meal_items.DONE
    meal_items.add_item(db, owner.id, meal.id, name="салат")
    asked = len(reader.text)

    wire = _reread(db, reader, meal.id, ("рис", "курица", "кетчуп"), calories=600.0)
    assert len(reader.text) == asked + 1  # one paid call, on the tap
    assert _names(db, _get(db, meal.id)) == ["гречка", "салат", "курица", "кетчуп"]
    assert _toasts(wire)[0] == "Читаю"
    # The answer lands on the message tapped; the meal's own line is retouched besides.
    last = [edit for edit in wire.edits() if edit["message_id"] == 4800][-1]
    assert last["text"] == "Записано: гречка, салат, курица и кетчуп."
    event = db.scalars(select(RecordEvent).where(RecordEvent.kind == "reread")).one()
    assert _datas(last)[1][0] == f"ml:u:{event.id}:r"
    assert _labels(last)[1] == ["↩️ Вернуть", "✏️ Исправить"]
    assert _get(db, meal.id).reading_status == "read"


def test_read_again_can_be_taken_back(db, owner, reader):
    meal = _meal_from(db, reader, "обед", "рис", "курица", ago=0, calories=500.0)
    before = (_names(db, meal), _totals(meal), meal.plate_totals, meal.description)
    _reread(db, reader, meal.id, ("суп", "хлеб"), calories=700.0)
    assert _names(db, _get(db, meal.id)) == ["суп", "хлеб"]
    assert _get(db, meal.id).calories == 700.0
    event = db.scalars(select(RecordEvent).where(RecordEvent.kind == "reread")).one()
    tap(f"ml:u:{event.id}:r")
    meal = _get(db, meal.id)
    assert (
        _names(db, meal),
        _totals(meal),
        meal.plate_totals,
        meal.description,
    ) == before


def test_read_again_never_brings_back_a_dish_the_person_took_out(db, owner, reader):
    meal = _meal_from(db, reader, "обед", "рис", "курица", ago=0, calories=500.0)
    chicken = next(i for i in crud_meal_item.live(db, meal.id) if i.name == "курица")
    meal_items.remove_item(db, owner.id, chicken.id)
    _reread(db, reader, meal.id, ("рис", "курица"), calories=500.0)
    assert _names(db, _get(db, meal.id)) == ["рис"]


def test_read_again_counts_a_typed_dish_once(db, owner, reader):
    meal = _meal_from(db, reader, "рис", ago=0, calories=100.0)
    rice = crud_meal_item.live(db, meal.id)[0]
    meal_items.set_quantity(db, owner.id, rice.id, 2.0)
    assert _get(db, meal.id).calories == 200.0
    _reread(db, reader, meal.id, ("рис", "соус"), calories=150.0)
    meal = _get(db, meal.id)
    assert _names(db, meal) == ["рис", "соус"]
    # The reading's 150 hold a rice already counted at 100 a helping... the person's
    # number stands and only what is new is added on top.
    assert meal.calories == 250.0


def test_read_again_leaves_a_joined_meal_alone(db, owner, reader):
    a = _meal_from(db, reader, "обед", "рис", "курица", ago=20, calories=500.0)
    b = _meal_from(db, reader, "ужин", "хлеб", "чипсы", ago=0, calories=300.0)
    a_id, b_id = a.id, b.id
    tap(f"ml:j:{a_id}:{b_id}:t")
    _reread(db, reader, a_id, ("суп", "салат"), calories=400.0)
    a = _get(db, a_id)
    assert _names(db, a) == ["хлеб", "чипсы", "суп", "салат"]
    assert a.calories == 700.0
    # And it still comes apart, the first keeping its new reading.
    tap(f"ml:z:{_only_source(db, b_id).id}:t")
    a, b = _get(db, a_id), _get(db, b_id)
    assert _names(db, a) == ["суп", "салат"] and a.calories == 400.0
    assert _names(db, b) == ["хлеб", "чипсы"] and b.calories == 300.0


def test_read_again_that_fails_leaves_the_record(db, owner, reader):
    meal = _meal_from(db, reader, "рис", ago=0)
    tap(f"ml:src:{meal.id}:t", 4800)
    reader.error = ValueError("boom")
    wire = tap(f"ml:rr:{meal.id}:t", 4800)
    reader.error = None
    meal = _get(db, meal.id)
    assert meal.reading_status == "read"
    assert _names(db, meal) == ["рис"]
    assert wire.edits()[-1]["text"] == "Записано: рис."
    assert "Не вышло" not in wire.edits()[-1]["text"]


def test_a_second_tap_while_it_reads_pays_for_nothing(db, owner, reader):
    meal = _meal_from(db, reader, "рис", ago=0)
    meal.reading_status = "pending"
    db.commit()
    asked = len(reader.text)
    wire = tap(f"ml:rr:{meal.id}:t", 4800)
    assert _toasts(wire) == ["Читаю"]
    assert len(reader.text) == asked


def test_a_stale_read_button_on_a_line_costs_nothing(db, owner, reader):
    """🔁 Прочитать заново on an old M7: the meal has been read since, and nothing is asked. The
    known-positive is the test above that pays on M13."""
    meal = _meal_from(db, reader, "рис", ago=0)
    asked = len(reader.text)
    wire = tap(f"ml:rr:{meal.id}:r", 4801)
    assert len(reader.text) == asked
    assert _screen(wire)["text"] == "Записано: рис."


def test_a_reply_source_read_again_does_not_join_anything_by_itself(
    db, owner, reader, monkeypatch
):
    """A photo sent as a reply and separated: its read does not go back in."""
    monkeypatch.setattr(tg, "_download_image", AsyncMock(return_value="data:x"))
    first = _meal_from(db, reader, "рис", ago=5)
    line = db.scalars(
        select(MealMessage).where(
            MealMessage.meal_id == first.id, MealMessage.view != "source"
        )
    ).one()
    reader.answer = _at("сыр")
    send(
        photo(
            reply_to_message={
                "message_id": line.message_id,
                "date": NOW_TS,
                "chat": {"id": CHAT, "type": "private"},
                "text": "x",
            }
        )
    )
    second = db.scalars(select(Meal).order_by(Meal.id.desc())).first()
    assert second.merged_into_id == first.id
    second_id, first_id = second.id, first.id
    tap(f"ml:z:{_only_source(db, second_id).id}:r")
    assert _get(db, second_id).merged_into_id is None
    _reread(db, reader, second_id, ("творог",))
    assert _get(db, second_id).merged_into_id is None
    assert _names(db, _get(db, second_id)) == ["творог"]
    assert _names(db, _get(db, first_id)) == ["рис"]


# --- M14 ---------------------------------------------------------------------------


def test_m14_the_screen_and_a_toggle(db, owner, reader):
    meal = _meal_from(
        db,
        reader,
        "рис",
        ago=0,
        food_items=[{"name": "рис", "groups": ["veg"]}],
    )
    count = db.query(RecordEvent).count()
    screen = _screen(tap(f"ml:gr:{meal.id}:t"))
    assert screen["text"] == M14
    assert _labels(screen) == [
        ["🫘 Бобовые", "⚓ Опора", "✓ 🥦 Овощи"],
        ["🍎 Фрукты", "🌾 Злаки", "🥜 Орехи"],
        ["🥛 Кальций", "🌰 Омега-3"],
        ["⬅️ К приёму"],
    ]
    assert _datas(screen)[0] == [
        f"ml:gt:{meal.id}:legumes:t",
        f"ml:gt:{meal.id}:anchor:t",
        f"ml:gt:{meal.id}:veg:t",
    ]
    assert _datas(screen)[-1] == [f"ml:o:{meal.id}:t"]
    _budget(screen)
    db.expire_all()
    assert db.query(RecordEvent).count() == count  # opening writes nothing

    screen = _screen(tap(f"ml:gt:{meal.id}:fruit:t"))
    assert _labels(screen)[1][0] == "✓ 🍎 Фрукты"
    meal = _get(db, meal.id)
    assert meal.ai_analysis["food_groups"] == ["veg", "fruit"]
    assert meal.ai_analysis["food_groups_by_user"] is True
    assert meal.nutrients["food_groups"] == ["veg", "fruit"]
    event = db.scalars(select(RecordEvent).where(RecordEvent.kind == "groups")).one()
    assert event.meal_id == meal.id
    assert meal.description == "рис"

    screen = _screen(tap(f"ml:gt:{meal.id}:veg:t"))
    assert _labels(screen)[0][2] == "🥦 Овощи"
    assert _get(db, meal.id).ai_analysis["food_groups"] == ["fruit"]


def test_m14_the_choice_is_remembered_for_the_dish(db, owner, reader):
    meal = _meal_from(db, reader, "рис", ago=30)
    tap(f"ml:gt:{meal.id}:grain:t")
    db.expire_all()
    item = crud_meal_item.live(db, meal.id)[0]
    assert meal_items._groups_of_dish(db, item.personal_food_id, "рис") == ["grain"]
    # The next meal of that dish, written for free, carries it.
    wire = send(text("рис", date=NOW_TS))
    latest = db.scalars(select(Meal).order_by(Meal.id.desc())).first()
    assert latest.id != meal.id
    assert meal_items._tagged(latest).get("food_groups") == ["grain"]


def test_m14_on_a_meal_of_two_the_choice_stays_with_the_meal(db, owner, reader):
    meal = _meal_from(db, reader, "обед", "рис", "курица", ago=0)
    tap(f"ml:gt:{meal.id}:grain:t")
    item = crud_meal_item.live(db, meal.id)[0]
    assert meal_items._groups_of_dish(db, item.personal_food_id, item.name) in (
        None,
        [],
    )


def test_m14_an_unknown_group_is_refused(db, owner, reader, caplog):
    meal = _meal_from(db, reader, "рис", ago=0)
    with caplog.at_level(logging.WARNING):
        tap(f"ml:gt:{meal.id}:sugar:t")
    assert "unknown or foreign" in caplog.text
    assert not _get(db, meal.id).ai_analysis or "sugar" not in str(
        _get(db, meal.id).ai_analysis
    )


def test_m14_every_group_has_its_button():
    assert list(food_groups.GROUPS) == [
        "legumes",
        "anchor",
        "veg",
        "fruit",
        "grain",
        "nuts",
        "calcium",
        "omega",
    ]


def test_the_topic_names_where_the_groups_are_now():
    how = explain_screen.TOPICS["groups"].how
    assert "в черновике" not in how
    assert "«🏷 Группы»" in how


# --- an edited message --------------------------------------------------------------


def test_m11_a_real_edit_keeps_a_new_source_and_leaves_the_record(
    db, owner, reader, caplog
):
    message = text("рис с курицей")
    reader.answer = _at("рис")
    send(message)
    db.expire_all()
    meal = db.scalars(select(Meal)).one()
    names = _names(db, meal)
    asked = len(reader.text)
    with caplog.at_level(logging.DEBUG):
        wire = edited({**message, "text": "гречка с курицей"})

    rows = list(db.scalars(select(MealSource).order_by(MealSource.id)))
    assert len(rows) == 2
    assert rows[0].text == "рис с курицей"  # what was sent never changes
    assert rows[1].text == "гречка с курицей"
    assert rows[1].supersedes_id == rows[0].id
    assert rows[1].meal_id == meal.id
    assert _names(db, _get(db, meal.id)) == names
    assert len(reader.text) == asked  # no read, no payment
    assert len(db.scalars(select(Meal)).all()) == 1

    reply = wire.sent()[-1]
    assert reply["text"] == M11
    assert _labels(reply) == [["🔁 Прочитать заново", "✏️ Исправить"]]
    assert _datas(reply) == [[f"ml:rr:{meal.id}:r", f"ml:o:{meal.id}:r"]]
    assert reply["disable_notification"] is True
    assert "гречка" not in caplog.text


def test_m11_the_button_reads_the_new_words(db, owner, reader):
    message = text("рис с курицей")
    reader.answer = _at("рис")
    send(message)
    meal = db.scalars(select(Meal)).one()
    meal_id = meal.id
    wire = edited({**message, "text": "гречка с курицей"})
    line_id = next(
        r.message_id
        for r in db.scalars(select(MealMessage))
        if r.message_id > 5000 and r.message_id == wire.next_id
    )
    reader.answer = _at("гречка")
    tap(f"ml:rr:{meal_id}:r", line_id)
    assert reader.text[-1] == "гречка с курицей"
    assert _names(db, _get(db, meal_id)) == ["гречка"]


def test_m11_the_same_edit_replayed_says_nothing_twice(db, owner, reader):
    message = text("рис")
    reader.answer = _at("рис")
    send(message)
    edited({**message, "text": "гречка"})
    wire = edited({**message, "text": "гречка"})
    assert not wire.sent()
    assert len(db.scalars(select(MealSource)).all()) == 2


def test_m11_an_edit_of_a_message_nobody_knows_writes_nothing(db, owner, reader):
    wire = edited(text("рис"))
    assert not wire.sent()
    db.expire_all()
    assert not db.scalars(select(Meal)).all()
    assert not db.scalars(select(MealSource)).all()
    assert not reader.text
    # The known-positive: that very message, sent and not edited, is a meal.
    reader.answer = _at("рис")
    send(text("рис"))
    db.expire_all()
    assert len(db.scalars(select(Meal)).all()) == 1


def test_m11_an_edit_that_looks_like_a_menu_button_stays_an_edit(
    db, owner, reader, monkeypatch
):
    """The conversation's filters read the effective message: an edit must not reach
    them."""
    screen = AsyncMock(return_value=None)
    monkeypatch.setattr(tg, "show_statistics", screen)
    message = text("рис")
    reader.answer = _at("рис")
    send(message)
    wire = edited({**message, "text": "📊 Статистика"})
    assert [p["text"] for p in wire.sent()] == [M11]
    screen.assert_not_awaited()
    # Known-positive: the same words as a new message do open the screen.
    send(text("📊 Статистика", message_id=message["message_id"] + 50))
    screen.assert_awaited_once()


def test_m11_a_row_of_someone_else_is_left_alone(db, owner, reader):
    other = User(telegram_id=777)
    db.add(other)
    db.commit()
    message = text("рис")
    meal = meal_items.save_meal(
        db,
        meal_items.MealCreate(description="рис", meal_time=NOW, sent_at=NOW),
        other.id,
    )
    db.add(
        MealSource(
            user_id=other.id,
            chat_id=message["chat"]["id"],
            message_id=message["message_id"],
            kind="text",
            text="рис",
            sent_at=NOW,
            meal_id=meal.id,
        )
    )
    capture.map_message(
        db, message["chat"]["id"], message["message_id"], meal.id, "source"
    )
    db.commit()
    wire = edited({**message, "text": "гречка"})
    assert not wire.sent()
    assert len(db.scalars(select(MealSource)).all()) == 1


def _album_with_an_edited_caption(db, owner) -> tuple:
    meal = meal_items.save_meal(
        db,
        meal_items.MealCreate(description="обед", meal_time=NOW, sent_at=NOW),
        owner.id,
    )
    long_ago = NOW - timedelta(hours=1)
    rows = [
        MealSource(
            user_id=owner.id,
            chat_id=CHAT,
            message_id=number,
            kind="photo",
            file_id=f"f{number}",
            text=f"подпись {number}",
            media_group_id="album",
            sent_at=NOW,
            created_at=long_ago,
            meal_id=meal.id,
        )
        for number in (10, 11)
    ]
    db.add_all(rows)
    db.commit()
    db.add(
        MealSource(
            user_id=owner.id,
            chat_id=CHAT,
            message_id=10,
            kind="photo",
            file_id="f10",
            text="новая подпись",
            media_group_id="album",
            sent_at=NOW,
            supersedes_id=rows[0].id,
            meal_id=meal.id,
        )
    )
    db.commit()
    return meal, rows


def test_an_album_is_read_with_its_edited_caption_and_its_own_rows(db, owner):
    meal, rows = _album_with_an_edited_caption(db, owner)
    job = capture.Read(
        meal_id=meal.id,
        rev=meal.rev or 0,
        source_id=rows[0].id,
        telegram_id=OWNER,
        chat_id=CHAT,
        kind=capture.IMAGE,
        file_id="f10",
        text=None,
    )
    read = capture.read_as_of_now(db, job)
    assert read.text == "новая подпись\nподпись 11"
    assert read.file_ids == ("f10", "f11")
    # The originals: a read over an edit's row would look like a photo it missed.
    assert read.source_ids == (rows[0].id, rows[1].id)
    assert capture.album_after(db, read) is None


def test_an_edit_does_not_make_an_album_wait_for_a_late_photo(db, owner, monkeypatch):
    monkeypatch.setattr(capture, "ALBUM_WAIT_SECONDS", 60)
    meal, rows = _album_with_an_edited_caption(db, owner)
    assert capture.album_wait(db, meal.id) == 0
    # Known-positive: a photo that did just come makes it wait.
    db.add(
        MealSource(
            user_id=owner.id,
            chat_id=CHAT,
            message_id=12,
            kind="photo",
            file_id="f12",
            media_group_id="album",
            sent_at=NOW,
            meal_id=meal.id,
        )
    )
    db.commit()
    assert capture.album_wait(db, meal.id) > 0


def test_silence_on_a_read_anew_leaves_the_record_and_the_line_says_it(
    db, owner, reader
):
    meal = _meal_from(db, reader, "рис", ago=0)
    capture.map_message(db, CHAT, 4900, meal.id, "pending")
    job = capture._anew_job(db, meal, 4900)
    meal.reading_status = "pending"
    db.commit()
    done = capture.read_no_speech(db, job)
    assert done.line.text == "Записано: рис."
    db.expire_all()
    assert _get(db, meal.id).deleted_at is None
    assert _get(db, meal.id).reading_status == "read"


def test_m8a_both_lines_stop_saying_what_they_said(db, owner, reader):
    first, second = _two(db, reader)
    lines = {
        row.meal_id: row.message_id
        for row in db.scalars(select(MealMessage).where(MealMessage.view == "line"))
    }
    source = _only_source(db, second.id)
    tap(f"ml:j:{first.id}:{second.id}:r")
    wire = tap(f"ml:z:{source.id}:r")
    said = {edit["message_id"]: edit["text"] for edit in wire.edits()}
    assert said[lines[first.id]].endswith(": рис.")
    assert said[lines[second.id]].endswith(": сыр.")


def test_m11_a_joined_message_answers_for_the_survivor(db, owner, reader):
    first = _meal_from(db, reader, "рис", ago=20)
    message = text("сыр", date=NOW_TS)
    reader.answer = _at("сыр")
    send(message)
    second = db.scalars(select(Meal).order_by(Meal.id.desc())).first()
    tap(f"ml:j:{first.id}:{second.id}:r")
    wire = edited({**message, "text": "творог"})
    # 🔁 would read the survivor's own first message, not this one: not offered.
    assert _datas(wire.sent()[-1]) == [[f"ml:o:{first.id}:r"]]
    row = db.scalars(
        select(MealSource).where(MealSource.supersedes_id.is_not(None))
    ).one()
    assert row.meal_id == first.id


def test_m11_a_deleted_meal_keeps_the_words_and_says_nothing(db, owner, reader):
    message = text("рис")
    reader.answer = _at("рис")
    send(message)
    meal = db.scalars(select(Meal)).one()
    meal_items.delete_meal(db, owner.id, meal.id)
    wire = edited({**message, "text": "гречка"})
    assert not wire.sent()
    assert len(db.scalars(select(MealSource)).all()) == 2


def test_m11_outside_a_private_chat_nothing_moves(db, owner, reader):
    message = text("рис")
    reader.answer = _at("рис")
    send(message)
    group = {**message, "text": "гречка", "chat": {"id": -5, "type": "group"}}
    wire = edited(group)
    assert not wire.sent()
    assert len(db.scalars(select(MealSource)).all()) == 1


def test_m13_shows_the_edited_words_once(db, owner, reader):
    message = text("рис")
    reader.answer = _at("рис")
    send(message)
    meal = db.scalars(select(Meal)).one()
    edited({**message, "text": "гречка"})
    screen = _screen(tap(f"ml:src:{meal.id}:t"))
    assert "1. ✏️ «гречка», 13:20\n" in screen["text"]
    assert "2. " not in screen["text"]


def test_a_replayed_update_still_cannot_make_a_second_row(db, owner):
    """The key stands for originals; only an edit's own row passes beside it."""
    fields = dict(
        user_id=owner.id, kind="text", chat_id=CHAT, message_id=1, sent_at=NOW
    )
    first = MealSource(text="рис", **fields)
    db.add(first)
    db.commit()
    db.add(MealSource(text="рис", **fields))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    # The known-positive: an edit of it is allowed.
    db.add(MealSource(text="гречка", supersedes_id=first.id, **fields))
    db.commit()


def test_polling_and_the_webhook_ask_for_edits(monkeypatch):
    assert "edited_message" in tg.ALLOWED_UPDATES
    assert "message" in tg.ALLOWED_UPDATES
    assert "callback_query" in tg.ALLOWED_UPDATES

    application = MagicMock()
    monkeypatch.setattr(bot_entry, "create_bot_application", lambda: application)
    monkeypatch.setattr(bot_entry.settings, "TELEGRAM_BOT_TOKEN", "123:fake")
    bot_entry.main()
    assert application.run_polling.call_args.kwargs["allowed_updates"] == (
        tg.ALLOWED_UPDATES
    )

    set_webhook = AsyncMock()
    monkeypatch.setattr(app_main.bot_app.bot.__class__, "set_webhook", set_webhook)
    monkeypatch.setattr(settings, "TELEGRAM_WEBHOOK_URL", "https://example.test/hook")
    monkeypatch.setattr(settings, "TELEGRAM_WEBHOOK_SECRET", "s")
    asyncio.run(app_main.setup_webhook())
    assert set_webhook.call_args.kwargs["allowed_updates"] == tg.ALLOWED_UPDATES


# --- the guard of the data the round trip stands on ----------------------------------


def test_a_replaced_item_keeps_its_origin(db, owner, reader):
    first, second = _two(db, reader)
    tap(f"ml:j:{first.id}:{second.id}:r")
    cheese = next(i for i in crud_meal_item.live(db, first.id) if i.name == "сыр")
    origin, source_id = cheese.origin_meal_id, cheese.source_id
    change = meal_items.replace_item(db, owner.id, cheese.id, name="творог")
    new = db.get(MealItem, change.items[0].id)
    assert new.origin_meal_id == origin == second.id
    assert new.source_id == source_id
