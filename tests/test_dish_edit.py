"""Editing a dish: its usual portion and groups, and how far any edit reaches.

On real rows, through the real application at the wire. What a person corrects on a dish
reaches the meals to come, a ⭐ of it included, and leaves every past meal as it was.
``reader`` stands in for the model; the learning task gets a fake embedding client.
"""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import select

from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.crud.crud_meal_item import crud_meal_item
from app.models import FavoriteMeal, Meal, MealItem, PersonalFood, RecordEvent
from app.services import meal_card, meal_items
from celery_app.tasks.personal_food import embed_and_save_personal_food
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
    TUE,
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

BIG = 2**31 - 1
ONE_ITEM = {"base_calories": 360.0, "base_grams": 200.0, "numbers_source": "dish"}
NOTICE = "<i>✓ Для будущих приёмов. Прошлые остаются как были.</i>"
GROUPS_SEEN = "Это видно только на карточках дня и недели."


def _fresh(db, dish) -> PersonalFood:  # noqa: F811
    db.expire_all()
    return db.get(PersonalFood, dish.id)


def _newest(db, owner) -> Meal:  # noqa: F811
    return max(_meals(db, owner), key=lambda meal: meal.id)


def _row_of(obj) -> dict:
    return {
        column.key: getattr(obj, column.key)
        for column in sa_inspect(type(obj)).mapper.column_attrs
    }


def _past(db, meals) -> list:  # noqa: F811
    """Every column of the meals and of all their items."""
    db.expire_all()
    rows = []
    for meal_id in meals:
        rows.append(_row_of(db.get(Meal, meal_id)))
        items = db.scalars(select(MealItem).where(MealItem.meal_id == meal_id))
        rows.extend(_row_of(item) for item in items)
    return rows


def _events(db, kind) -> list:  # noqa: F811
    db.expire_all()
    return list(db.scalars(select(RecordEvent).where(RecordEvent.kind == kind)))


def _portion(dish, value: str) -> dict:
    tap(f"dh:pg:{dish.id}")
    return _typed_reply(value)


def _tagged_meal(db, owner, dish, groups, when=TUE) -> Meal:  # noqa: F811
    meal, _ = _eat(db, owner, dish, when, **ONE_ITEM)
    rows = [{"name": dish.canonical_name, "groups": groups}]
    tags = {"food_items": rows, "food_groups": groups, "food_groups_by_user": False}
    meal.ai_analysis = dict(tags)
    meal.nutrients = dict(tags)
    db.commit()
    return meal


# --- O14 leads to both ------------------------------------------------------------


def test_o14_leads_to_the_portion_and_the_groups_and_fits_at_the_longest_ids(db, owner):
    dish = _dish(db, owner, "омлет с сыром")
    origin = f"i{BIG}-h{BIG}"
    shown = _shown(f"dh:mo:{dish.id}:{origin}")
    _fits(shown)
    assert _on(shown, "⚖️ Порция") == f"dh:pg:{dish.id}:{origin}"
    assert _on(shown, "🏷 Группы") == f"dh:gr:{dish.id}:{origin}"


# --- the usual portion ------------------------------------------------------------


def test_the_portion_asks_and_opening_writes_nothing(db, owner):
    dish = _dish(db, owner, "омлет с сыром")
    asked = _shown(f"dh:pg:{dish.id}")
    assert asked["text"] == (
        "<b>⚖️ Порция: омлет с сыром</b>\nНапиши граммы числом, например 200."
    )
    assert _labels(asked) == [["⬅️ К блюду"]]
    assert _on(asked, "⬅️ К блюду") == f"dh:o:{dish.id}:l0"
    fresh = _fresh(db, dish)
    assert fresh.usual_grams is None
    assert fresh.usual_grams_by_user is False
    assert meal_card.has_slot(db, OWNER) is True


