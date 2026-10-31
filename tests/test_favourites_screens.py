"""Favourites as section 6 says, and how a merge, a hide and an old name reach them.

On real rows, through the real application at the wire. A row of the list logs, a pencil
opens the favourite, and a portion typed there is the dish's one number. ``reader``
stands in for the model and is asserted not to be asked; nothing here reaches the OpenAI
client.
"""

from __future__ import annotations

import logging
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.crud.crud_meal_item import crud_meal_item
from app.models import FavoriteMeal, Meal, MealMessage, PersonalFood, RecordEvent
from app.services import favorites, meal_card, meal_items
from app.services.numbers import SHOW_NUMBERS
from tests.test_capture_router import (  # noqa: F401 (fixtures)
    OWNER,
    Session,
    db,
    owner,
    reader,
    send,
    text,
)
from tests.test_dishes import (  # noqa: F401 (fixtures)
    _data,
    _dish,
    _eat,
    _fits,
    _labels,
    _meals,
    _no_screen_from_another_test,
    _on,
    _per_100,
    _shown,
    _star,
    _toast,
    _typed_reply,
    hard_day,
    stranger,
    tap,
)

INT_MAX = 2**31 - 1
HEAD = "<b>⭐ Избранное</b>"
HINT = "Нажатие на название записывает сразу. Кнопка ✏️: порция и другие настройки."
STALE = "<i>Этого блюда уже нет в избранном. Вот актуальный список.</i>"
AS_THAT_MEAL = "Порция: как в той записи."
TODAY_TITLE = "📊 Сегодня"


def _starred(db, owner, name="омлет с сыром", *, grams=None, calories=360.0, **dish):
    food = _dish(db, owner, name, **dish)
    fav = _star(db, owner, food, calories=calories)
    if grams:
        fav.usual_grams = grams
        db.commit()
    return food, fav


def _numbers_on(db, owner) -> None:
    owner.settings = {**owner.settings, SHOW_NUMBERS: True}
    db.commit()


def _fresh(db, row):
    db.expire_all()
    return db.get(type(row), row.id)


def _newest(db, owner) -> Meal:
    return max(_meals(db, owner), key=lambda meal: meal.id)


def _events(db, kind) -> list:
    db.expire_all()
    return list(db.scalars(select(RecordEvent).where(RecordEvent.kind == kind)))


def _aged(db, meal: Meal, seconds: int) -> None:
    meal.created_at = meal.created_at - timedelta(seconds=seconds)
    db.commit()


# --- O9 the list --------------------------------------------------------------------


def test_o9_a_row_is_a_name_and_a_pencil_with_no_way_back(db, owner, reader):
    _, omelette = _starred(db, owner)
    shown = _shown("fav:p:0")
    assert shown["text"] == f"{HEAD}\n{HINT}"
    assert _labels(shown) == [["Омлет с сыром", "✏️"]]
    assert _data(shown) == [[f"fav:l:{omelette.id}:n", f"fav:o:{omelette.id}:0"]]
    _fits(shown)
    assert _meals(db, owner) == []


def test_o9_from_the_menu_is_the_same_screen(db, owner):
    _, omelette = _starred(db, owner)
    sent = send(text("⭐ Избранное")).sent()[-1]
    assert sent["text"] == f"{HEAD}\n{HINT}"
    assert _labels(sent) == [["Омлет с сыром", "✏️"]]
    assert _data(sent)[0][0] == f"fav:l:{omelette.id}:n"


def test_o9_a_long_name_keeps_to_the_budget_of_a_pair(db, owner):
    _starred(db, owner, "очень длинное название одного блюда")
    shown = _shown("fav:p:0")
    assert _labels(shown)[0][0].endswith("…")
    _fits(shown)


def test_o9_says_an_estimate_with_numbers_on_and_a_checked_number_without(db, owner):
    _numbers_on(db, owner)
    _starred(db, owner)
    label = _labels(_shown("fav:p:0"))[0][0]
    assert label == "Омлет с сыром · ≈ 360 ккал"

    _starred(db, owner, "творог", grams=150, numbers_quality="barcode", calories=200.0)
    labels = [row[0] for row in _labels(_shown("fav:p:0"))]
    assert "Творог · 200 ккал" in labels
    assert "Омлет с сыром · ≈ 360 ккал" in labels


def test_o9_with_numbers_off_says_no_number(db, owner):
    _starred(db, owner)
    assert "ккал" not in str(_labels(_shown("fav:p:0")))


def test_o9_the_pager_says_where_it_is_and_its_middle_only_answers(db, owner):
    for number in range(5):
        _starred(db, owner, f"блюдо {number}")
    first = _shown("fav:p:0")
    assert _labels(first)[-1] == ["1/2", "▶️"]
    assert _data(first)[-1] == ["fav:x", "fav:p:1"]
    _fits(first)
    second = _shown("fav:p:1")
    assert _labels(second)[-1] == ["◀️", "2/2"]
    wire = tap("fav:x")
    assert wire.edits() == []


