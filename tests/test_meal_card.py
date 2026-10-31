"""Slice N, the meal's card: every screen and verb, on real rows through the real
screen function, and the typed slot through the handler the bot registers."""

from __future__ import annotations

import asyncio
import inspect
import logging
import re
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from telegram.ext import ApplicationHandlerStop, CallbackQueryHandler, MessageHandler

from app.core.config import settings
from app.crud.crud_favorite_meal import crud_favorite_meal
from app.crud.crud_meal_item import crud_meal_item
from app.crud.crud_personal_food import crud_personal_food
from app.db.base import Base
from app.models import Meal, MealItem, PersonalFood, RecordEvent, User
from app.schemas.meal import MealCreate
from app.services import favorites, meal_card, meal_items, next_step, stats_screen
from app.services import telegram as tg
from app.services.numbers import SHOW_NUMBERS
from tests.test_user_text import findings

ZONE = "Asia/Tbilisi"  # UTC+4, no daylight saving
# Monday 26 October 2026, 13:20 local.
EATEN = datetime(2026, 10, 26, 9, 20, tzinfo=UTC)
NOW = datetime(2026, 10, 26, 14, 0, tzinfo=UTC)
WHEN = "пн 26 октября, 13:20"
DELETED_NOTICE = "<i>Этого приёма уже нет, вот сегодняшний день.</i>"
DISH_GONE = "<i>Этого блюда в приёме уже нет.</i>"
HINT = "<i>Нажми на блюдо, чтобы поправить.</i>"
HOW = (
    "Считается от первой записи: ½ — вдвое меньше, ×1 — столько же, ×2 — вдвое больше."
)