def test_a_typed_portion_stands_on_the_card_and_serves_only_future_meals(
    db, owner, reader
):
    dish = _dish(db, owner, "омлет с сыром", **_per_100())
    meal, item = _eat(db, owner, dish, TUE, **ONE_ITEM)
    past = _past(db, [meal.id])

    shown = _portion(dish, "300")
    assert shown["text"].startswith(f"{NOTICE}\n\n<b>🍲 Омлет с сыром</b>")
    assert "Обычная порция: 300 г." in shown["text"]
    assert "↩️ Вернуть" in str(_labels(shown))
    _fits(shown)
    fresh = _fresh(db, dish)
    assert (fresh.usual_grams, fresh.usual_grams_by_user) == (300.0, True)
    event = _events(db, "portion")[0]
    assert event.food_id == dish.id
    assert event.before == {"usual_grams": None, "usual_grams_by_user": False}
    assert event.after == {"usual_grams": 300.0, "usual_grams_by_user": True}

    assert _past(db, [meal.id]) == past
    send(text("омлет с сыром"))
    newest = _newest(db, owner)
    assert newest.id != meal.id
    added = crud_meal_item.live(db, newest.id)[0]
    assert added.base_grams == 300.0
    assert added.base_calories == pytest.approx(540.0)
    assert reader.text == []


@pytest.mark.parametrize(
    "typed, grams",
    [("200", 200.0), ("150,5", 150.5), ("200 г", 200.0), ("5000", 5000.0)],
)
def test_the_portion_takes_grams_as_a_meal_does(db, owner, typed, grams):
    dish = _dish(db, owner, "омлет")
    _portion(dish, typed)
    assert _fresh(db, dish).usual_grams == grams


@pytest.mark.parametrize(
    "typed", ["суп с курицей", "0", "5001", "200 300", "150 10 8 2"]
)
def test_the_portion_a_wait_never_eats_food(db, owner, reader, typed):
    dish = _dish(db, owner, "омлет")
    tap(f"dh:pg:{dish.id}")
    send(text(typed))
    assert meal_card.has_slot(db, OWNER) is False
    assert _fresh(db, dish).usual_grams is None
    assert _events(db, "portion") == []
    assert len(_meals(db, owner)) == 1  # the words went down the router: a meal


def test_the_same_portion_says_so_and_writes_nothing(db, owner):
    dish = _dish(db, owner, "омлет")
    _portion(dish, "200")
    assert len(_events(db, "portion")) == 1  # known-positive: a new value writes
    shown = _portion(dish, "200")
    assert shown["text"].startswith("<i>Уже так</i>")
    assert len(_events(db, "portion")) == 1


def test_a_portion_scales_the_last_weighed_numbers(db, owner, reader):
    """No numbers of its own: the last portion's grams say what its numbers were for."""
    dish = _dish(db, owner, "рагу", numbers_basis="none")
    _eat(db, owner, dish, TUE, **ONE_ITEM)
    _portion(dish, "100")
    send(text("рагу"))
    added = crud_meal_item.live(db, _newest(db, owner).id)[0]
    assert (added.base_grams, added.base_calories) == (100.0, pytest.approx(180.0))


def test_a_portion_with_no_weight_leaves_the_numbers(db, owner, reader):
    dish = _dish(db, owner, "рагу", numbers_basis="none")
    _eat(db, owner, dish, TUE, base_calories=360.0, numbers_source="dish")
    _portion(dish, "250")
    send(text("рагу"))
    added = crud_meal_item.live(db, _newest(db, owner).id)[0]
    assert (added.base_grams, added.base_calories) == (250.0, 360.0)


def test_the_portion_undo_restores_and_a_later_edit_refuses_it(db, owner):
    dish = _dish(db, owner, "омлет")
    undo = _on(_portion(dish, "200"), "↩️ Вернуть")
    back = _shown(undo)
    assert back["text"].startswith("<i>✓ Возвращено</i>")
    assert "Обычная порция" not in back["text"]
    fresh = _fresh(db, dish)
    assert (fresh.usual_grams, fresh.usual_grams_by_user) == (None, False)

    undo_of_undo = _on(back, "↩️ Вернуть")
    assert _toast(tap(undo)) == meal_items.SAME_TOAST  # a second tap
    _portion(dish, "300")
    refused = _shown(undo_of_undo)
    assert refused["text"].startswith(meal_items.UNDO_REFUSED)
    assert _fresh(db, dish).usual_grams == 300.0