def test_o9_a_pencil_on_a_second_page_comes_back_to_it(db, owner):
    for number in range(5):
        _starred(db, owner, f"блюдо {number}")
    pencil = _data(_shown("fav:p:1"))[0][1]
    assert pencil.endswith(":1")
    assert _on(_shown(pencil), "⬅️ Избранное") == "fav:p:1"


def test_o9_a_removed_favourite_redraws_with_the_notice_and_logs_nothing(
    db, owner, reader
):
    _, gone = _starred(db, owner)
    _, kept = _starred(db, owner, "овсянка")
    tap(f"fav:r:{gone.id}:0")
    shown = _shown(f"fav:l:{gone.id}:n")
    assert shown["text"] == f"{STALE}\n\n{HEAD}\n{HINT}"
    assert _labels(shown) == [["Овсянка", "✏️"]]
    assert _meals(db, owner) == []
    # The control: the very same tap on one still there does log.
    tap(f"fav:l:{kept.id}:n")
    assert len(_meals(db, owner)) == 1


def test_o9_on_a_hard_day_is_the_same_screen(db, owner, hard_day):
    _starred(db, owner)
    shown = _shown("fav:p:0")
    assert shown["text"] == f"{HEAD}\n{HINT}"
    assert _labels(shown) == [["Омлет с сыром", "✏️"]]


def test_o9a_the_empty_copy_names_a_button_that_is_there(db, owner):
    empty = _shown("fav:p:0")
    assert (
        empty["text"]
        == f"{HEAD}\nПока пусто. ⭐ ставится кнопкой «⭐ В избранное» у блюда."
    )
    assert _labels(empty) == []
    food = _dish(db, owner, "омлет")
    assert _on(_shown(f"dh:o:{food.id}:l0"), "⭐ В избранное")


# --- O10 logged ---------------------------------------------------------------------


def test_o10_a_row_logs_at_now_with_one_item_and_no_model(db, owner, reader):
    food, fav = _starred(db, owner, grams=200.0)
    wire = tap(f"fav:l:{fav.id}:n")
    shown = wire.edits()[-1]
    assert shown["text"] == "Записано: омлет с сыром."
    assert _toast(wire) == "Записано"
    meal = _newest(db, owner)
    assert _labels(shown) == [["↩️ Отменить", "🕐 Время"], ["⬅️ Избранное"]]
    assert _data(shown) == [
        [f"fav:u:{meal.id}", f"ml:t:{meal.id}:r"],
        ["fav:p:0"],
    ]
    _fits(shown)
    assert meal.resolution_source == "favorite"
    assert meal.meal_type is None
    assert meal.calories == 360.0
    item = crud_meal_item.live(db, meal.id)[0]
    assert item.personal_food_id == food.id
    assert item.numbers_source == "dish"
    assert len(crud_meal_item.live(db, meal.id)) == 1
    assert reader.text == [] and reader.image == []
    assert _fresh(db, fav).use_count == 1


def test_o10_with_numbers_on_says_the_number_as_a_fresh_line_does(db, owner):
    _numbers_on(db, owner)
    _, fav = _starred(db, owner)
    assert _shown(f"fav:l:{fav.id}:n")["text"] == (
        "Записано: омлет с сыром · ≈ 360 ккал."
    )


def test_o10_from_a_dish_with_numbers_on_says_it_too(db, owner):
    _numbers_on(db, owner)
    food = _dish(db, owner, "омлет с сыром", **_per_100(), usual_grams=200.0)
    assert _shown(f"dh:log:{food.id}")["text"] == (
        "Записано: омлет с сыром · ≈ 360 ккал."
    )


def test_o10_a_reply_to_it_means_that_meal(db, owner):
    _, fav = _starred(db, owner)
    tap(f"fav:l:{fav.id}:n", 5100)
    meal = _newest(db, owner)
    row = db.scalar(select(MealMessage).where(MealMessage.message_id == 5100))
    assert row.meal_id == meal.id


def test_o10_the_time_button_opens_the_meals_time(db, owner):
    _, fav = _starred(db, owner)
    shown = _shown(f"fav:l:{fav.id}:n")
    assert _on(_shown(_on(shown, "🕐 Время")), "Не помню")


def test_o10_a_double_tap_within_three_seconds_is_one_meal(db, owner):
    _, fav = _starred(db, owner)
    tap(f"fav:l:{fav.id}:n", 5200)
    again = tap(f"fav:l:{fav.id}:n", 5200)
    assert _toast(again) == "Уже записано"
    assert again.edits() == []
    assert len(_meals(db, owner)) == 1