@pytest.fixture
def Session(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(tg, "SessionLocal", factory)
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_IDS", "42")
    yield factory
    engine.dispose()


@pytest.fixture
def db(Session):
    with Session() as session:
        yield session


@pytest.fixture
def owner(db):
    user = User(telegram_id=42, username="owner", settings={"timezone": ZONE})
    db.add(user)
    db.commit()
    return user


@pytest.fixture
def stranger(db):
    user = User(telegram_id=77, username="other", settings={"timezone": ZONE})
    db.add(user)
    db.commit()
    return user


def _meal(db, user, names, calories=620.0, **fields) -> Meal:
    """A saved meal, written the way a saved draft writes it."""
    nutrients = {
        "foods": names,
        "calories": calories,
        "protein": None if calories is None else 38.0,
        "fats": None if calories is None else 19.0,
        "carbs": None if calories is None else 70.0,
    }
    in_fields = {
        "description": ", ".join(names),
        "meal_time": EATEN,
        "sent_at": fields.get("meal_time", EATEN),
        "calories": calories,
        "proteins": nutrients["protein"],
        "fats": nutrients["fats"],
        "carbohydrates": nutrients["carbs"],
        "nutrients": nutrients,
        "photos": ["PHOTO"],
        "resolution_source": "vision",
        **fields,
    }
    return meal_items.save_meal(db, MealCreate(**in_fields), user.id)


def _one(db, user, name="курица", calories=330.0, grams=240.0, **fields):
    signals = {"portion_grams": grams} if grams else None
    meal = _meal(db, user, [name], calories, resolution_signals=signals, **fields)
    return meal, crud_meal_item.live(db, meal.id)[0]


def _three_counted(db, user, **fields):
    """A meal whose every dish has its own numbers, the N1 of the spec."""
    meal, rice = _one(db, user, "рис", 240.0, 180.0, **fields)
    rice.base_proteins, rice.base_fats, rice.base_carbohydrates = 13.0, 6.5, 45.0
    chicken = meal_items.add_item(db, user.id, meal.id, name="курица").items[0]
    veg = meal_items.add_item(db, user.id, meal.id, name="овощи").items[0]
    for item, kcal, grams in ((chicken, 165.0, 120.0), (veg, 100.0, 120.0)):
        item.base_calories, item.base_proteins = kcal, 10.0
        item.base_fats, item.base_carbohydrates = 5.0, 10.0
        item.base_grams, item.numbers_source = grams, "model"
    db.commit()
    meal_items.set_quantity(db, user.id, chicken.id, 2.0)
    meal_items.set_quantity(db, user.id, veg.id, 0.5)
    db.refresh(meal)
    return meal, rice, chicken, veg


def _tap(db, data, telegram_id=42, message_id=7, now=NOW):
    return meal_card.handle_callback(
        db, telegram_id, data, now=now, message_id=message_id
    )


def _rows(screen):
    return [[b.text for b in row] for row in screen.markup.inline_keyboard]


def _data(screen):
    return [[b.callback_data for b in row] for row in screen.markup.inline_keyboard]


def _button(screen, label):
    for row in screen.markup.inline_keyboard:
        for button in row:
            if button.text == label:
                return button.callback_data
    raise AssertionError(f"no {label!r} in {_rows(screen)}")


def _text_lines(screen):
    return screen.text.split("\n")


def _set(db, user, **values):
    user.settings = {**(user.settings or {}), **values}
    db.commit()


def _counts(db):
    return (
        db.scalar(select(RecordEvent.id).order_by(RecordEvent.id.desc()).limit(1)),
        len(db.scalars(select(MealItem)).all()),
    )


# --- N1 and its variants ----------------------------------------------------


def test_n1_is_the_spec_card(db, owner):
    meal, rice, chicken, veg = _three_counted(db, owner)
    screen = _tap(db, f"ml:o:{meal.id}:r")
    assert screen.text == "\n".join(
        [
            f"<b>🍽 Приём, {WHEN}</b>",
            "рис",
            "курица · 2 порции",
            "овощи · ½ порции",
            "<i>📷 Оценка по фото</i>",
            HINT,
        ]
    )
    assert _rows(screen) == [
        ["рис", "курица", "овощи"],
        ["➕ Блюдо", "🕐 Время", "🗑 Удалить"],
        ["🔢 Цифры", "Ещё ›"],
        ["⬅️ К ответу бота"],
    ]
    assert _data(screen) == [
        [f"ml:i:{rice.id}:r", f"ml:i:{chicken.id}:r", f"ml:i:{veg.id}:r"],
        [f"ml:pk:{meal.id}:a:r:0", f"ml:t:{meal.id}:c", f"ml:d:{meal.id}:r"],
        [f"ml:n:{meal.id}:1:r", f"ml:mo:{meal.id}:r"],
        [f"ml:r:{meal.id}"],
    ]


def test_n1a_open_numbers_sum_with_a_mark_on_every_estimate(db, owner):
    meal, *_ = _three_counted(db, owner)
    screen = _tap(db, f"ml:n:{meal.id}:1:r")
    block = screen.text.split("<blockquote expandable>")[1].split("</blockquote>")[0]
    assert block.split("\n") == [
        "🔥 ≈ 620 ккал · Б 38 · Ж 19 · У 70",
        "рис · ≈ 180 г · ≈ 240 ккал",
        "курица · ≈ 240 г · ≈ 330 ккал",
        "овощи · ≈ 60 г · ≈ 50 ккал",
    ]
    assert _rows(screen)[2][0] == "✓ 🔢 Цифры"
    assert _button(screen, "✓ 🔢 Цифры") == f"ml:n:{meal.id}:0:r"
    closed = _tap(db, f"ml:n:{meal.id}:0:r")
    assert "blockquote" not in closed.text
    assert _rows(closed)[2][0] == "🔢 Цифры"


def test_numbers_are_open_without_a_button_when_the_setting_shows_them(db, owner):
    _set(db, owner, **{SHOW_NUMBERS: True})
    meal, *_ = _three_counted(db, owner)
    screen = _tap(db, f"ml:o:{meal.id}:r")
    assert "<blockquote expandable>🔥 ≈ 620 ккал" in screen.text
    assert _rows(screen)[2] == ["Ещё ›"]


def test_exact_numbers_go_without_the_mark(db, owner):
    meal, item = _one(db, owner, "творог", 180.0, 100.0)
    item.numbers_source = "barcode"
    db.commit()
    screen = _tap(db, f"ml:n:{meal.id}:1:r")
    assert "🔥 180 ккал" in screen.text
    assert "творог · 100 г · 180 ккал" in screen.text
    assert "≈" not in screen.text
    # Control: the same meal from a model carries it.
    item.numbers_source = "model"
    db.commit()
    assert "🔥 ≈ 180 ккал" in _tap(db, f"ml:n:{meal.id}:1:r").text


def test_a_big_number_prints_whole_with_no_word_added(db, owner):
    meal, item = _one(db, owner, "торт", 3000.0, 900.0)
    card = _tap(db, f"ml:n:{meal.id}:1:r")
    assert "🔥 ≈ 3000 ккал" in card.text
    dish = _tap(db, f"ml:ni:{item.id}:1:r")
    assert "🔥 ≈ 3000 ккал" in dish.text
    lines = [line for line in card.text.split("\n") if "3000" in line]
    assert lines == [
        "<blockquote expandable>🔥 ≈ 3000 ккал · Б 38 · Ж 19 · У 70",
        "торт · ≈ 900 г · ≈ 3000 ккал</blockquote>",
    ]


def test_rule_1_model_grams_only_inside_open_numbers(db, owner):
    meal, item = _one(db, owner, "курица", 330.0, 240.0)
    for closed in (_tap(db, f"ml:o:{meal.id}:r"), _tap(db, f"ml:i:{item.id}:r")):
        assert "240" not in closed.text
    assert "≈ 240 г" in _tap(db, f"ml:n:{meal.id}:1:r").text
    assert "≈ 240 г" in _tap(db, f"ml:ni:{item.id}:1:r").text


def test_typed_grams_show_on_the_line_and_without_the_mark(db, owner):
    meal, item = _one(db, owner, "курица", 330.0, 240.0)
    meal_items.set_grams(db, owner.id, item.id, 150.0)
    card = _tap(db, f"ml:o:{meal.id}:r")
    assert "курица · 150 г" in card.text
    assert f"В приёме {WHEN} · 150 г" in _tap(db, f"ml:i:{item.id}:r").text


def test_a_factor_of_one_writes_no_quantity_and_words_are_the_persons(db, owner):
    meal, item = _one(db, owner, "чипсы", 500.0, 100.0)
    assert _text_lines(_tap(db, f"ml:o:{meal.id}:r"))[1] == "чипсы"
    item.amount_text = "три пачки"
    db.commit()
    assert _text_lines(_tap(db, f"ml:o:{meal.id}:r"))[1] == "чипсы, три пачки"
    assert f"В приёме {WHEN} · три пачки" in _tap(db, f"ml:i:{item.id}:r").text


@pytest.mark.parametrize(
    "precision, shown",
    [
        ("exact", WHEN),
        ("approx", "пн 26 октября, ≈ 13:20"),
        ("day_only", "пн 26 октября, без времени"),
    ],
)
def test_the_title_always_carries_the_day_and_how_sure_the_time_is(
    db, owner, precision, shown
):
    meal, _item = _one(db, owner)
    meal.time_precision = precision
    db.commit()
    assert _text_lines(_tap(db, f"ml:o:{meal.id}:r"))[0] == f"<b>🍽 Приём, {shown}</b>"


def test_sent_line_only_past_a_quarter_of_an_hour(db, owner):
    meal, _item = _one(db, owner)
    meal.sent_at = EATEN + timedelta(minutes=15)
    db.commit()
    assert "Отправлено" not in _tap(db, f"ml:o:{meal.id}:r").text
    meal.sent_at = datetime(2026, 10, 27, 5, 10, tzinfo=UTC)
    db.commit()
    assert (
        "<i>Отправлено: вт 27 октября, 9:10.</i>" in _tap(db, f"ml:o:{meal.id}:r").text
    )


def test_a_stored_label_replaces_the_word(db, owner):
    meal, _item = _one(db, owner, meal_type="Обед")
    assert _text_lines(_tap(db, f"ml:o:{meal.id}:r"))[0] == f"<b>🍽 Обед, {WHEN}</b>"


@pytest.mark.parametrize(
    "source, tagged, shown",
    [
        ("barcode_off", True, "<i>📦 По штрих-коду (точно)</i>"),
        ("barcode_off", False, "<i>📦 По штрих-коду (неполные данные)</i>"),
        ("label_ocr", True, "<i>🏷️ С этикетки (проверь)</i>"),
        ("name_web", True, "<i>🌐 Нашли в сети (проверь)</i>"),
        ("name_off", True, "<i>🔍 Нашли в базе (проверь)</i>"),
        ("saved_rag", True, "<i>⭐ Из твоей базы</i>"),
    ],
)
def test_the_source_line_uses_the_shipped_badges(db, owner, source, tagged, shown):
    meal, _item = _one(db, owner, resolution_source=source)
    if not tagged:
        meal.fats = None
        db.commit()
    assert shown in _tap(db, f"ml:o:{meal.id}:r").text


def test_text_meal_has_no_source_line_and_exact_name_has_its_tag(db, owner):
    meal, _item = _one(db, owner, photos=[], resolution_source=None)
    text = _tap(db, f"ml:o:{meal.id}:r").text
    assert "Оценка" not in text
    assert meal_items.TAG_DISH not in text
    meal.resolution_source = meal_items.EXACT_NAME_SOURCE
    db.commit()
    assert "<i>Из твоих блюд</i>" in _tap(db, f"ml:o:{meal.id}:r").text


def test_n1b_legacy_meal_is_not_recomputed_on_open(db, owner):
    meal = Meal(
        user_id=owner.id,
        description="суп с курицей и хлебом",
        meal_time=EATEN,
        calories=512.34,
        proteins=20.0,
        fats=10.0,
        carbohydrates=60.0,
        numbers_basis="plate",
        sent_at=EATEN,
    )
    db.add(meal)
    db.commit()
    before = _counts(db), meal.rev
    screen = _tap(db, f"ml:o:{meal.id}:t")
    assert screen.text == "\n".join(
        [
            f"<b>🍽 Приём, {WHEN}</b>",
            "суп с курицей и хлебом",
            "Цифры на всю запись, не по блюдам.",
        ]
    )
    assert _rows(screen) == [
        ["➕ Блюдо", "🕐 Время", "🗑 Удалить"],
        ["⭐ Весь приём", "🔢 Цифры"],
        ["⬅️ Приёмы дня"],
    ]
    assert _button(screen, "⬅️ Приёмы дня") == "ml:dl:20261026:t"
    assert _button(screen, "⭐ Весь приём") == f"ml:fm:{meal.id}:1:t"
    assert (_counts(db), meal.rev) == before
    assert meal.calories == 512.34
    assert "🔥 ≈ 512 ккал" in _tap(db, f"ml:n:{meal.id}:1:t").text
    # The first ➕ keeps the totals to the kopeck, through the synthetic line.
    added = _tap(db, f"ml:pa:{meal.id}:{_dish(db, owner, 'хлеб').id}:t")
    assert "<i>✓ Добавлено: хлеб</i>" in added.text
    db.refresh(meal)
    assert meal.plate_totals["calories"] == 512.34
    assert meal.calories == 512.34


def _dish(db, user, name) -> PersonalFood:
    dish = crud_personal_food.add_dish(db, user_id=user.id, name=name)
    db.commit()
    return dish


def test_n1c_plate_says_so_and_chips_lead_to_n2a(db, owner):
    meal = _meal(db, owner, ["рис", "курица", "овощи"])
    screen = _tap(db, f"ml:o:{meal.id}:r")
    assert screen.text == "\n".join(
        [
            f"<b>🍽 Приём, {WHEN}</b>",
            "рис",
            "курица",
            "овощи",
            "<i>📷 Оценка по фото</i>",
            "Цифры посчитаны на всю тарелку.",
            HINT,
        ]
    )
    assert _rows(screen)[0] == ["рис", "курица", "овощи"]
    item = crud_meal_item.live(db, meal.id)[1]
    dish = _tap(db, _data(screen)[0][1])
    assert dish.text == "\n".join(
        ["<b>🍲 Курица</b>", f"В приёме {WHEN}", "Цифры посчитаны на всю тарелку."]
    )
    assert _rows(dish) == [
        ["🔄 Заменить", "🗑 Из приёма"],
        ["⚖️ Граммы", "⭐ В избранное"],
        ["⬅️ К приёму"],
    ]
    assert _button(dish, "🗑 Из приёма") == f"ml:x:{item.id}:r"


def test_n1d_unread_says_it_is_in_the_diary_with_what(db, owner):
    meal = Meal(
        user_id=owner.id,
        description=None,
        meal_time=EATEN,
        photos=["PHOTO"],
        numbers_basis="none",
        sent_at=EATEN,
    )
    db.add(meal)
    db.commit()
    screen = _tap(db, f"ml:o:{meal.id}:r")
    assert screen.text == "\n".join(
        [
            f"<b>🍽 Приём, {WHEN}</b>",
            "Фото сохранено, распознать еду пока не вышло.",
            "В дневнике запись с этим фото.",
        ]
    )
    assert _rows(screen) == [["🕐 Время", "🗑 Удалить"], ["⬅️ К ответу бота"]]


def test_n1h_keeps_the_rows_that_stay_and_hides_row_three(db, owner):
    _set(db, owner, **{next_step.ENERGY: next_step.SIMPLE})
    meal, item = _one(db, owner, "чипсы", 500.0, 100.0)
    item.amount_text = "три пачки"
    meal.sent_at = EATEN + timedelta(hours=9)
    db.commit()
    screen = _tap(db, f"ml:o:{meal.id}:r")
    assert screen.text == f"<b>🍽 Приём, {WHEN}</b>\nчипсы, три пачки"
    assert _rows(screen) == [
        ["чипсы"],
        ["➕ Блюдо", "🕐 Время", "🗑 Удалить"],
        ["⬅️ К ответу бота"],
    ]
    # Control: the same meal at the usual energy has the third row and the hint.
    _set(db, owner, **{next_step.ENERGY: next_step.NORMAL})
    usual = _tap(db, f"ml:o:{meal.id}:r")
    assert HINT in usual.text and "Ещё ›" in _rows(usual)[2]


def test_n1h_numbers_follow_the_setting(db, owner):
    _set(db, owner, **{next_step.ENERGY: next_step.SIMPLE, SHOW_NUMBERS: True})
    meal, _item = _one(db, owner)
    assert "<blockquote expandable>" in _tap(db, f"ml:o:{meal.id}:r").text


def test_more_than_three_dishes_page_the_chips_and_the_text_lists_all(db, owner):
    names = ["рис", "курица", "овощи", "хлеб", "сыр"]
    meal = _meal(db, owner, names)
    items = crud_meal_item.live(db, meal.id)
    first = _tap(db, f"ml:o:{meal.id}:r")
    assert _rows(first)[0] == ["рис", "курица", "▶️"]
    for name in names:
        assert name in first.text
    assert _data(first)[0][2] == f"ml:op:{meal.id}:1:r"
    second = _tap(db, _data(first)[0][2])
    assert _rows(second)[0] == ["овощи", "хлеб", "▶️"]
    assert _data(second)[0][0] == f"ml:i:{items[2].id}:r"
    third = _tap(db, _data(second)[0][2])
    assert _rows(third)[0] == ["сыр", "▶️"]
    assert _data(third)[0][1] == f"ml:op:{meal.id}:0:r"


def test_a_chip_is_cut_to_eleven_and_a_mark(db, owner):
    meal = _meal(db, owner, ["очень длинное название", "рис"])
    assert _rows(_tap(db, f"ml:o:{meal.id}:r"))[0][0] == "очень длинн…"


def test_user_text_is_escaped(db, owner):
    meal = _meal(db, owner, ["<b>суп</b>", "рис"])
    item = crud_meal_item.live(db, meal.id)[0]
    for screen in (_tap(db, f"ml:o:{meal.id}:r"), _tap(db, f"ml:mo:{meal.id}:r")):
        assert "&lt;b&gt;суп&lt;/b&gt;" in screen.text
    assert "&lt;b&gt;суп" in _tap(db, f"ml:i:{item.id}:r").text
    assert "<b>суп</b>" not in _tap(db, f"ml:x:{item.id}:r").text


# --- origin -----------------------------------------------------------------


@pytest.mark.parametrize(
    "origin, label, target",
    [
        ("r", "⬅️ К ответу бота", "ml:r:{mid}"),
        ("t", "⬅️ Приёмы дня", "ml:dl:20261026:t"),
        ("y", "⬅️ Приёмы дня", "ml:dl:20261026:y"),
        ("w", "⬅️ Приёмы дня", "ml:dl:20261026:w"),
        ("h9", "⬅️ Когда было", "dh:h:9:0"),
    ],
)
def test_the_back_row_follows_the_origin_through_every_nested_tap(
    db, owner, origin, label, target
):
    meal, item = _one(db, owner)
    card = _tap(db, f"ml:o:{meal.id}:{origin}")
    assert _rows(card)[-1] == [label]
    assert _data(card)[-1] == [target.format(mid=meal.id)]
    dish = _tap(db, f"ml:i:{item.id}:{origin}")
    assert _button(dish, "⬅️ К приёму") == f"ml:o:{meal.id}:{origin}"
    grams = _tap(db, f"ml:g:{item.id}:{origin}")
    assert _button(grams, "⬅️ К блюду в приёме") == f"ml:i:{item.id}:{origin}"
    pick = _tap(db, f"ml:pk:{meal.id}:a:{origin}:0")
    assert _button(pick, "⬅️ К приёму") == f"ml:o:{meal.id}:{origin}"
    more = _tap(db, f"ml:mo:{meal.id}:{origin}")
    assert _button(more, "⬅️ К приёму") == f"ml:o:{meal.id}:{origin}"


def test_the_line_is_what_the_reply_says_with_its_two_buttons(db, owner):
    meal = _meal(db, owner, ["рис", "курица", "овощи"])
    screen = _tap(db, f"ml:r:{meal.id}")
    assert screen.text == "Записано: рис, курица и овощи."
    assert _rows(screen) == [["✏️ Исправить", "🕐 Время"]]
    assert _data(screen) == [[f"ml:o:{meal.id}:r", f"ml:t:{meal.id}:r"]]
    alone, item = _one(db, owner, "сыр")
    screen = _tap(db, f"ml:r:{alone.id}")
    assert screen.text == "Записано: сыр."
    assert _data(screen)[0][0] == f"ml:i:{item.id}:r"


# --- N2 ---------------------------------------------------------------------


def test_n2_is_the_spec_screen(db, owner):
    meal, item = _one(db, owner)
    meal_items.set_quantity(db, owner.id, item.id, 2.0)
    screen = _tap(db, f"ml:i:{item.id}:r")
    assert screen.text == "\n".join(
        [
            "<b>🍲 Курица</b>",
            f"В приёме {WHEN} · 2 порции",
            "<i>Оценка по фото</i>",
            HOW,
        ]
    )
    assert _rows(screen) == [
        ["🔄 Заменить", "🗑 Из приёма"],
        ["½", "×1", "✓ ×2"],
        ["⚖️ Граммы", "⭐ В избранное"],
        ["🔢 Цифры"],
        ["⬅️ К приёму"],
    ]
    assert _data(screen) == [
        [f"ml:pk:{item.id}:r:r:0", f"ml:x:{item.id}:r"],
        [f"ml:q:{item.id}:h:r", f"ml:q:{item.id}:1:r", f"ml:q:{item.id}:2:r"],
        [f"ml:g:{item.id}:r", f"ml:fi:{item.id}:1:r"],
        [f"ml:ni:{item.id}:1:r"],
        [f"ml:o:{meal.id}:r"],
    ]


@pytest.mark.parametrize(
    "verb, notice, line, marked",
    [
        ("2", "<i>✓ 2 порции</i>", " · 2 порции", "✓ ×2"),
        ("h", "<i>✓ ½ порции</i>", " · ½ порции", "✓ ½"),
    ],
)
def test_quantity_says_it_in_words_and_gives_no_second_undo(
    db, owner, verb, notice, line, marked
):
    meal, item = _one(db, owner)
    screen = _tap(db, f"ml:q:{item.id}:{verb}:r")
    assert _text_lines(screen)[0] == notice
    assert _text_lines(screen)[1] == ""
    assert f"В приёме {WHEN}{line}" in screen.text
    assert marked in _rows(screen)[1]
    assert not any("Вернуть" in label for row in _rows(screen) for label in row)
    assert meal.calories in (660.0, 165.0)


def test_times_one_is_the_way_back_and_half_after_double_is_half_of_base(db, owner):
    meal, item = _one(db, owner)
    _tap(db, f"ml:q:{item.id}:2:r")
    _tap(db, f"ml:q:{item.id}:h:r")
    db.refresh(meal)
    assert meal.calories == 165.0
    back = _tap(db, f"ml:q:{item.id}:1:r")
    assert _text_lines(back)[0] == "<i>✓ Как записано</i>"
    db.refresh(meal)
    assert meal.calories == 330.0


def test_the_same_factor_twice_is_one_event_and_a_toast(db, owner):
    _meal_row, item = _one(db, owner)
    first = _tap(db, f"ml:q:{item.id}:2:r")
    events = _counts(db)
    second = _tap(db, f"ml:q:{item.id}:2:r")
    assert first.toast is None
    assert second.toast == "Уже так"
    assert second.text == ""
    assert _counts(db) == events


def test_a_typed_weight_shows_no_mark_and_times_one_returns_to_base(db, owner):
    meal, item = _one(db, owner)
    meal_items.set_grams(db, owner.id, item.id, 120.0)
    screen = _tap(db, f"ml:i:{item.id}:r")
    assert _rows(screen)[1] == ["½", "×1", "×2"]
    _tap(db, f"ml:q:{item.id}:1:r")
    db.refresh(meal)
    assert meal.calories == 330.0
    assert "✓ ×1" in _rows(_tap(db, f"ml:i:{item.id}:r"))[1]


def test_quantity_on_a_plate_line_redraws_the_line_and_changes_nothing(db, owner):
    meal = _meal(db, owner, ["рис", "курица"])
    item = crud_meal_item.live(db, meal.id)[0]
    events = _counts(db)
    screen = _tap(db, f"ml:q:{item.id}:2:r")
    assert "Цифры посчитаны на всю тарелку." in screen.text
    assert _counts(db) == events
    assert meal.calories == 620.0


def test_n2b_a_dish_nobody_counted(db, owner):
    meal = _meal(db, owner, ["хлеб"], calories=None)
    item = crud_meal_item.live(db, meal.id)[0]
    screen = _tap(db, f"ml:i:{item.id}:r")
    assert screen.text == "\n".join(
        ["<b>🍲 Хлеб</b>", f"В приёме {WHEN}", "Цифры не посчитаны."]
    )
    assert _rows(screen) == [
        ["🔄 Заменить", "🗑 Из приёма"],
        ["⚖️ Граммы", "⭐ В избранное"],
        ["⬅️ К приёму"],
    ]
    assert "Не посчитано блюд: 1" in _tap(db, f"ml:n:{meal.id}:1:r").text


def test_n2h_quantity_and_removal_only(db, owner):
    _set(db, owner, **{next_step.ENERGY: next_step.SIMPLE})
    meal, item = _one(db, owner, "чипсы", 500.0, 100.0)
    item.amount_text = "три пачки"
    db.commit()
    screen = _tap(db, f"ml:i:{item.id}:r")
    assert screen.text == "\n".join(
        ["<b>🍲 Чипсы</b>", f"В приёме {WHEN} · три пачки", HOW]
    )
    assert _rows(screen) == [
        ["½", "✓ ×1", "×2"],
        ["🗑 Из приёма"],
        ["⬅️ К приёму"],
    ]


@pytest.mark.parametrize(
    "source, tag",
    [
        ("barcode", "по штрих-коду (точно)"),
        ("label", "с этикетки"),
        ("user", "указано вручную"),
        ("dish", "из твоих блюд, не проверено"),
    ],
)
def test_the_dish_tag_says_where_its_numbers_came_from(db, owner, source, tag):
    _meal_row, item = _one(db, owner)
    item.numbers_source = source
    db.commit()
    wanted = f"<i>{tag[0].upper()}{tag[1:]}</i>"
    assert wanted in _tap(db, f"ml:i:{item.id}:r").text


def test_the_dish_tag_for_a_text_meal_and_a_verified_dish(db, owner):
    meal, item = _one(db, owner, photos=[], resolution_source=None)
    assert "<i>Оценка по тексту</i>" in _tap(db, f"ml:i:{item.id}:r").text
    item.numbers_source = "dish"
    dish = db.get(PersonalFood, item.personal_food_id)
    dish.numbers_quality = "label"
    db.commit()
    assert "<i>Из твоих блюд</i>" in _tap(db, f"ml:i:{item.id}:r").text
    assert meal.id


def test_favourite_dish_takes_this_items_portion_and_undoes(db, owner):
    _meal_row, item = _one(db, owner)
    meal_items.set_quantity(db, owner.id, item.id, 2.0)
    screen = _tap(db, f"ml:fi:{item.id}:1:r")
    assert _button(screen, "✓ ⭐ В избранном") == f"ml:fi:{item.id}:0:r"
    fav = crud_favorite_meal.find_active_by_name(db, owner.id, "курица")
    assert fav.calories == 660.0
    assert fav.nutrients["calories"] == 660.0
    assert fav.nutrients["foods"] == ["курица"]
    assert _tap(db, f"ml:fi:{item.id}:1:r").toast == "Уже так"
    off = _tap(db, f"ml:fi:{item.id}:0:r")
    assert off.toast == "Убрано из избранного"
    assert _button(off, "⭐ В избранное")
    assert crud_favorite_meal.find_active_by_name(db, owner.id, "курица") is None
    assert _tap(db, f"ml:fi:{item.id}:0:r").toast == "Уже так"


def test_a_plate_line_favourite_without_numbers_shows_in_the_list(db, owner):
    meal = _meal(db, owner, ["рис", "курица"])
    item = crud_meal_item.live(db, meal.id)[0]
    _tap(db, f"ml:fi:{item.id}:1:r")
    assert "рис" in str(_rows(favorites.open_list(db, 42)))


# --- N3, the grams --------------------------------------------------------------


def test_n3_opens_the_slot_with_the_message_to_strip(db, owner):
    _meal_row, item = _one(db, owner)
    screen = _tap(db, f"ml:g:{item.id}:r", message_id=55, now=NOW)
    assert screen.text == "<b>⚖️ Курица</b>\nНапиши граммы числом, например 150."
    assert _rows(screen) == [["⬅️ К блюду в приёме"]]
    db.refresh(owner)
    ask = owner.settings["ask"]
    assert ask["kind"] == "grams"
    assert ask["item_id"] == item.id
    assert ask["meal_id"] == item.meal_id
    assert ask["message_id"] == 55
    assert ask["at"] == NOW.isoformat()


@pytest.mark.parametrize("text", ["150", "150 г", "150г", " 150 Г "])
def test_parsing_grams_takes_a_number_and_an_optional_g(text):
    assert meal_card.parse_grams(text) == 150.0


@pytest.mark.parametrize(
    "text", ["0", "5001", "сыр", "150 кг", "150 грамм риса", "-5", "1 2", ""]
)
def test_parsing_grams_refuses_everything_else(text):
    assert meal_card.parse_grams(text) is None


@pytest.mark.parametrize("text", ["1", "5000"])
def test_grams_bounds(text):
    assert meal_card.parse_grams(text) == float(text)


# --- the typed slot through the registered handler -------------------------------


def _application(monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "123:fake")
    return tg.create_bot_application()


def _slot_handler(application):
    return next(
        h
        for h in application.handlers[-1]
        if isinstance(h, MessageHandler) and h.callback is tg.meal_slot_text
    )


def _text_update(text=None, photo=None):
    message = SimpleNamespace(
        text=text,
        photo=photo,
        chat_id=42,
        replies=[],
        markups=[],
        modes=[],
        sent_ids=[],
    )

    async def reply_text(body, *args, **kwargs):
        message.replies.append(body)
        message.markups.append(kwargs.get("reply_markup"))
        message.modes.append(kwargs.get("parse_mode"))
        message.sent_ids.append(900 + len(message.sent_ids))
        return SimpleNamespace(message_id=message.sent_ids[-1])

    message.reply_text = reply_text
    update = SimpleNamespace(
        message=message,
        effective_user=SimpleNamespace(id=42, username="owner"),
    )
    return update, message


def _send(text=None, photo=None):
    """Returns the message, the bot, and whether the text was consumed."""
    update, message = _text_update(text, photo)
    bot = SimpleNamespace(edit_message_reply_markup=AsyncMock())
    context = SimpleNamespace(bot=bot)
    try:
        asyncio.run(tg.meal_slot_text(update, context))
    except ApplicationHandlerStop:
        return message, bot, True
    return message, bot, False


def _ask(db, owner):
    db.refresh(owner)
    return (owner.settings or {}).get("ask")


def test_the_slot_handler_runs_before_the_conversation_and_after_the_gate(
    Session, monkeypatch
):
    application = _application(monkeypatch)
    groups = sorted(application.handlers)
    assert _slot_handler(application)
    assert groups.index(-1) < groups.index(0)
    gate_group = next(
        g
        for g in groups
        if any(
            getattr(h, "callback", None) is tg.access_gate
            for h in application.handlers[g]
        )
    )
    assert gate_group < -1


def test_typed_grams_answer_with_a_new_message_and_strip_the_old_keyboard(db, owner):
    meal, item = _one(db, owner)
    _tap(db, f"ml:g:{item.id}:t", message_id=55, now=datetime.now(UTC))
    message, bot, consumed = _send("150")
    assert consumed
    db.refresh(item)
    assert item.grams == 150.0
    db.refresh(meal)
    assert meal.calories == pytest.approx(330.0 * 150 / 240, abs=0.01)
    assert message.replies[0].startswith("<i>✓ 150 г</i>\n\n<b>🍲 Курица</b>")
    undo = [
        b
        for row in message.markups[0].inline_keyboard
        for b in row
        if b.text == "↩️ Вернуть"
    ]
    event = db.scalars(select(RecordEvent).order_by(RecordEvent.id.desc())).first()
    assert undo[0].callback_data == f"ml:u:{event.id}:t"
    bot.edit_message_reply_markup.assert_awaited_once_with(
        chat_id=42, message_id=55, reply_markup=None
    )
    assert _ask(db, owner) is None


def test_grams_without_a_base_keep_the_numbers_and_say_so(db, owner):
    meal = _meal(db, owner, ["рис", "курица"])
    item = crud_meal_item.live(db, meal.id)[0]
    _tap(db, f"ml:g:{item.id}:r", now=datetime.now(UTC))
    message, _bot, consumed = _send("150 г")
    assert consumed
    assert message.replies[0].startswith("<i>✓ 150 г, цифры не менялись</i>")
    db.refresh(meal)
    assert meal.calories == 620.0


def test_a_wait_never_eats_food_a_word_closes_the_slot_and_goes_on(db, owner):
    _meal_row, item = _one(db, owner)
    _tap(db, f"ml:g:{item.id}:r", now=datetime.now(UTC))
    assert _ask(db, owner)
    message, _bot, consumed = _send("суп с хлебом")
    assert not consumed
    assert message.replies == []
    assert _ask(db, owner) is None
    db.refresh(item)
    assert item.grams is None
    # Closed for good: the next number is not grams, it goes down the router.
    _message, _bot, consumed = _send("150")
    assert not consumed
    db.refresh(item)
    assert item.grams is None


def test_a_photo_a_command_and_a_menu_button_close_the_slot_and_go_on(db, owner):
    _meal_row, item = _one(db, owner)
    cases = [
        {"photo": ["P"]},
        {"text": "/cancel"},
        {"text": "⚖️ Мой вес"},
        {"text": "🍽 Добавить прием пищи"},
    ]
    for case in cases:
        _tap(db, f"ml:pk:{item.meal_id}:a:r:0", now=datetime.now(UTC))
        assert _ask(db, owner)
        _message, _bot, consumed = _send(**case)
        assert not consumed, case
        assert _ask(db, owner) is None, case
    assert len(crud_meal_item.live(db, item.meal_id)) == 1


def test_every_main_menu_label_passes_a_name_slot(db, owner):
    meal, _item = _one(db, owner)
    labels = [b.text for row in tg.main_keyboard.keyboard for b in row]
    assert len(labels) == 5
    for label in labels:
        _tap(db, f"ml:pk:{meal.id}:a:r:0", now=datetime.now(UTC))
        _message, _bot, consumed = _send(label)
        assert not consumed, label
    # Control: a plain word in the same slot is a dish.
    _tap(db, f"ml:pk:{meal.id}:a:r:0", now=datetime.now(UTC))
    _message, _bot, consumed = _send("хлеб")
    assert consumed


def test_text_past_ten_minutes_is_not_an_answer(db, owner):
    _meal_row, item = _one(db, owner)
    _tap(db, f"ml:g:{item.id}:r", now=datetime.now(UTC) - timedelta(minutes=11))
    assert _ask(db, owner)
    _message, _bot, consumed = _send("150")
    assert not consumed
    db.refresh(item)
    assert item.grams is None
    assert _ask(db, owner) is None
    # Control: nine minutes is still the answer.
    _tap(db, f"ml:g:{item.id}:r", now=datetime.now(UTC) - timedelta(minutes=9))
    _message, _bot, consumed = _send("150")
    assert consumed


def test_the_slot_survives_a_restart_it_lives_in_the_database(Session, db, owner):
    _meal_row, item = _one(db, owner)
    _tap(db, f"ml:g:{item.id}:r", now=datetime.now(UTC))
    with Session() as fresh:
        assert fresh.query(User).filter_by(telegram_id=42).one().settings["ask"]
    _message, _bot, consumed = _send("150")
    assert consumed


def test_no_slot_is_no_answer_and_nothing_is_written(db, owner):
    _meal_row, item = _one(db, owner)
    message, _bot, consumed = _send("150")
    assert not consumed
    assert message.replies == []
    db.refresh(item)
    assert item.grams is None


def test_an_expired_subscription_closes_the_slot_and_goes_on(db, owner, monkeypatch):
    _meal_row, item = _one(db, owner)
    _tap(db, f"ml:g:{item.id}:r", now=datetime.now(UTC))
    with patch.object(tg, "check_subscription", AsyncMock(return_value=False)):
        _message, _bot, consumed = _send("150")
    assert not consumed
    db.refresh(item)
    assert item.grams is None


def test_typed_name_adds_a_known_dish_at_its_portion(db, owner):
    meal, _item = _one(db, owner)
    _other, _bread = _one(db, owner, "хлеб", 100.0, 40.0)
    _tap(db, f"ml:pk:{meal.id}:a:r:0", message_id=56, now=datetime.now(UTC))
    assert _ask(db, owner)["kind"] == "dish_add"
    message, bot, consumed = _send("ХЛЕБ")
    assert consumed
    assert message.replies[0].startswith("<i>✓ Добавлено: хлеб</i>\n\n<b>🍽 Приём")
    live = crud_meal_item.live(db, meal.id)
    assert [i.name for i in live] == ["курица", "хлеб"]
    assert live[1].numbers_source == "dish"
    bot.edit_message_reply_markup.assert_awaited_once()


def test_typed_unknown_name_makes_a_dish_without_numbers(db, owner):
    meal, _item = _one(db, owner)
    _tap(db, f"ml:pk:{meal.id}:a:r:0", now=datetime.now(UTC))
    _message, _bot, consumed = _send("новое блюдо")
    assert consumed
    item = crud_meal_item.live(db, meal.id)[1]
    assert item.numbers_source == "none"
    assert crud_personal_food.find_by_name(db, owner.id, "новое блюдо")
    db.refresh(meal)
    assert meal.calories == 330.0


def test_typed_name_replaces_in_a_replace_slot(db, owner):
    meal, item = _one(db, owner)
    _tap(db, f"ml:pk:{item.id}:r:r:0", now=datetime.now(UTC))
    assert _ask(db, owner)["kind"] == "dish_replace"
    message, _bot, consumed = _send("индейка")
    assert consumed
    assert message.replies[0].startswith("<i>✓ Заменено: курица → индейка</i>")
    assert [i.name for i in crud_meal_item.live(db, meal.id)] == ["индейка"]


def test_a_number_in_a_name_slot_is_a_name(db, owner):
    meal, _item = _one(db, owner)
    _tap(db, f"ml:pk:{meal.id}:a:r:0", now=datetime.now(UTC))
    _message, _bot, consumed = _send("150")
    assert consumed
    assert crud_meal_item.live(db, meal.id)[1].name == "150"


def test_the_answer_for_a_meal_deleted_meanwhile_is_todays_card(db, owner):
    meal, item = _one(db, owner)
    _tap(db, f"ml:g:{item.id}:r", now=datetime.now(UTC))
    meal_items.delete_meal(db, owner.id, meal.id)
    message, _bot, consumed = _send("150")
    assert consumed
    assert message.replies[0].startswith(DELETED_NOTICE)


def test_strip_failing_does_not_lose_the_answer(db, owner):
    from telegram.error import BadRequest

    _meal_row, item = _one(db, owner)
    _tap(db, f"ml:g:{item.id}:r", now=datetime.now(UTC))
    update, message = _text_update("150")
    bot = SimpleNamespace(
        edit_message_reply_markup=AsyncMock(side_effect=BadRequest("gone"))
    )
    with pytest.raises(ApplicationHandlerStop):
        asyncio.run(tg.meal_slot_text(update, SimpleNamespace(bot=bot)))
    assert message.replies


def test_an_inline_tap_elsewhere_closes_the_slot_through_the_leave_handler(
    db, owner, Session, monkeypatch
):
    application = _application(monkeypatch)
    leave = [
        h
        for group in application.handlers.values()
        for h in group
        if getattr(h, "callback", None) is tg.meal_slot_left_by_tap
    ]
    assert len(leave) == 1 and isinstance(leave[0], CallbackQueryHandler)
    assert leave[0].pattern.match("st:t")
    assert not leave[0].pattern.match("ml:nop")
    _meal_row, item = _one(db, owner)
    _tap(db, f"ml:g:{item.id}:r", now=datetime.now(UTC))
    update = SimpleNamespace(effective_user=SimpleNamespace(id=42))
    asyncio.run(tg.meal_slot_left_by_tap(update, SimpleNamespace()))
    assert _ask(db, owner) is None
    _message, _bot, consumed = _send("150")
    assert not consumed


def test_leave_handler_with_no_slot_writes_nothing(db, owner):
    update = SimpleNamespace(effective_user=SimpleNamespace(id=42))
    before = owner.updated_at
    asyncio.run(tg.meal_slot_left_by_tap(update, SimpleNamespace()))
    db.refresh(owner)
    assert owner.updated_at == before and _ask(db, owner) is None


def test_inside_the_card_every_tap_closes_the_slot_but_the_pager_middle(db, owner):
    meal, item = _one(db, owner)
    _tap(db, f"ml:pk:{meal.id}:a:r:0")
    assert _ask(db, owner)
    nop = _tap(db, "ml:nop")
    assert nop.text == ""
    assert _ask(db, owner)
    _tap(db, f"ml:i:{item.id}:r")
    assert _ask(db, owner) is None
    # Reopened slots are the new kind, not stacked.
    _tap(db, f"ml:g:{item.id}:r")
    _tap(db, f"ml:pk:{meal.id}:a:r:0")
    assert _ask(db, owner)["kind"] == "dish_add"


def test_callback_wrapper_gives_the_message_id_and_delivers_by_tap(db, owner):
    meal, item = _one(db, owner)
    query = SimpleNamespace(
        data=f"ml:g:{item.id}:r",
        answer=AsyncMock(),
        message=SimpleNamespace(message_id=88),
    )
    update = SimpleNamespace(
        callback_query=query, effective_user=SimpleNamespace(id=42)
    )
    tap = AsyncMock()
    with patch.object(tg._card_delivery, "tap", tap):
        asyncio.run(tg.meal_card_callback(update, SimpleNamespace()))
    assert tap.await_count == 1
    assert _ask(db, owner)["message_id"] == 88
    assert meal.id


def test_card_verbs_are_registered_outside_the_conversation(Session, monkeypatch):
    application = _application(monkeypatch)
    by_callback = {
        h.callback: h
        for group in application.handlers.values()
        for h in group
        if isinstance(h, CallbackQueryHandler)
    }
    assert by_callback[tg.meal_card_callback].pattern.match("ml:o:1:r")
    assert not by_callback[tg.meal_card_callback].pattern.match("meal:1:save")
    assert not by_callback[tg.stats_callback].pattern.match("ml:o:1:r")
    assert application.handlers[0].index(by_callback[tg.meal_card_callback]) > 0


# --- N4, the picker --------------------------------------------------------------


def _dishes(db, user, names):
    made = []
    for offset, name in enumerate(names):
        dish = _dish(db, user, name)
        dish.last_used_at = NOW - timedelta(hours=offset + 1)
        made.append(dish)
    db.commit()
    return made


def test_n4_lists_favourites_first_then_recent_with_a_pager(db, owner):
    meal, _item = _one(db, owner)
    _dishes(db, owner, ["хлеб", "сыр", "овсянка", "омлет", "кефир"])
    crud_favorite_meal.create_from_meal(db, meal, "овсянка")
    screen = _tap(db, f"ml:pk:{meal.id}:a:r:0")
    assert screen.text == (
        f"<b>➕ Блюдо в приём {WHEN}</b>\nНажми или напиши название."
    )
    labels = _rows(screen)
    assert labels[0][0] == "⭐ овсянка"
    assert labels[2] == ["◀️", "1/2", "▶️"]
    assert _button(screen, "1/2") == "ml:nop"
    assert labels[-1] == ["⬅️ К приёму"]
    assert _data(screen)[2][2] == f"ml:pk:{meal.id}:a:r:1"
    second = _tap(db, f"ml:pk:{meal.id}:a:r:1")
    assert _rows(second)[-2][1] == "2/2"
    every = [b for page in (screen, second) for row in _rows(page)[:-2] for b in row]
    assert "хлеб" in every and "кефир" in every or "кефир" in str(_rows(second))


def test_one_page_has_no_pager(db, owner):
    meal, _item = _one(db, owner, "курица")
    pick = _tap(db, f"ml:pk:{meal.id}:a:r:0")
    assert _rows(pick) == [["курица"], ["⬅️ К приёму"]]


def test_empty_base_is_the_second_line_and_back(db, owner):
    meal = Meal(
        user_id=owner.id, description="суп", meal_time=EATEN, numbers_basis="none"
    )
    db.add(meal)
    db.commit()
    screen = _tap(db, f"ml:pk:{meal.id}:a:r:0")
    assert _text_lines(screen)[1] == "Нажми или напиши название."
    assert _rows(screen) == [["⬅️ К приёму"]]
    assert _ask(db, owner)["kind"] == "dish_add"


def test_pick_adds_at_the_dishs_portion_with_undo_in_the_third_slot(db, owner):
    meal, _item = _one(db, owner)
    _other, bread_item = _one(db, owner, "хлеб", 100.0, 40.0)
    screen = _tap(db, f"ml:pa:{meal.id}:{bread_item.personal_food_id}:r")
    lines = _text_lines(screen)
    assert lines[0] == "<i>✓ Добавлено: хлеб</i>" and lines[1] == ""
    assert lines[2].startswith("<b>🍽 Приём")
    assert _rows(screen)[2] == ["🔢 Цифры", "Ещё ›", "↩️ Вернуть"]
    db.refresh(meal)
    assert meal.calories == 430.0
    # One render: the next open has no undo.
    assert "↩️ Вернуть" not in str(_rows(_tap(db, f"ml:o:{meal.id}:r")))


def test_replace_picks_keeps_quantity_and_says_it(db, owner):
    meal, item = _one(db, owner)
    _other, turkey = _one(db, owner, "индейка", 200.0, 100.0)
    pick = _tap(db, f"ml:pk:{item.id}:r:r:0")
    assert pick.text == "<b>🔄 Вместо «курица»</b>\nНажми или напиши название."
    assert _rows(pick) == [["индейка"], ["⬅️ К блюду в приёме"]]
    assert _data(pick)[0][0] == f"ml:pr:{item.id}:{turkey.personal_food_id}:r"
    screen = _tap(db, _data(pick)[0][0])
    assert _text_lines(screen)[0] == "<i>✓ Заменено: курица → индейка</i>"
    assert "↩️ Вернуть" in _rows(screen)[2]
    assert meal.id


def test_plate_replace_and_remove_say_the_numbers_stayed(db, owner):
    meal = _meal(db, owner, ["рис", "курица", "овощи"])
    rice, chicken, _veg = crud_meal_item.live(db, meal.id)
    bread = _dish(db, owner, "хлеб")
    replaced = _tap(db, f"ml:pr:{rice.id}:{bread.id}:r")
    assert _text_lines(replaced)[0] == (
        "<i>✓ Заменено: рис → хлеб. "
        "Цифры посчитаны на всю тарелку, поэтому не менялись.</i>"
    )
    removed = _tap(db, f"ml:x:{chicken.id}:r")
    assert _text_lines(removed)[0] == (
        "<i>✓ Убрано: курица. Цифры посчитаны на всю тарелку, поэтому не менялись.</i>"
    )
    db.refresh(meal)
    assert meal.calories == 620.0


def test_a_gone_dish_redraws_the_picker(db, owner):
    meal, item = _one(db, owner)
    for data in (f"ml:pa:{meal.id}:999:r", f"ml:pr:{item.id}:999:r"):
        screen = _tap(db, data)
        assert _text_lines(screen)[0] == "<i>Этого блюда уже нет в списке.</i>"
        assert "Нажми или напиши название." in screen.text


def test_same_dish_replace_is_a_toast(db, owner):
    _meal_row, item = _one(db, owner)
    events = _counts(db)
    screen = _tap(db, f"ml:pr:{item.id}:{item.personal_food_id}:r")
    assert screen.toast == "Уже так" and screen.text == ""
    assert _counts(db) == events


# --- N2 removal, N6, undo --------------------------------------------------------


def test_remove_goes_to_the_card_with_notice_and_undo(db, owner):
    meal, _rice, chicken, _veg = _three_counted(db, owner)
    screen = _tap(db, f"ml:x:{chicken.id}:r")
    assert _text_lines(screen)[0] == "<i>✓ Убрано: курица</i>"
    assert "курица" not in _rows(screen)[0]
    undo = _button(screen, "↩️ Вернуть")
    back = _tap(db, undo)
    assert _text_lines(back)[0] == "<i>✓ Возвращено</i>"
    assert "курица · 2 порции" in back.text
    assert meal.id


def test_removing_the_last_dish_is_n6(db, owner):
    meal, item = _one(db, owner)
    screen = _tap(db, f"ml:x:{item.id}:t")
    assert screen.text.startswith("<b>🗑 Приём удалён</b>")
    assert _rows(screen)[0] == ["↩️ Вернуть"]
    db.refresh(meal)
    assert meal.deleted_at is not None


def test_n6_is_the_spec_screen_and_back_follows_the_origin(db, owner):
    meal = _meal(db, owner, ["рис", "курица", "овощи"])
    screen = _tap(db, f"ml:d:{meal.id}:t")
    assert screen.text == "\n".join(
        [
            "<b>🗑 Приём удалён</b>",
            "Пн 26 октября, 13:20: рис, курица и овощи.",
            "Вернуть можно и позже, пока эта кнопка на экране.",
        ]
    )
    event = db.scalars(select(RecordEvent).order_by(RecordEvent.id.desc())).first()
    assert _data(screen) == [[f"ml:u:{event.id}:t"], ["ml:dl:20261026:t"]]
    assert _rows(screen)[1] == ["⬅️ Приёмы дня"]
    assert meal.calories == 620.0


def test_n6_from_the_line_leads_to_the_day_card(db, owner):
    meal, _item = _one(db, owner)
    screen = _tap(db, f"ml:d:{meal.id}:r")
    assert _rows(screen)[1] == ["📊 Карточка дня"]
    assert _data(screen)[1] == ["st:t"]


def test_undo_restores_exactly_a_second_tap_opens_the_card(db, owner):
    meal = _meal(db, owner, ["рис", "курица"])
    deleted = _tap(db, f"ml:d:{meal.id}:r")
    undo = _button(deleted, "↩️ Вернуть")
    back = _tap(db, undo)
    assert _text_lines(back)[0] == "<i>✓ Возвращено</i>"
    assert "рис" in back.text
    db.refresh(meal)
    assert meal.deleted_at is None and meal.calories == 620.0
    events = _counts(db)
    again = _tap(db, undo)
    assert _counts(db) == events
    assert "С тех пор" not in again.text
    assert again.text.startswith("<b>🍽 Приём")


def test_the_undo_can_itself_be_undone(db, owner):
    meal = _meal(db, owner, ["рис", "курица"])
    deleted = _tap(db, f"ml:d:{meal.id}:r")
    restored = _tap(db, _button(deleted, "↩️ Вернуть"))
    gone = _tap(db, _button(restored, "↩️ Вернуть"))
    assert gone.text.startswith("<b>🗑 Приём удалён</b>")
    db.refresh(meal)
    assert meal.deleted_at is not None


def test_undo_guard_refuses_after_a_planted_later_change(db, owner):
    meal, rice, chicken, _veg = _three_counted(db, owner)
    removed = _tap(db, f"ml:x:{chicken.id}:r")
    undo = _button(removed, "↩️ Вернуть")
    # Known positive: this later change is what the guard must see.
    meal_items.set_quantity(db, owner.id, rice.id, 2.0)
    db.refresh(meal)
    calories = meal.calories
    events = _counts(db)
    screen = _tap(db, undo)
    assert _text_lines(screen)[0] == (
        "<i>С тех пор запись менялась, поэтому возврат не применён.</i>"
    )
    assert screen.text.split("\n\n", 1)[1].startswith("<b>🍽 Приём")
    assert _counts(db) == events
    db.refresh(meal)
    assert meal.calories == calories
    assert "курица" not in _rows(screen)[0]


def test_undo_of_grams_stays_on_the_dish(db, owner):
    _meal_row, item = _one(db, owner)
    change = meal_items.set_grams(db, owner.id, item.id, 120.0)
    screen = _tap(db, f"ml:u:{change.event.id}:r")
    assert _text_lines(screen)[2] == "<b>🍲 Курица</b>"
    db.refresh(item)
    assert item.grams is None


# --- N7 and the whole meal's star ----------------------------------------------


def test_n7_is_the_spec_screen(db, owner):
    meal = _meal(db, owner, ["рис", "курица", "овощи"])
    screen = _tap(db, f"ml:mo:{meal.id}:r")
    assert screen.text == f"<b>🍽 Ещё: приём {WHEN}</b>\nрис, курица и овощи"
    assert _rows(screen) == [["⭐ Весь приём"], ["⬅️ К приёму"]]
    assert _data(screen)[0] == [f"ml:fm:{meal.id}:1:r"]


def test_n7_star_only_with_two_dishes(db, owner):
    meal, _item = _one(db, owner)
    assert _rows(_tap(db, f"ml:mo:{meal.id}:r")) == [["⬅️ К приёму"]]


def test_whole_meal_star_marks_and_undoes(db, owner):
    meal = _meal(db, owner, ["рис", "курица"])
    on = _tap(db, f"ml:fm:{meal.id}:1:r")
    assert "Весь приём в избранном." in on.text
    assert _button(on, "✓ ⭐ Весь приём") == f"ml:fm:{meal.id}:0:r"
    fav = crud_favorite_meal.list_active(db, owner.id)[0]
    assert fav.calories == 620.0 and fav.source_meal_id == meal.id
    assert _tap(db, f"ml:fm:{meal.id}:1:r").toast == "Уже так"
    off = _tap(db, f"ml:fm:{meal.id}:0:r")
    assert off.toast == "Убрано из избранного"
    assert "в избранном" not in off.text
    assert crud_favorite_meal.list_active(db, owner.id) == []


# --- stale, foreign, malformed --------------------------------------------------


def _all_verbs(meal_id, item_id, food_id, event_id):
    return [
        f"ml:o:{meal_id}:r",
        f"ml:op:{meal_id}:1:r",
        f"ml:mo:{meal_id}:r",
        f"ml:r:{meal_id}",
        f"ml:n:{meal_id}:1:r",
        f"ml:d:{meal_id}:r",
        f"ml:fm:{meal_id}:1:r",
        f"ml:pk:{meal_id}:a:r:0",
        f"ml:pa:{meal_id}:{food_id}:r",
        f"ml:i:{item_id}:r",
        f"ml:q:{item_id}:2:r",
        f"ml:g:{item_id}:r",
        f"ml:x:{item_id}:r",
        f"ml:ni:{item_id}:1:r",
        f"ml:fi:{item_id}:1:r",
        f"ml:pk:{item_id}:r:r:0",
        f"ml:pr:{item_id}:{food_id}:r",
        f"ml:t:{meal_id}:r",
        f"ml:ts:{meal_id}:1700000000:e:r",
    ]


def test_every_verb_on_a_deleted_meal_is_todays_card_with_the_notice(db, owner):
    meal, item = _one(db, owner)
    _o, other = _one(db, owner, "хлеб")
    meal_items.delete_meal(db, owner.id, meal.id)
    events = _counts(db)
    for data in _all_verbs(meal.id, item.id, other.personal_food_id, 0):
        screen = _tap(db, data)
        assert screen.text.startswith(DELETED_NOTICE + "\n\n<b>📊 Сегодня"), data
    assert _counts(db) == events
    assert _ask(db, owner) is None


def test_every_verb_with_a_foreign_id_changes_nothing_and_warns(
    db, owner, stranger, caplog
):
    meal, item = _one(db, stranger)
    fav_before = crud_favorite_meal.list_active(db, stranger.id)
    change = meal_items.set_quantity(db, stranger.id, item.id, 2.0)
    events = _counts(db)
    verbs = _all_verbs(meal.id, item.id, item.personal_food_id, 0) + [
        f"ml:u:{change.event.id}:r"
    ]
    for data in verbs:
        with caplog.at_level(logging.WARNING, logger="app.services.meal_card"):
            caplog.clear()
            screen = _tap(db, data)
        assert screen.text.startswith("<b>📊 Сегодня"), data
        assert "уже нет" not in screen.text, data
        assert any("meal card" in r.getMessage() for r in caplog.records), data
    assert _counts(db) == events
    db.refresh(meal)
    assert meal.deleted_at is None and meal.calories == 660.0
    assert crud_favorite_meal.list_active(db, stranger.id) == fav_before
    assert crud_favorite_meal.list_active(db, owner.id) == []
    # Control: the same verb by the owner of the row does work.
    assert _tap(db, f"ml:q:{item.id}:h:r", telegram_id=77).text.startswith(
        "<i>✓ ½ порции</i>"
    )


def test_a_foreign_undo_already_spent_does_not_open_their_meal(
    db, owner, stranger, caplog
):
    meal, item = _one(db, stranger)
    change = meal_items.set_quantity(db, stranger.id, item.id, 2.0)
    assert meal_items.undo(db, stranger.id, change.event.id).status == meal_items.DONE
    with caplog.at_level(logging.WARNING, logger="app.services.meal_card"):
        screen = _tap(db, f"ml:u:{change.event.id}:r")
    assert screen.text.startswith("<b>📊 Сегодня")
    assert any("meal card" in r.getMessage() for r in caplog.records)


def test_a_foreign_dish_id_in_the_picker_is_unknown_data(db, owner, stranger, caplog):
    meal, _item = _one(db, owner)
    _o, foreign = _one(db, stranger, "чужое")
    with caplog.at_level(logging.WARNING, logger="app.services.meal_card"):
        screen = _tap(db, f"ml:pa:{meal.id}:{foreign.personal_food_id}:r")
    assert screen.text.startswith("<b>📊 Сегодня")
    assert caplog.records
    assert len(crud_meal_item.live(db, meal.id)) == 1


@pytest.mark.parametrize(
    "data",
    [
        "ml:",
        "ml:o",
        "ml:o:abc:r",
        "ml:o:1",
        "ml:o:1:z",
        "ml:o:1:r:extra",
        "ml:q:1:5:r",
        "ml:q:1:2",
        "ml:n:1:7:r",
        "ml:pk:1:z:r:0",
        "ml:pk:1:a:r:x",
        "ml:dl:2026:t",
        "ml:dl:20261399:t",
        "ml:dl:20261026:r",
        "ml:nosuch:1",
        "ml:o:" + "9" * 40 + ":r",
    ],
)
def test_malformed_data_is_todays_card_and_a_warning(db, owner, data, caplog):
    with caplog.at_level(logging.WARNING, logger="app.services.meal_card"):
        screen = _tap(db, data)
    assert screen.text.startswith("<b>📊 Сегодня")
    assert caplog.records


def test_a_known_good_id_of_each_shape_does_not_warn(db, owner, caplog):
    meal, item = _one(db, owner)
    with caplog.at_level(logging.WARNING, logger="app.services.meal_card"):
        _tap(db, f"ml:o:{meal.id}:r")
        _tap(db, f"ml:i:{item.id}:r")
        _tap(db, "ml:dl:20261026:t")
    assert not caplog.records


def test_a_removed_dish_opens_the_card_with_its_notice(db, owner):
    meal, _rice, chicken, _veg = _three_counted(db, owner)
    meal_items.remove_item(db, owner.id, chicken.id)
    for data in (
        f"ml:i:{chicken.id}:r",
        f"ml:q:{chicken.id}:2:r",
        f"ml:g:{chicken.id}:r",
        f"ml:x:{chicken.id}:r",
        f"ml:fi:{chicken.id}:1:r",
        f"ml:pk:{chicken.id}:r:r:0",
    ):
        screen = _tap(db, data)
        assert _text_lines(screen)[0] == DISH_GONE, data
        assert screen.text.split("\n\n", 1)[1].startswith("<b>🍽 Приём"), data
    assert meal.id


def test_a_user_with_no_row_gets_an_empty_todays_card(db):
    assert "<b>📊 Сегодня" in _tap(db, "ml:o:1:r", telegram_id=999).text


# --- the day list (the bridge to the card) ----------------------------------------


def test_day_list_lines_chips_and_back(db, owner):
    first, _i = _one(db, owner, "овсянка", meal_time=EATEN - timedelta(hours=5))
    second = _meal(db, owner, ["рис", "курица", "овощи"])
    third, _i = _one(db, owner, "суп", meal_time=EATEN + timedelta(hours=5))
    third.time_precision = "approx"
    fourth, _i = _one(db, owner, "яблоко", meal_time=EATEN - timedelta(hours=1))
    fourth.time_precision = "day_only"
    db.commit()
    screen = _tap(db, "ml:dl:20261026:t")
    assert screen.text == "\n".join(
        [
            "<b>🍽 Приёмы, пн 26 октября</b>",
            "8:20 · овсянка",
            "без времени · яблоко",
            "13:20 · рис, курица и овощи",
            "≈ 18:20 · суп",
            "<i>Нажми на время, чтобы открыть приём и поправить.</i>",
        ]
    )
    assert _rows(screen) == [
        ["8:20", "без времени", "13:20"],
        ["≈ 18:20"],
        ["‹ вс 25"],
        ["⬅️ Назад: сегодня"],
    ]
    assert _data(screen)[0][2] == f"ml:o:{second.id}:t"
    assert _button(screen, "‹ вс 25") == "ml:dl:20261025:t"
    assert _button(screen, "⬅️ Назад: сегодня") == "st:t"
    assert first.id and third.id


@pytest.mark.parametrize(
    "origin, label, target",
    [
        ("t", "⬅️ Назад: сегодня", "st:t"),
        ("y", "⬅️ Назад: вчера", "st:y"),
        ("w", "⬅️ Назад: 7 дней", "st:w"),
    ],
)
def test_day_list_back_is_kept_while_walking(db, owner, origin, label, target):
    earlier = _tap(db, f"ml:dl:20261020:{origin}")
    assert _rows(earlier)[-1] == [label]
    assert _data(earlier)[-1] == [target]
    assert _rows(earlier)[-2] == ["‹ пн 19", "ср 21 ›"]
    assert _button(earlier, "ср 21 ›") == f"ml:dl:20261021:{origin}"


def test_an_empty_day_says_so_and_a_duplicate_clock_is_numbered(db, owner):
    empty = _tap(db, "ml:dl:20261025:t")
    assert empty.text == "<b>🍽 Приёмы, вс 25 октября</b>\nВ этом дне записей нет."
    _one(db, owner, "сыр")
    _one(db, owner, "хлеб")
    assert _rows(_tap(db, "ml:dl:20261026:t"))[0] == ["13:20", "13:20 · 2"]


def test_more_than_nine_meals_page_with_a_last_slot(db, owner):
    for offset in range(10):
        _one(db, owner, f"блюдо {offset}", meal_time=EATEN + timedelta(minutes=offset))
    screen = _tap(db, "ml:dl:20261026:t")
    chips = [label for row in _rows(screen)[:3] for label in row]
    assert len(chips) == 9
    assert chips[-1] == "▶️"
    second = _tap(db, _data(screen)[2][2])
    labels = [label for row in _rows(second)[:-2] for label in row]
    assert labels[0] == "13:28" and labels[1] == "13:29"
    assert _rows(screen)[0][0] == "13:20"


def test_a_long_name_is_cut_at_eighty_on_the_day_list(db, owner):
    _one(db, owner, "щ" * 100)
    line = _text_lines(_tap(db, "ml:dl:20261026:t"))[1]
    assert line == "13:20 · " + "щ" * 79 + "…"


def test_a_deleted_meal_is_not_on_the_day_list(db, owner):
    meal, _i = _one(db, owner, "сыр")
    meal_items.delete_meal(db, owner.id, meal.id)
    assert "сыр" not in _tap(db, "ml:dl:20261026:t").text


# --- the bridges ---------------------------------------------------------------


def _day_keyboard(db, tab=stats_screen.TODAY, now=NOW):
    return stats_screen.open_view(db, 42, tab, now)


def test_the_day_card_opens_the_days_meals(db, owner):
    _one(db, owner, "сыр")
    card = _day_keyboard(db)
    assert _button(card, "🍽 Приёмы") == "ml:dl:20261026:t"
    assert ["🔢 Цифры", "🍽 Приёмы"] in _rows(card)
    day_list = _tap(db, _button(card, "🍽 Приёмы"))
    assert day_list.text.startswith("<b>🍽 Приёмы, пн 26 октября</b>")


def test_yesterdays_card_opens_yesterdays_meals(db, owner):
    _one(db, owner, "сыр", meal_time=EATEN - timedelta(days=1))
    card = _day_keyboard(db, stats_screen.YESTERDAY)
    assert _button(card, "🍽 Приёмы") == "ml:dl:20261025:y"


def test_an_empty_today_has_no_way_to_the_meals(db, owner):
    assert "🍽 Приёмы" not in str(_rows(_day_keyboard(db)))


def test_saved_draft_footer_opens_the_card(db, owner):
    update = SimpleNamespace(effective_user=SimpleNamespace(id=42, username="owner"))
    replies = []

    async def reply(body, **kwargs):
        replies.append(body)

    draft = {
        "description": "рис и курица",
        "nutrition": {
            "foods": ["рис", "курица"],
            "calories": 500.0,
            "protein": 20.0,
            "fats": 10.0,
            "carbs": 60.0,
        },
    }
    with patch("celery_app.tasks.personal_food.embed_and_save_personal_food.delay"):
        saved = asyncio.run(
            tg._save_current_meal(
                update.effective_user,
                draft,
                {"meal_time": EATEN},
                reply,
                favorite=False,
            )
        )
    meal = db.scalars(select(Meal)).one()
    assert saved == meal.id
    markup = tg._day_card_button(saved)
    assert [[b.text for b in row] for row in markup.inline_keyboard] == [
        ["🍽 Приём", "📊 Карточка дня"]
    ]
    assert markup.inline_keyboard[0][0].callback_data == f"ml:o:{meal.id}:r"
    assert markup.inline_keyboard[0][1].callback_data == "st:t"
    assert _tap(db, markup.inline_keyboard[0][0].callback_data).text.startswith(
        "<b>🍽 Приём"
    )
    # Elsewhere, where no meal is at hand, only the day.
    assert [[b.text for b in row] for row in tg._day_card_button().inline_keyboard] == [
        ["📊 Карточка дня"]
    ]


# --- layout and copy ---------------------------------------------------------------


BIG = 10**12


def _every_screen(db, owner):
    """Every screen the module draws, by the verbs a person taps."""
    _dishes(db, owner, ["хлеб", "сыр", "овсянка", "омлет", "кефир", "щи"])
    many = _meal(db, owner, ["рис", "курица", "овощи", "хлеб", "сыр"])
    plate_items = crud_meal_item.live(db, many.id)
    one, item = _one(db, owner, "творог")
    legacy = Meal(
        user_id=owner.id, description="суп", meal_time=EATEN, numbers_basis="plate"
    )
    unread = Meal(user_id=owner.id, meal_time=EATEN, photos=["P"], numbers_basis="none")
    db.add_all([legacy, unread])
    db.commit()
    screens = []
    for origin in ("r", "t", f"h{BIG}"):
        for data in (
            f"ml:o:{many.id}:{origin}",
            f"ml:op:{many.id}:1:{origin}",
            f"ml:o:{one.id}:{origin}",
            f"ml:n:{one.id}:1:{origin}",
            f"ml:o:{legacy.id}:{origin}",
            f"ml:o:{unread.id}:{origin}",
            f"ml:mo:{many.id}:{origin}",
            f"ml:i:{plate_items[0].id}:{origin}",
            f"ml:i:{item.id}:{origin}",
            f"ml:ni:{item.id}:1:{origin}",
            f"ml:g:{item.id}:{origin}",
            f"ml:pk:{many.id}:a:{origin}:0",
            f"ml:pk:{item.id}:r:{origin}:0",
            f"ml:t:{one.id}:{origin}",
        ):
            screens.append(_tap(db, data))
    for origin in ("t", "y", "w"):
        screens.append(_tap(db, f"ml:dl:20261026:{origin}"))
    screens.append(_tap(db, f"ml:q:{item.id}:2:r"))
    screens.append(_tap(db, f"ml:pa:{one.id}:{item.personal_food_id}:r"))
    screens.append(_tap(db, f"ml:x:{plate_items[0].id}:r"))
    screens.append(_tap(db, f"ml:d:{one.id}:r"))
    return screens


def test_layout_rows_buttons_and_label_budgets(db, owner):
    screens = _every_screen(db, owner)
    assert len(screens) > 30
    for screen in screens:
        rows = _rows(screen)
        assert len(rows) <= 5, rows
        for row in rows:
            assert len(row) <= 3, rows
            budget = {1: 34, 2: 20, 3: 13}[len(row)]
            for label in row:
                assert len(label) <= budget, (label, rows)
        for row in _data(screen):
            for data in row:
                assert len(data.encode()) <= 64, data


def test_callback_stays_in_64_bytes_at_worst_case_ids(db, owner):
    worst = str(10**17)
    for data in (
        f"ml:pa:{worst}:{worst}:h{worst}",
        f"ml:pr:{worst}:{worst}:h{worst}",
        f"ml:pk:{worst}:a:h{worst}:99",
        f"ml:op:{worst}:99:h{worst}",
    ):
        assert len(data.encode()) <= 64, data
        # And the module reads them: unknown row, but not malformed.
        assert meal_card.is_known_shape(data), data


def test_every_label_mark_is_drawn(db, owner):
    labels = {
        label
        for screen in _every_screen(db, owner)
        for row in _rows(screen)
        for label in row
    }
    for expected in (
        "➕ Блюдо",
        "🕐 Время",
        "🗑 Удалить",
        "🔢 Цифры",
        "✓ 🔢 Цифры",
        "Ещё ›",
        "▶️",
        "🔄 Заменить",
        "🗑 Из приёма",
        "½",
        "×1",
        "✓ ×1",
        "✓ ×2",
        "⚖️ Граммы",
        "⭐ В избранное",
        "⭐ Весь приём",
        "↩️ Вернуть",
        "⬅️ К ответу бота",
        "⬅️ Приёмы дня",
        "⬅️ Когда было",
        "⬅️ К приёму",
        "⬅️ К блюду в приёме",
        "📊 Карточка дня",
        "⬅️ Назад: сегодня",
        "⬅️ Назад: вчера",
        "⬅️ Назад: 7 дней",
    ):
        assert expected in labels, expected


def test_copy_rules_over_every_screen(db, owner):
    for screen in _every_screen(db, owner):
        labels = [label for row in _rows(screen) for label in row]
        assert "🗑 Убрать" not in labels
        assert "⬅️ Приём" not in labels
        assert "⬅️ Сегодня" not in labels
        assert not re.search(r"исходн", screen.text, re.IGNORECASE)
        # An estimated total carries its mark.
        for line in screen.text.split("\n"):
            if "🔥" in line:
                assert "🔥 ≈" in line, line
        # A bot line naming a meal carries its day.
        title = screen.text.split("\n", 1)[0]
        if title.startswith("<b>🍽 Приём,") or title.startswith("<b>➕ Блюдо в"):
            assert "пн 26 октября" in title


def test_no_gender_no_judging_no_register_in_the_module():
    source = inspect.getsource(meal_card)
    assert findings("app/services/meal_card.py", source) == []
    # Control: the checker sees a planted form through the same call.
    assert findings("app/services/meal_card.py", 'X = "Записала"\n') != []


def test_the_day_is_the_users_not_utcs(db, owner):
    # 22:30 UTC on the 25th is 2:30 on the 26th at UTC+4.
    meal, _item = _one(db, owner, meal_time=datetime(2026, 10, 25, 22, 30, tzinfo=UTC))
    assert "пн 26 октября, 2:30" in _tap(db, f"ml:o:{meal.id}:r").text
    assert date(2026, 10, 26) == meal_card.meal_day(meal, owner)