def test_undo_of_undo_brings_the_portion_back(db, owner):
    dish = _dish(db, owner, "омлет")
    back = _shown(_on(_portion(dish, "200"), "↩️ Вернуть"))
    again = _shown(_on(back, "↩️ Вернуть"))
    assert "Обычная порция: 200 г." in again["text"]
    assert _fresh(db, dish).usual_grams_by_user is True


def test_the_portion_and_the_groups_stand_off_a_hidden_or_a_merged_dish(db, owner):
    hidden = _dish(db, owner, "омлет", hidden_at=datetime(2026, 10, 1, tzinfo=UTC))
    target = _dish(db, owner, "омлет с сыром")
    merged = _dish(db, owner, "омлетик", merged_into_id=target.id)
    for verb in ("pg", "gr"):
        assert _shown(f"dh:{verb}:{hidden.id}")["text"].startswith("<b>🍲 Омлет</b>")
        assert "объединено" in _shown(f"dh:{verb}:{merged.id}")["text"]
    assert meal_card.has_slot(db, OWNER) is False
    # Known-positive: the same verbs ask on a dish standing in the lists.
    assert "Порция" in _shown(f"dh:pg:{target.id}")["text"]


def test_a_stranger_edits_nothing_of_a_dish(db, owner, stranger):
    theirs = PersonalFood(user_id=stranger.id, canonical_name="чужое")
    db.add(theirs)
    db.commit()
    for data in (
        f"dh:pg:{theirs.id}",
        f"dh:gr:{theirs.id}",
        f"dh:gt:{theirs.id}:veg:1",
    ):
        assert "чужое" not in _shown(data)["text"].casefold()
    fresh = _fresh(db, theirs)
    assert fresh.food_groups is None
    assert meal_card.has_slot(db, OWNER) is False
    assert _events(db, "groups") == []


# --- the groups -------------------------------------------------------------------


def test_the_groups_screen_opens_on_what_the_dish_last_carried_and_writes_nothing(
    db, owner
):
    dish = _dish(db, owner, "омлет с сыром")
    _tagged_meal(db, owner, dish, ["anchor", "calcium"])
    shown = _shown(f"dh:gr:{dish.id}")
    assert shown["text"] == (
        "<b>🏷 Группы: омлет с сыром</b>\n"
        f"{GROUPS_SEEN}\n"
        "Для будущих приёмов. Прошлые остаются как были."
    )
    assert _labels(shown) == [
        ["🫘 Бобовые", "✓ ⚓ Опора", "🥦 Овощи"],
        ["🍎 Фрукты", "🌾 Злаки", "🥜 Орехи"],
        ["✓ 🥛 Кальций", "🌰 Омега-3"],
        ["⬅️ К блюду"],
    ]
    _fits(shown)
    assert _on(shown, "🥦 Овощи") == f"dh:gt:{dish.id}:veg:1"
    assert _on(shown, "✓ ⚓ Опора") == f"dh:gt:{dish.id}:anchor:0"
    assert _on(shown, "⬅️ К блюду") == f"dh:o:{dish.id}:l0"
    assert _fresh(db, dish).food_groups is None
    assert _events(db, "groups") == []