def test_o10_the_same_tap_past_three_seconds_is_another_meal(db, owner):
    _, fav = _starred(db, owner)
    tap(f"fav:l:{fav.id}:n", 5200)
    _aged(db, _newest(db, owner), 4)
    again = tap(f"fav:l:{fav.id}:n", 5200)
    assert _toast(again) == "Записано"
    assert len(_meals(db, owner)) == 2


def test_o10_another_message_or_another_favourite_is_another_meal(db, owner):
    _, fav = _starred(db, owner)
    _, other = _starred(db, owner, "овсянка")
    tap(f"fav:l:{fav.id}:n", 5200)
    tap(f"fav:l:{fav.id}:n", 5300)
    assert len(_meals(db, owner)) == 2
    tap(f"fav:l:{other.id}:n", 5200)
    assert len(_meals(db, owner)) == 3


def test_o10_an_undone_log_does_not_hold_the_next_one_back(db, owner):
    _, fav = _starred(db, owner)
    tap(f"fav:l:{fav.id}:n", 5200)
    tap(f"fav:u:{_newest(db, owner).id}", 5200)
    assert _toast(tap(f"fav:l:{fav.id}:n", 5200)) == "Записано"


def test_o10_a_meal_deleted_elsewhere_does_not_hold_the_next_log_back(db, owner):
    _, fav = _starred(db, owner)
    tap(f"fav:l:{fav.id}:n", 5200)
    meal_items.delete_meal(db, owner.id, _newest(db, owner).id)
    assert _toast(tap(f"fav:l:{fav.id}:n", 5200)) == "Записано"


@pytest.mark.parametrize("old_chip", ["n", "b", "l", "d"])
def test_o10_a_chip_on_an_old_message_still_logs_with_no_label(db, owner, old_chip):
    _, fav = _starred(db, owner)
    assert _shown(f"fav:l:{fav.id}:{old_chip}")["text"] == "Записано: омлет с сыром."
    assert _newest(db, owner).meal_type is None


def test_o10a_the_undo_is_soft_and_a_reply_no_longer_means_it(db, owner):
    _, fav = _starred(db, owner)
    tap(f"fav:l:{fav.id}:n", 5200)
    meal = _newest(db, owner)
    shown = tap(f"fav:u:{meal.id}", 5200).edits()[-1]
    assert shown["text"] == "↩️ Запись отменена: омлет с сыром."
    assert _labels(shown) == [["⬅️ Избранное"]]
    assert _data(shown) == [["fav:p:0"]]
    db.expire_all()
    assert db.get(Meal, meal.id).deleted_at is not None
    assert [event.meal_id for event in _events(db, "delete")] == [meal.id]
    assert db.scalar(select(MealMessage).where(MealMessage.message_id == 5200)) is None

    again = _shown(f"fav:u:{meal.id}")
    assert "уже отменена" in again["text"]
    assert len(_events(db, "delete")) == 1


# --- O11 the favourite --------------------------------------------------------------


def test_o11_exact_with_a_typed_portion(db, owner):
    food, fav = _starred(
        db, owner, usual_grams=200.0, usual_grams_by_user=True, grams=200.0
    )
    shown = _shown(f"fav:o:{fav.id}:0")
    assert shown["text"] == "<b>⭐ Омлет с сыром</b>\nОбычная порция: 200 г."
    assert _labels(shown) == [
        ["🍽 Записать"],
        ["⚖️ Порция", "🔢 Цифры"],
        ["🍲 Всё о блюде", "🗑 Из избранного"],
        ["⬅️ Избранное"],
    ]
    assert _data(shown) == [
        [f"fav:l:{fav.id}:n"],
        [f"fav:g:{fav.id}", f"fav:o:{fav.id}:0:1"],
        [f"dh:o:{food.id}:f{fav.id}", f"fav:r:{fav.id}:0"],
        ["fav:p:0"],
    ]
    _fits(shown)


def test_o11_fits_at_the_longest_ids(db, owner):
    food = _dish(db, owner, "омлет", id=INT_MAX)
    fav = FavoriteMeal(
        id=INT_MAX, user_id=owner.id, name="омлет", personal_food_id=food.id
    )
    db.add(fav)
    db.commit()
    _fits(_shown(f"fav:o:{INT_MAX}:0"))
    _fits(_shown("fav:p:0"))


def test_o11_a_portion_nobody_typed_shows_only_with_numbers_open(db, owner):
    _, fav = _starred(db, owner, grams=200.0)
    closed = _shown(f"fav:o:{fav.id}:0")
    assert closed["text"] == "<b>⭐ Омлет с сыром</b>"
    opened = _shown(f"fav:o:{fav.id}:0:1")
    assert opened["text"].split("\n")[:2] == [
        "<b>⭐ Омлет с сыром</b>",
        "Обычная порция: 200 г.",
    ]
    assert "🔥 ≈ 360 ккал" in opened["text"]
    assert _on(opened, "✓ 🔢 Цифры") == f"fav:o:{fav.id}:0"