def test_a_group_tap_is_the_persons_word_and_a_second_tap_of_it_says_so(db, owner):
    dish = _dish(db, owner, "омлет с сыром")
    _tagged_meal(db, owner, dish, ["anchor"])
    wire = tap(f"dh:gt:{dish.id}:veg:1")
    shown = wire.edits()[-1]
    assert "✓ 🥦 Овощи" in str(_labels(shown))
    assert _on(shown, "✓ 🥦 Овощи") == f"dh:gt:{dish.id}:veg:0"
    assert _fresh(db, dish).food_groups == ["anchor", "veg"]
    event = _events(db, "groups")[0]
    assert (event.food_id, event.meal_id) == (dish.id, None)
    assert event.before == {"food_groups": None}
    assert event.after == {"food_groups": ["anchor", "veg"]}

    again = tap(f"dh:gt:{dish.id}:veg:1")
    assert _toast(again) == meal_items.SAME_TOAST
    assert len(_events(db, "groups")) == 1

    tap(f"dh:gt:{dish.id}:veg:0")
    assert _fresh(db, dish).food_groups == ["anchor"]
    assert len(_events(db, "groups")) == 2


def test_all_groups_off_is_a_word_too_and_a_future_meal_carries_none(db, owner, reader):
    dish = _dish(db, owner, "омлет", **_per_100(), usual_grams=200.0)
    _tagged_meal(db, owner, dish, ["anchor"])
    tap(f"dh:gt:{dish.id}:anchor:0")
    assert _fresh(db, dish).food_groups == []
    send(text("омлет"))
    assert meal_items._tagged(_newest(db, owner)).get("food_groups", []) == []


def test_without_a_word_a_future_meal_still_learns_from_the_last_tagged_one(
    db, owner, reader
):
    """The control: nothing was typed, so the column says nothing."""
    dish = _dish(db, owner, "омлет", **_per_100(), usual_grams=200.0)
    _tagged_meal(db, owner, dish, ["anchor"])
    send(text("омлет"))
    assert meal_items._tagged(_newest(db, owner))["food_groups"] == ["anchor"]


def test_the_groups_reach_future_meals_and_not_past_ones(db, owner, reader):
    dish = _dish(db, owner, "омлет", **_per_100(), usual_grams=200.0)
    meal = _tagged_meal(db, owner, dish, ["anchor"])
    past = _past(db, [meal.id])
    tap(f"dh:gt:{dish.id}:calcium:1")
    assert _past(db, [meal.id]) == past
    send(text("омлет"))
    newest = _newest(db, owner)
    assert newest.id != meal.id
    assert meal_items._tagged(newest)["food_groups"] == ["anchor", "calcium"]
    assert meal_items._tag_rows(newest) == {"омлет": ["anchor", "calcium"]}
    assert reader.text == []


def test_a_group_from_the_dish_log_button_too(db, owner):
    dish = _dish(db, owner, "омлет", **_per_100(), usual_grams=200.0)
    tap(f"dh:gt:{dish.id}:veg:1")
    tap(f"dh:log:{dish.id}")
    assert meal_items._tagged(_newest(db, owner))["food_groups"] == ["veg"]


def test_a_tap_on_a_one_dish_meal_moves_a_word_the_dish_has(db, owner):
    """M14 teaches the dish, or the next meal would go back on what was just said."""
    dish = _dish(db, owner, "омлет")
    meal = _tagged_meal(db, owner, dish, ["anchor"])
    tap(f"dh:gt:{dish.id}:anchor:1")  # same target: nothing written yet
    assert _fresh(db, dish).food_groups is None
    tap(f"dh:gt:{dish.id}:veg:1")
    assert _fresh(db, dish).food_groups == ["anchor", "veg"]
    tap(f"ml:gt:{meal.id}:calcium:r")
    # Only the group tapped: the ordered word, with what the meal never had still in it.
    assert _fresh(db, dish).food_groups == ["anchor", "veg", "calcium"]
    tap(f"ml:gt:{meal.id}:anchor:r")
    assert _fresh(db, dish).food_groups == ["veg", "calcium"]


def test_a_tap_on_a_plate_leaves_the_dishs_word(db, owner):
    dish = _dish(db, owner, "омлет")
    other = _dish(db, owner, "хлеб")
    meal = _tagged_meal(db, owner, dish, ["anchor"])
    crud_meal_item.add(
        db, meal_id=meal.id, name="хлеб", personal_food_id=other.id, position=1
    )
    db.commit()
    tap(f"dh:gt:{dish.id}:veg:1")
    tap(f"ml:gt:{meal.id}:calcium:r")
    db.expire_all()
    assert meal_items._tagged(db.get(Meal, meal.id))["food_groups"] == [
        "anchor",
        "calcium",
    ]  # known-positive: the tap landed
    assert _fresh(db, dish).food_groups == ["anchor", "veg"]


def test_a_tap_on_a_one_dish_meal_gives_no_word_to_a_dish_without_one(db, owner):
    dish = _dish(db, owner, "омлет")
    meal = _tagged_meal(db, owner, dish, ["anchor"])
    tap(f"ml:gt:{meal.id}:calcium:r")
    assert _fresh(db, dish).food_groups is None


def test_an_unknown_group_is_nobodys_word(db, owner):
    dish = _dish(db, owner, "омлет")
    shown = _shown(f"dh:gt:{dish.id}:sugar:1")
    assert "Группы" not in shown["text"]
    assert _fresh(db, dish).food_groups is None
    assert _events(db, "groups") == []
    # Known-positive: a group the table knows goes through the same shape.
    tap(f"dh:gt:{dish.id}:veg:1")
    assert _fresh(db, dish).food_groups == ["veg"]


# --- a ⭐ follows its dish ----------------------------------------------------------


def test_a_star_with_no_correction_logs_what_it_holds(db, owner):
    """The control: a favourite's portion is its own until a person corrects the dish."""
    dish = _dish(db, owner, "омлет", **_per_100())
    fav = _star(db, owner, dish, calories=360.0)
    fav.usual_grams = 200.0
    fav.nutrients = {
        "foods": ["омлет"],
        "food_groups": ["veg"],
        "food_items": [{"name": "омлет", "groups": ["veg"]}],
        "food_groups_by_user": True,
    }
    db.commit()
    tap(f"dh:log:{dish.id}")
    meal = _newest(db, owner)
    assert meal.calories == 360.0
    assert meal_items._tagged(meal)["food_groups"] == ["veg"]
    tap(f"fav:l:{fav.id}:n", 4901)
    assert _newest(db, owner).id != meal.id
    assert _newest(db, owner).calories == 360.0


@pytest.mark.parametrize("button", ["dh:log:{dish}", "fav:l:{fav}:n"])
def test_typed_numbers_reach_the_stars_log(db, owner, button):
    dish = _dish(db, owner, "омлет")
    fav = _star(db, owner, dish, calories=360.0)
    fav.usual_grams = 200.0
    db.commit()
    tap(f"dh:nm:{dish.id}")
    _typed_reply("150 10 8 2")
    tap(button.format(dish=dish.id, fav=fav.id))
    meal = _newest(db, owner)
    assert meal.calories == pytest.approx(300.0)
    assert meal.proteins == pytest.approx(20.0)
    item = crud_meal_item.live(db, meal.id)[0]
    assert (item.base_calories, item.base_grams) == (pytest.approx(300.0), 200.0)
    assert meal.nutrients["calories"] == pytest.approx(300.0)
    db.expire_all()
    assert db.get(FavoriteMeal, fav.id).calories == 360.0  # its own copy stays


def test_an_unlinked_star_finds_its_corrected_dish_by_name(db, owner):
    dish = _dish(db, owner, "омлет")
    fav = _star(db, owner, dish, calories=360.0)
    fav.usual_grams = 200.0
    fav.personal_food_id = None  # a row an old container wrote
    dish.usual_grams = 100.0
    dish.usual_grams_by_user = True
    db.commit()
    tap(f"fav:l:{fav.id}:n")
    assert _newest(db, owner).calories == pytest.approx(180.0)


def test_typing_a_starred_dishs_name_logs_what_its_button_logs(db, owner, reader):
    dish = _dish(db, owner, "омлет")
    fav = _star(db, owner, dish, calories=360.0)
    fav.usual_grams = 200.0
    db.commit()
    tap(f"dh:nm:{dish.id}")
    _typed_reply("150 10 8 2")
    send(text("омлет"))
    assert _newest(db, owner).calories == pytest.approx(300.0)
    assert reader.text == []