def test_o11_a_star_with_no_weight_says_so_only_with_numbers_open(db, owner):
    _, fav = _starred(db, owner)
    assert AS_THAT_MEAL not in _shown(f"fav:o:{fav.id}:0")["text"]
    assert AS_THAT_MEAL in _shown(f"fav:o:{fav.id}:0:1")["text"]


def test_o11_with_numbers_on_has_them_and_no_button_for_them(db, owner):
    _numbers_on(db, owner)
    _, fav = _starred(db, owner)
    shown = _shown(f"fav:o:{fav.id}:0")
    assert "🔥 ≈ 360 ккал" in shown["text"]
    assert _labels(shown)[1] == ["⚖️ Порция"]


def test_o11_a_star_with_no_numbers_does_not_guess(db, owner):
    _, fav = _starred(db, owner, calories=None)
    assert "ккал" not in _shown(f"fav:o:{fav.id}:0:1")["text"]


def test_o11_leads_to_the_dish_and_the_dish_comes_back_here(db, owner):
    food, fav = _starred(db, owner)
    card = _shown(_on(_shown(f"fav:o:{fav.id}:0"), "🍲 Всё о блюде"))
    assert "Омлет с сыром" in card["text"]
    assert _on(card, "⬅️ Избранное") == "fav:p:0"


def test_o11_an_old_row_with_no_dish_gets_one_for_that_button(db, owner):
    fav = FavoriteMeal(user_id=owner.id, name="старое блюдо", calories=100.0)
    db.add(fav)
    db.commit()
    shown = _shown(f"fav:o:{fav.id}:0")
    food = _fresh(db, fav).personal_food_id
    assert _on(shown, "🍲 Всё о блюде") == f"dh:o:{food}:f{fav.id}"


def test_o11_escapes_a_name(db, owner):
    _, fav = _starred(db, owner, "<b>самса</b> & чай")
    assert "&lt;b&gt;самса&lt;/b&gt; &amp; чай" in _shown(f"fav:o:{fav.id}:0")["text"]


def test_o11_stale_is_the_list_with_the_notice(db, owner):
    _, fav = _starred(db, owner)
    tap(f"fav:r:{fav.id}:0")
    assert _shown(f"fav:o:{fav.id}:0")["text"].startswith(STALE)
    assert _shown(f"fav:g:{fav.id}")["text"].startswith(STALE)


def test_o11_on_a_hard_day_is_the_same_screen(db, owner, hard_day):
    _, fav = _starred(db, owner)
    assert _labels(_shown(f"fav:o:{fav.id}:0"))[1] == ["⚖️ Порция", "🔢 Цифры"]


@pytest.mark.parametrize(
    "data",
    [
        "fav:",
        "fav:zz",
        "fav:o:abc:0",
        "fav:l:1:q",
        "fav:p",
        "fav:o:5:n",
        "fav:p:n",
        "fav:r:1:n",
        "fav:b:1:n",
        "fav:l:x:b",
        "fav:u:x",
        "fav:g:x",
        f"fav:l:{INT_MAX + 1}:n",
        f"fav:g:{INT_MAX + 1}",
        f"fav:u:{INT_MAX + 1}",
    ],
)
def test_malformed_data_is_today_and_a_warning(db, owner, caplog, data):
    _starred(db, owner)
    with caplog.at_level(logging.WARNING, logger="app.services.favorites"):
        shown = _shown(data)
    assert TODAY_TITLE in shown["text"]
    assert any("unknown or foreign" in record.message for record in caplog.records)


def test_a_foreign_favourite_is_today_and_nothing_changes(db, owner, stranger):
    food = PersonalFood(user_id=stranger.id, canonical_name="чужое")
    db.add(food)
    db.commit()
    theirs = FavoriteMeal(
        user_id=stranger.id, name="чужое", personal_food_id=food.id, calories=1.0
    )
    db.add(theirs)
    db.commit()
    for data in (
        f"fav:o:{theirs.id}:0",
        f"fav:l:{theirs.id}:n",
        f"fav:g:{theirs.id}",
        f"fav:r:{theirs.id}:0",
        f"fav:b:{theirs.id}:0",
    ):
        assert TODAY_TITLE in _shown(data)["text"], data
    db.expire_all()
    assert db.get(FavoriteMeal, theirs.id).removed_at is None
    assert db.get(FavoriteMeal, theirs.id).use_count == 0
    assert _meals(db, owner) == []
    assert not meal_card.has_slot(db, OWNER)
    # The control: the same words on one's own do change things.
    _, mine = _starred(db, owner)
    assert TODAY_TITLE not in _shown(f"fav:o:{mine.id}:0")["text"]


def test_a_foreign_meal_is_not_undone(db, owner, stranger):
    meal = Meal(
        user_id=stranger.id,
        description="чужое",
        meal_time=meal_time(),
        resolution_source="favorite",
    )
    db.add(meal)
    db.commit()
    assert TODAY_TITLE in _shown(f"fav:u:{meal.id}")["text"]
    db.expire_all()
    assert db.get(Meal, meal.id).deleted_at is None


def meal_time():
    from datetime import UTC, datetime

    return datetime(2026, 10, 28, 4, 40, tzinfo=UTC)


# --- O11a the portion ---------------------------------------------------------------


def test_o11a_asks_with_the_exact_words_and_a_way_back(db, owner):
    _, fav = _starred(db, owner)
    shown = _shown(f"fav:g:{fav.id}")
    assert shown["text"] == (
        "<b>⚖️ Порция: омлет с сыром</b>\nНапиши граммы числом, например 200."
    )
    assert _labels(shown) == [["⬅️ К избранному блюду"]]
    assert _data(shown) == [[f"fav:o:{fav.id}:0"]]
    assert meal_card.has_slot(db, OWNER)


def test_o11a_grams_are_the_dishs_one_number_and_the_star_keeps_its_copy(db, owner):
    food, fav = _starred(db, owner, grams=200.0)
    tap(f"fav:g:{fav.id}")
    reply = _typed_reply("300 г")
    assert reply["text"].startswith("<i>✓ Для будущих приёмов.")
    assert "Обычная порция: 300 г." in reply["text"]
    assert _labels(reply)[0] == ["🍽 Записать"]
    fresh = _fresh(db, food)
    assert fresh.usual_grams == 300.0 and fresh.usual_grams_by_user is True
    star = _fresh(db, fav)
    assert star.usual_grams == 200.0 and star.calories == 360.0
    assert [event.food_id for event in _events(db, "portion")] == [food.id]
    # The dish's card says that same number, and the next log scales to it.
    assert "Обычная порция: 300 г." in _shown(f"dh:o:{food.id}:l0")["text"]
    tap(f"fav:l:{fav.id}:n")
    assert _newest(db, owner).calories == pytest.approx(540.0)


def test_o11a_grams_leave_the_numbers_when_the_portion_has_no_weight(db, owner):
    _, fav = _starred(db, owner)
    tap(f"fav:g:{fav.id}")
    assert "Обычная порция: 300 г." in _typed_reply("300")["text"]
    tap(f"fav:l:{fav.id}:n")
    assert _newest(db, owner).calories == pytest.approx(360.0)


def test_o11a_past_meals_stay(db, owner):
    food, fav = _starred(db, owner, grams=200.0)
    tap(f"fav:l:{fav.id}:n")
    logged = _newest(db, owner)
    tap(f"fav:g:{fav.id}")
    _typed_reply("300")
    assert _fresh(db, logged).calories == 360.0


def test_o11a_the_same_grams_again_write_nothing(db, owner):
    _, fav = _starred(db, owner)
    tap(f"fav:g:{fav.id}")
    _typed_reply("300")
    tap(f"fav:g:{fav.id}")
    reply = _typed_reply("300")
    assert reply["text"].startswith("<i>Уже так</i>")
    assert len(_events(db, "portion")) == 1


def test_o11a_the_way_back_undoes_it_and_says_so_on_the_dish(db, owner):
    food, fav = _starred(db, owner)
    tap(f"fav:g:{fav.id}")
    reply = _typed_reply("300")
    undo = _on(reply, "↩️ Вернуть")
    assert undo.startswith("dh:u:") and undo.endswith(f":f{fav.id}")
    _fits(reply)
    shown = _shown(undo)
    assert "✓ Возвращено" in shown["text"]
    assert _on(shown, "⬅️ Избранное") == "fav:p:0"
    assert _fresh(db, food).usual_grams is None


def test_o11a_what_is_no_number_goes_on_to_the_router_and_is_food(db, owner, reader):
    food, fav = _starred(db, owner)
    tap(f"fav:g:{fav.id}")
    assert meal_card.has_slot(db, OWNER)
    send(text("суп с лапшой"))
    db.expire_all()
    assert not meal_card.has_slot(db, OWNER)
    assert _fresh(db, food).usual_grams is None
    assert reader.text, "the words went to the model, as food"


def test_o11a_any_other_tap_closes_the_slot(db, owner, reader):
    food, fav = _starred(db, owner)
    tap(f"fav:g:{fav.id}")
    assert meal_card.has_slot(db, OWNER)
    tap("fav:x")
    db.expire_all()
    assert meal_card.has_slot(db, OWNER), "the pager's middle leaves it alone"
    tap(f"fav:o:{fav.id}:0")
    db.expire_all()
    assert not meal_card.has_slot(db, OWNER)
    send(text("300"))
    assert _fresh(db, food).usual_grams is None