def test_typed_numbers_with_a_weightless_last_meal_find_the_stars_weight(db, owner):
    dish = _dish(db, owner, "омлет")
    _eat(db, owner, dish, TUE, base_calories=500.0, numbers_source="dish")
    fav = _star(db, owner, dish, calories=360.0)
    fav.usual_grams = 200.0
    db.commit()
    tap(f"dh:nm:{dish.id}")
    _typed_reply("150 10 8 2")
    portion = meal_items.dish_portion(db, _fresh(db, dish))
    assert portion.grams == 200.0
    assert portion.numbers["calories"] == pytest.approx(300.0)


@pytest.mark.parametrize("button", ["dh:log:{dish}", "fav:l:{fav}:n"])
def test_a_typed_portion_scales_a_weighed_star(db, owner, button):
    dish = _dish(db, owner, "омлет")
    fav = _star(db, owner, dish, calories=360.0)
    fav.usual_grams = 200.0
    db.commit()
    _portion(dish, "100")
    tap(button.format(dish=dish.id, fav=fav.id))
    meal = _newest(db, owner)
    assert meal.calories == pytest.approx(180.0)
    assert crud_meal_item.live(db, meal.id)[0].base_grams == 100.0


@pytest.mark.parametrize("button", ["dh:log:{dish}", "fav:l:{fav}:n"])
def test_a_typed_portion_leaves_a_stars_numbers_with_no_weight(db, owner, button):
    dish = _dish(db, owner, "омлет")
    fav = _star(db, owner, dish, calories=360.0)
    shown = _portion(dish, "250")
    assert "Обычная порция: 250 г." in shown["text"]
    assert "без веса" not in shown["text"]
    tap(button.format(dish=dish.id, fav=fav.id))
    meal = _newest(db, owner)
    assert meal.calories == 360.0
    assert crud_meal_item.live(db, meal.id)[0].base_grams == 250.0


@pytest.mark.parametrize("button", ["dh:log:{dish}", "fav:l:{fav}:n"])
def test_the_groups_word_reaches_the_stars_log(db, owner, button):
    dish = _dish(db, owner, "омлет")
    fav = _star(db, owner, dish, calories=360.0)
    fav.nutrients = {"foods": ["омлет"], "food_groups": ["veg"], "food_items": []}
    db.commit()
    tap(f"dh:gt:{dish.id}:veg:0")
    tap(f"dh:gt:{dish.id}:anchor:1")
    tap(button.format(dish=dish.id, fav=fav.id))
    assert meal_items._tagged(_newest(db, owner))["food_groups"] == ["anchor"]
    assert meal_items._tag_rows(_newest(db, owner)) == {"омлет": ["anchor"]}
    db.expire_all()
    assert db.get(FavoriteMeal, fav.id).nutrients["food_groups"] == ["veg"]


# --- every edit at once -----------------------------------------------------------


def test_no_edit_of_a_dish_reaches_a_past_meal(db, owner, reader):
    dish = _dish(db, owner, "омлет", **_per_100(), numbers_quality="medium")
    first = _tagged_meal(db, owner, dish, ["anchor"])
    second, _ = _eat(db, owner, dish, TUE, name="омлетик", factor=0.5, **ONE_ITEM)
    past = _past(db, [first.id, second.id])

    tap(f"dh:rn:{dish.id}")
    _typed_reply("омлет с сыром")
    tap(f"dh:nm:{dish.id}")
    _typed_reply("150 10 8 2")
    _portion(dish, "300")
    tap(f"dh:gt:{dish.id}:calcium:1")

    # The controls: each of them did change the dish.
    fresh = _fresh(db, dish)
    assert fresh.canonical_name == "омлет с сыром"
    assert fresh.aliases == ["омлет"]
    assert float(fresh.per_100g_calories) == 150.0
    assert fresh.usual_grams == 300.0
    assert fresh.food_groups == ["anchor", "calcium"]
    assert _past(db, [first.id, second.id]) == past

    # The history is the same two meals, under the new title.
    history = _shown(f"dh:h:{dish.id}:0")
    assert history["text"].startswith("<b>🕘 Омлет с сыром</b>")
    assert len(_labels(history)[0]) == 2

    # The old name is an alias: typing it logs this dish, with all it was told.
    send(text("омлет"))
    newest = _newest(db, owner)
    assert newest.id not in (first.id, second.id)
    added = crud_meal_item.live(db, newest.id)[0]
    assert added.personal_food_id == dish.id
    assert added.base_grams == 300.0
    assert added.base_calories == pytest.approx(450.0)
    assert meal_items._tagged(newest)["food_groups"] == ["anchor", "calcium"]
    assert reader.text == []