def test_o11a_a_tap_on_a_gone_favourite_closes_the_slot_open_before(db, owner):
    _, fav = _starred(db, owner)
    _, gone = _starred(db, owner, "овсянка")
    tap(f"fav:r:{gone.id}:0")
    tap(f"fav:g:{fav.id}")
    db.expire_all()
    assert meal_card.has_slot(db, OWNER)
    assert _shown(f"fav:g:{gone.id}")["text"].startswith(STALE)
    db.expire_all()
    assert not meal_card.has_slot(db, OWNER)


def test_o11a_a_favourite_removed_meanwhile_answers_with_the_list(db, owner):
    food, fav = _starred(db, owner)
    tap(f"fav:g:{fav.id}")
    with db.bind.connect() as connection:
        connection.execute(
            FavoriteMeal.__table__.update()
            .where(FavoriteMeal.id == fav.id)
            .values(removed_at=meal_time())
        )
        connection.commit()
    reply = _typed_reply("300")
    assert reply["text"].startswith(STALE)
    assert _fresh(db, food).usual_grams is None


# --- O11b out of favourites ---------------------------------------------------------


def test_o11b_exact_and_the_dish_stays(db, owner):
    food, fav = _starred(db, owner)
    shown = _shown(f"fav:r:{fav.id}:0")
    assert shown["text"] == (
        "<b>🗑 Убрано из избранного: омлет с сыром</b>\n"
        "Само блюдо на месте.\n"
        "Вернуть можно и позже, пока эта кнопка на экране."
    )
    assert _labels(shown) == [["↩️ Вернуть"], ["⬅️ Избранное"]]
    assert _data(shown) == [[f"fav:b:{fav.id}:0"], ["fav:p:0"]]
    fresh = _fresh(db, food)
    assert fresh.hidden_at is None and fresh.merged_into_id is None
    assert _fresh(db, fav).removed_at is not None


def test_o11b_the_way_back_opens_the_favourite_again(db, owner):
    _, fav = _starred(db, owner)
    tap(f"fav:r:{fav.id}:0")
    shown = _shown(f"fav:b:{fav.id}:0")
    assert shown["text"].startswith("<i>✓ Возвращено</i>\n\n<b>⭐ Омлет с сыром</b>")
    assert _labels(shown)[0] == ["🍽 Записать"]
    assert _fresh(db, fav).removed_at is None


def test_o11b_the_way_back_after_a_new_star_makes_no_second_one(db, owner):
    food, fav = _starred(db, owner)
    tap(f"fav:r:{fav.id}:0")
    tap(f"dh:fv:{food.id}:1")
    assert len(_labels(_shown("fav:p:0"))) == 1
    shown = _shown(f"fav:b:{fav.id}:0")
    assert "Омлет с сыром" in shown["text"]
    assert len(_labels(_shown("fav:p:0"))) == 1
    assert _fresh(db, fav).removed_at is not None


def test_o11b_the_way_back_on_a_hidden_dish_brings_the_dish_back(db, owner):
    food, fav = _starred(db, owner)
    tap(f"fav:r:{fav.id}:0")
    tap(f"dh:x:{food.id}")
    assert _fresh(db, food).hidden_at is not None
    tap(f"fav:b:{fav.id}:0")
    assert _fresh(db, food).hidden_at is None


def test_old_labels_are_gone(db, owner):
    _, fav = _starred(db, owner)
    screens = [
        _shown("fav:p:0"),
        _shown(f"fav:o:{fav.id}:0"),
        _shown(f"fav:l:{fav.id}:n"),
        _shown(f"fav:r:{fav.id}:0"),
    ]
    labels = str([_labels(screen) for screen in screens])
    for old in ("⬅️ Назад", "⬅️ К избранному", "✖️ Убрать", "Завтрак", "Сейчас"):
        assert old not in labels, old
    assert favorites.CALLBACK_PREFIX == "fav:"


# --- O12 the whole meal -------------------------------------------------------------


def _two_dishes(db, owner) -> Meal:
    rice = _dish(db, owner, "рис")
    meal, _ = _eat(db, owner, rice, base_calories=200.0)
    crud_meal_item.add(db, meal_id=meal.id, name="курица", base_calories=300.0)
    meal.description = "рис и курица"
    meal.calories = 500.0
    db.commit()
    return meal


def test_o12_a_whole_meal_is_a_star_on_a_dish_with_a_portion_of_no_weight(db, owner):
    meal = _two_dishes(db, owner)
    wire = tap(f"ml:fm:{meal.id}:1:r")
    assert _toast(wire) == "Весь приём в избранном"
    shown = wire.edits()[-1]
    assert "Весь приём в избранном." in shown["text"]
    assert _on(shown, "✓ ⭐ Весь приём") == f"ml:fm:{meal.id}:0:r"
    fav = db.scalar(select(FavoriteMeal).where(FavoriteMeal.user_id == owner.id))
    assert fav.name == "рис и курица" and fav.usual_grams is None
    assert fav.calories == 500.0
    food = db.get(PersonalFood, fav.personal_food_id)
    assert food.canonical_name == "рис и курица"
    assert food.numbers_basis == "per_portion"

    off = tap(f"ml:fm:{meal.id}:0:r")
    assert _toast(off) == "Убрано из избранного"
    assert _on(off.edits()[-1], "⭐ Весь приём")


# --- a star follows its dish --------------------------------------------------------


def _rename(food, name: str) -> None:
    tap(f"dh:rn:{food.id}")
    _typed_reply(name)


def test_a_renamed_dishs_star_goes_by_the_new_name(db, owner, reader):
    food, fav = _starred(db, owner, "омлет")
    _rename(food, "омлет с сыром")
    assert _labels(_shown("fav:p:0"))[0][0] == "Омлет с сыром"
    assert _shown(f"fav:o:{fav.id}:0")["text"].startswith("<b>⭐ Омлет с сыром</b>")
    assert _shown(f"fav:l:{fav.id}:n")["text"] == "Записано: омлет с сыром."
    meal = _newest(db, owner)
    assert meal.description == "омлет с сыром"
    item = crud_meal_item.live(db, meal.id)[0]
    assert item.name == "омлет с сыром" and item.personal_food_id == food.id


def test_a_renamed_dish_keeps_its_star_on_a_meals_dish_screen(db, owner):
    food, _ = _starred(db, owner, "омлет")
    _rename(food, "омлет с сыром")
    _, item = _eat(db, owner, _fresh(db, food), base_calories=100.0)
    assert item.name == "омлет с сыром"
    shown = _shown(f"ml:i:{item.id}:r")
    assert _on(shown, "✓ ⭐ В избранном") == f"ml:fi:{item.id}:0:r"
    # Taking it off finds that star, by its dish.
    tap(f"ml:fi:{item.id}:0:r")
    assert _labels(_shown("fav:p:0")) == []


def test_a_renamed_stars_groups_go_by_the_name_its_item_holds(db, owner):
    food, fav = _starred(db, owner, "омлет", food_groups=["anchor"])
    _rename(food, "яичница")
    tap(f"fav:l:{fav.id}:n")
    meal = _newest(db, owner)
    assert meal_items._tag_rows(meal)["яичница"] == ["anchor"]
    assert meal_items._tagged(meal)["food_groups"] == ["anchor"]


def test_a_whole_meal_keeps_its_tick_after_its_dish_is_renamed(db, owner):
    meal = _two_dishes(db, owner)
    tap(f"ml:fm:{meal.id}:1:r")
    # The row's own words and its source meal would both still say it: another meal
    # under the old words finds it only through the dish.
    fav = db.scalar(select(FavoriteMeal).where(FavoriteMeal.user_id == owner.id))
    fav.name = "другие слова"
    db.commit()
    other = _two_dishes(db, owner)
    assert _on(_shown(f"ml:mo:{other.id}:r"), "✓ ⭐ Весь приём")


def test_a_renamed_dish_keeps_its_star_in_the_picker(db, owner):
    food, _ = _starred(db, owner, "омлет")
    other = _dish(db, owner, "овсянка")
    meal, _ = _eat(db, owner, other)
    _rename(food, "яичница")
    shown = _shown(f"ml:pk:{crud_meal_item.live(db, meal.id)[0].id}:r:r:0")
    assert "⭐ яичница" in str(_labels(shown))


def test_saving_a_whole_meal_under_a_star_of_an_old_name_makes_no_second_one(db, owner):
    food, fav = _starred(db, owner, "омлет")
    _rename(food, "яичница")
    meal = Meal(user_id=owner.id, description="омлет", meal_time=meal_time())
    db.add(meal)
    db.commit()
    again, created = favorites.save_from_meal(db, meal, "омлет")
    assert not created and again.id == fav.id


def test_a_merged_star_shows_and_logs_the_target(db, owner, reader):
    source, fav = _starred(db, owner, "омлетик")
    target = _dish(db, owner, "омлет", **_per_100(), usual_grams=200.0)
    tap(f"dh:j:{source.id}:{target.id}")
    assert _labels(_shown("fav:p:0"))[0][0] == "Омлет"
    assert _on(_shown(f"fav:o:{fav.id}:0"), "🍲 Всё о блюде") == (
        f"dh:o:{target.id}:f{fav.id}"
    )
    assert _shown(f"fav:l:{fav.id}:n")["text"] == "Записано: омлет."
    item = crud_meal_item.live(db, _newest(db, owner).id)[0]
    assert item.personal_food_id == target.id and item.name == "омлет"