def _learned_write(Session, owner, name: str) -> None:  # noqa: F811
    """The real write-back task, as a confirmed meal schedules it."""
    fake = MagicMock()
    fake.embed_text = AsyncMock(return_value=[0.0] * 1536)
    fake.client.close = AsyncMock()
    with (
        patch("celery_app.tasks.personal_food.SessionLocal", Session),
        patch("celery_app.tasks.personal_food.OpenAIService", return_value=fake),
    ):
        embed_and_save_personal_food.run(
            user_id=owner.id,
            canonical_name=name,
            per_100g_calories=999.0,
            per_100g_proteins=1.0,
            per_100g_fats=1.0,
            per_100g_carbs=1.0,
        )


def test_the_learning_task_keeps_all_a_person_told_the_dish(db, owner, Session):
    dish = _dish(db, owner, "омлет")
    tap(f"dh:nm:{dish.id}")
    _typed_reply("150 10 8 2")
    _portion(dish, "300")
    tap(f"dh:gt:{dish.id}:veg:1")
    _learned_write(Session, owner, "омлет")
    fresh = _fresh(db, dish)
    assert float(fresh.per_100g_calories) == 150.0
    assert fresh.numbers_quality == "user"
    assert (fresh.usual_grams, fresh.usual_grams_by_user) == (300.0, True)
    assert fresh.food_groups == ["veg"]
    assert fresh.times_used == 2  # known-positive: the task did reach this row


def test_the_learning_task_writes_numbers_nobody_typed(db, owner, Session):
    """The control: the same write on a row nobody corrected does replace them."""
    dish = _dish(db, owner, "омлет", **_per_100())
    _learned_write(Session, owner, "омлет")
    assert float(_fresh(db, dish).per_100g_calories) == 999.0


def test_a_hard_day_shows_no_portion_line(db, owner, hard_day):
    dish = _dish(db, owner, "омлет", usual_grams=200.0, usual_grams_by_user=True)
    assert "Обычная порция" not in _shown(f"dh:o:{dish.id}:l0")["text"]


# --- the migration ----------------------------------------------------------------


def _migration():
    path = (
        Path(__file__).parent.parent
        / "alembic"
        / "versions"
        / "b8c9d0e1f2a3_dish_food_groups.py"
    )
    spec = importlib.util.spec_from_file_location("dish_groups_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_migration_adds_a_nullable_column_and_takes_it_back():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE personal_foods (id INTEGER PRIMARY KEY)"))
        conn.execute(sa.text("INSERT INTO personal_foods (id) VALUES (1)"))
        with Operations.context(MigrationContext.configure(conn)):
            _migration().upgrade()
        columns = {c["name"]: c for c in sa.inspect(conn).get_columns("personal_foods")}
        assert columns["food_groups"]["nullable"] is True
        row = conn.execute(sa.text("SELECT food_groups FROM personal_foods")).one()
        assert row[0] is None  # no backfill: nobody has said anything yet
        with Operations.context(MigrationContext.configure(conn)):
            _migration().downgrade()
        columns = {c["name"] for c in sa.inspect(conn).get_columns("personal_foods")}
        assert columns == {"id"}
    engine.dispose()