def test_a_star_of_a_merge_undone_is_on_its_own_dish_again(db, owner):
    source, fav = _starred(db, owner, "омлетик")
    target = _dish(db, owner, "омлет")
    joined = _shown(f"dh:j:{source.id}:{target.id}")
    tap(_on(joined, "↩️ Вернуть"))
    assert _labels(_shown("fav:p:0"))[0][0] == "Омлетик"
    assert _on(_shown(f"fav:o:{fav.id}:0"), "🍲 Всё о блюде").startswith(
        f"dh:o:{source.id}:"
    )


def test_a_hide_undone_makes_no_second_star_next_to_a_new_one(db, owner):
    food, fav = _starred(db, owner)
    hidden = _shown(f"dh:x:{food.id}")
    tap(f"dh:fv:{food.id}:1")  # a star asks for the dish: it comes back
    assert _fresh(db, food).hidden_at is None
    # Hidden again by another tap, the first event's button is a stale one. The second
    # event is the one that restores, and only what it took away.
    second = _shown(f"dh:x:{food.id}")
    tap(_on(second, "↩️ Вернуть"))
    assert _fresh(db, food).hidden_at is None
    assert len(_labels(_shown("fav:p:0"))) == 1
    tap(_on(hidden, "↩️ Вернуть"))
    assert len(_labels(_shown("fav:p:0"))) == 1
    assert _fresh(db, fav).removed_at is not None


def test_a_hide_undone_with_a_new_star_keeps_one_star_and_control(db, owner):
    food, fav = _starred(db, owner)
    hidden = _shown(f"dh:x:{food.id}")
    # A control first: with no new star the hide's undo gives this very star back.
    tap(_on(hidden, "↩️ Вернуть"))
    assert _fresh(db, fav).removed_at is None
    # A star made while hidden, then hidden by hand on the row: the old star must not
    # come back beside it.
    hidden = _shown(f"dh:x:{food.id}")
    event = _events(db, "food_hide")[-1]
    new = FavoriteMeal(
        user_id=owner.id, name="омлет с сыром", personal_food_id=food.id, calories=1.0
    )
    db.add(new)
    db.commit()
    tap(f"dh:u:{event.id}")
    active = db.scalars(
        select(FavoriteMeal).where(
            FavoriteMeal.personal_food_id == food.id,
            FavoriteMeal.removed_at.is_(None),
        )
    ).all()
    db.expire_all()
    assert [star.id for star in active] == [new.id]


# --- an old name in the free match and in the search --------------------------------


def test_a_merged_name_alone_or_among_others_is_free_and_writes_the_target(
    db, owner, reader
):
    source = _dish(db, owner, "омлетик")
    target = _dish(db, owner, "омлет", **_per_100(), usual_grams=200.0)
    soup = _dish(db, owner, "суп", **_per_100(), usual_grams=300.0)
    tap(f"dh:j:{source.id}:{target.id}")

    send(text("ОМЛЕТИК"))
    alone = crud_meal_item.live(db, _newest(db, owner).id)
    assert [(i.name, i.personal_food_id) for i in alone] == [("омлет", target.id)]

    send(text("омлетик, суп"))
    both = crud_meal_item.live(db, _newest(db, owner).id)
    assert [i.personal_food_id for i in both] == [target.id, soup.id]
    assert reader.text == [] and reader.image == []


def test_a_renamed_dishs_old_name_is_free_and_writes_the_new_one(db, owner, reader):
    food = _dish(db, owner, "омлет", **_per_100(), usual_grams=200.0)
    _rename(food, "яичница")
    send(text("омлет"))
    items = crud_meal_item.live(db, _newest(db, owner).id)
    assert [(i.name, i.personal_food_id) for i in items] == [("яичница", food.id)]
    assert reader.text == []


def test_a_word_that_is_only_part_of_a_name_is_no_free_match(db, owner, reader):
    _dish(db, owner, "омлет с сыром", aliases=["яичница с сыром"])
    send(text("яичница"))
    assert reader.text, "a part of a name is new words, for the model"


def test_the_search_finds_a_dish_by_a_name_merged_into_it(db, owner):
    source = _dish(db, owner, "глазунья")
    target = _dish(db, owner, "яичница")
    tap("dh:f")
    # The control: before the merge the same word finds the source.
    before = _typed_reply("глазун")
    assert _data(before)[0] == [f"dh:o:{source.id}:l0"]
    tap(f"dh:j:{source.id}:{target.id}")
    tap("dh:f")
    after = _typed_reply("глазун")
    assert _data(after)[0] == [f"dh:o:{target.id}:l0"]
    assert len(_data(after)) == 2


def test_the_search_does_not_bring_a_hidden_dish_back_by_an_old_name(db, owner):
    food = _dish(db, owner, "омлет", aliases=["глазунья"])
    tap("dh:f")
    assert _data(_typed_reply("глазун"))[0] == [f"dh:o:{food.id}:l0"]
    tap(f"dh:x:{food.id}")
    tap("dh:f")
    assert "ничего не нашлось" in _typed_reply("глазун")["text"]
