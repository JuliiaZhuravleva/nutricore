"""What the review of slice N found: double taps, a deleted meal's numbers, a star, a
typed weight's way back, a late model reply. Real rows, the real screen function and
the handler the bot registers; the model is a fake, and every guard has its control.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from telegram.ext import ApplicationHandlerStop

from app.crud.crud_favorite_meal import crud_favorite_meal
from app.crud.crud_meal_item import crud_meal_item
from app.models import PersonalFood, RecordEvent
from app.services import meal_card, meal_items, stats_screen
from app.services import telegram as tg
from tests.test_meal_card import (  # noqa: F401 (fixtures)
    DELETED_NOTICE,
    DISH_GONE,
    NOW,
    Session,
    _button,
    _data,
    _dish,
    _meal,
    _one,
    _rows,
    _tap,
    _text_update,
    db,
    owner,
    stranger,
)

ZONE = ZoneInfo("Asia/Tbilisi")
UNDO = "↩️ Вернуть"
REMOVED_TITLE = "<b>🗑 Приём удалён</b>"
SATURDAY = datetime(2026, 10, 31, 10, 0, tzinfo=UTC)  # 14:00 local


def _events(db, kind=None) -> list:
    db.expire_all()
    stmt = select(RecordEvent).order_by(RecordEvent.id)
    if kind:
        stmt = stmt.where(RecordEvent.kind == kind)
    return list(db.scalars(stmt).all())


def _per_100(db, user, name, kcal="150.00", **fields) -> PersonalFood:
    dish = PersonalFood(
        user_id=user.id,
        canonical_name=name,
        numbers_basis="per_100",
        per_100g_calories=Decimal(kcal),
        per_100g_proteins=Decimal("10.00"),
        per_100g_fats=Decimal("5.00"),
        per_100g_carbs=Decimal("10.00"),
        **fields,
    )
    db.add(dish)
    db.commit()
    return dish


# --- rule 4: a double tap ---------------------------------------------------


def test_a_double_tap_on_a_picked_dish_adds_it_once_and_keeps_the_undo(db, owner):
    meal, _rice = _one(db, owner, "рис", 300.0, None)
    _other, bread = _one(db, owner, "хлеб", 200.0, None)
    data = f"ml:pa:{meal.id}:{bread.personal_food_id}:r"
    now = datetime.now(UTC)

    first = _tap(db, data, now=now)
    db.refresh(meal)
    assert meal.calories == 500.0
    undo = _button(first, UNDO)

    second = _tap(db, data, now=now + timedelta(seconds=1))
    assert second.toast == "Уже так"
    db.refresh(meal)
    assert meal.calories == 500.0
    assert len(crud_meal_item.live(db, meal.id)) == 2
    assert len(_events(db, "add")) == 1

    # The undo of the first tap still holds, as it would not after a second row.
    _tap(db, undo)
    db.refresh(meal)
    assert meal.calories == 300.0


def test_the_same_dish_added_after_the_window_or_after_an_undo_is_meant(db, owner):
    meal, _rice = _one(db, owner, "рис", 300.0, None)
    _other, bread = _one(db, owner, "хлеб", 200.0, None)
    data = f"ml:pa:{meal.id}:{bread.personal_food_id}:r"
    now = datetime.now(UTC)

    _tap(db, data, now=now)
    later = _tap(db, data, now=now + timedelta(seconds=10))
    assert later.toast is None
    db.refresh(meal)
    assert meal.calories == 700.0

    # Undone within the window: the same button again is a new wish, not a repeat.
    _tap(db, _button(later, UNDO), now=now + timedelta(seconds=10))
    db.refresh(meal)
    assert meal.calories == 500.0
    meal2, _r = _one(db, owner, "рис", 300.0, None)
    data2 = f"ml:pa:{meal2.id}:{bread.personal_food_id}:r"
    moment = datetime.now(UTC)
    undo = _button(_tap(db, data2, now=moment), UNDO)
    _tap(db, undo, now=moment)
    again = _tap(db, data2, now=moment + timedelta(seconds=1))
    assert again.toast is None
    db.refresh(meal2)
    assert meal2.calories == 500.0


def test_another_dish_within_the_window_is_not_a_repeat(db, owner):
    meal, _rice = _one(db, owner, "рис", 300.0, None)
    _o, bread = _one(db, owner, "хлеб", 200.0, None)
    _o, cheese = _one(db, owner, "сыр", 100.0, None)
    now = datetime.now(UTC)
    _tap(db, f"ml:pa:{meal.id}:{bread.personal_food_id}:r", now=now)
    _tap(db, f"ml:pa:{meal.id}:{cheese.personal_food_id}:r", now=now)
    db.refresh(meal)
    assert meal.calories == 600.0


def test_a_second_tap_on_delete_keeps_the_way_back(db, owner):
    meal, _item = _one(db, owner, "рис", 300.0, None)
    first = _tap(db, f"ml:d:{meal.id}:r")
    assert first.text.startswith(REMOVED_TITLE)

    second = _tap(db, f"ml:d:{meal.id}:r")

    assert second.text == first.text
    assert _data(second) == _data(first)
    assert len(_events(db, "delete")) == 1
    _tap(db, _button(second, UNDO))
    db.refresh(meal)
    assert meal.deleted_at is None and meal.calories == 300.0


def test_a_second_tap_on_removing_the_last_dish_keeps_the_way_back(db, owner):
    meal, item = _one(db, owner, "рис", 300.0, None)
    first = _tap(db, f"ml:x:{item.id}:r")
    assert first.text.startswith(REMOVED_TITLE)

    second = _tap(db, f"ml:x:{item.id}:r")

    assert second.text == first.text and _data(second) == _data(first)
    _tap(db, _button(second, UNDO))
    db.refresh(meal)
    assert meal.deleted_at is None


def test_a_second_tap_on_removing_a_dish_keeps_the_way_back(db, owner):
    meal, rice = _one(db, owner, "рис", 300.0, None)
    meal_items.add_item(db, owner.id, meal.id, name="курица")
    first = _tap(db, f"ml:x:{rice.id}:r")
    assert first.text.startswith("<i>✓ Убрано: рис</i>")

    second = _tap(db, f"ml:x:{rice.id}:r")

    assert second.text == first.text and _data(second) == _data(first)
    assert DISH_GONE not in second.text
    assert len(_events(db, "remove")) == 1
    _tap(db, _button(second, UNDO))
    assert {i.name for i in crud_meal_item.live(db, meal.id)} == {"рис", "курица"}


def test_a_dish_gone_some_other_way_still_says_it_is_gone(db, owner):
    # Control: the notice stays for what cannot be taken back any more.
    meal, rice = _one(db, owner, "рис", 300.0, None)
    meal_items.add_item(db, owner.id, meal.id, name="курица")
    _tap(db, f"ml:x:{rice.id}:r")
    meal_items.add_item(db, owner.id, meal.id, name="хлеб")

    late = _tap(db, f"ml:x:{rice.id}:r")

    assert DISH_GONE in late.text and UNDO not in sum(_rows(late), [])


def test_a_second_tap_on_replace_keeps_the_way_back(db, owner):
    _o, turkey = _one(db, owner, "индейка", 500.0, None)
    meal, item = _one(db, owner, "курица", 200.0, None)
    data = f"ml:pr:{item.id}:{turkey.personal_food_id}:r"
    first = _tap(db, data)
    assert first.text.startswith("<i>✓ Заменено: курица → индейка</i>")

    second = _tap(db, data)

    assert second.text == first.text and _data(second) == _data(first)
    assert len(_events(db, "replace")) == 1


def test_a_second_tap_on_undoing_a_restore_keeps_the_way_back(db, owner):
    meal, _item = _one(db, owner, "рис", 300.0, None)
    deleted = _tap(db, f"ml:d:{meal.id}:r")
    restored = _tap(db, _button(deleted, UNDO))
    db.refresh(meal)
    assert meal.deleted_at is None
    undo_restore = _button(restored, UNDO)

    again = _tap(db, undo_restore)
    assert again.text.startswith(REMOVED_TITLE)
    second = _tap(db, undo_restore)

    assert second.text == again.text and _data(second) == _data(again)
    _tap(db, _button(second, UNDO))
    db.refresh(meal)
    assert meal.deleted_at is None


def test_a_second_tap_on_undo_after_a_delete_opens_the_restored_card(db, owner):
    # Control: N6 «Stale», which the change above must not disturb.
    meal, _item = _one(db, owner, "рис", 300.0, None)
    undo = _button(_tap(db, f"ml:d:{meal.id}:r"), UNDO)
    _tap(db, undo)
    assert _tap(db, undo).text.startswith("<b>🍽 Приём")


def test_a_deleted_meal_with_no_delete_event_is_todays_card(db, owner):
    meal, _item = _one(db, owner, "рис", 300.0, None)
    meal.deleted_at = NOW
    db.commit()
    assert _tap(db, f"ml:d:{meal.id}:r").text.startswith(DELETED_NOTICE)


def test_foreign_delete_of_a_deleted_meal_offers_no_undo(db, owner, stranger):
    meal, _item = _one(db, stranger, "рис", 300.0, None)
    meal_items.delete_meal(db, stranger.id, meal.id)
    screen = _tap(db, f"ml:d:{meal.id}:r")
    assert REMOVED_TITLE not in screen.text and DELETED_NOTICE not in screen.text


# --- the portion of a replacing dish ----------------------------------------


def _chicken_at_300(db, owner):
    meal, item = _one(db, owner, "курица", 200.0, 150.0)
    assert meal_items.set_grams(db, owner.id, item.id, 300.0).status == "done"
    assert item.factor == 2.0
    return meal, item


def test_a_replacing_dish_counts_the_typed_grams_from_its_own_portion(db, owner):
    _o, turkey = _one(db, owner, "индейка", 500.0, 250.0)
    meal, item = _chicken_at_300(db, owner)

    change = meal_items.replace_item(
        db, owner.id, item.id, food_id=turkey.personal_food_id
    )

    new = change.items[0]
    assert new.factor == pytest.approx(1.2) and new.grams == 300.0
    db.refresh(meal)
    assert meal.calories == pytest.approx(600.0)


def test_a_replacing_portion_without_grams_is_taken_as_it_is(db, owner):
    _o, turkey = _one(db, owner, "индейка", 500.0, None)
    meal, item = _chicken_at_300(db, owner)

    change = meal_items.replace_item(
        db, owner.id, item.id, food_id=turkey.personal_food_id
    )

    assert change.items[0].factor == 1 and change.items[0].grams == 300.0
    db.refresh(meal)
    assert meal.calories == 500.0


def test_a_replacing_dish_per_100_and_a_tapped_quantity_still_count(db, owner):
    # Controls: per 100 g from the typed grams, and ×2 with no grams carries over.
    per_100 = _per_100(db, owner, "индейка")
    meal, item = _chicken_at_300(db, owner)
    new = meal_items.replace_item(db, owner.id, item.id, food_id=per_100.id).items[0]
    assert (new.base_calories, new.base_grams, new.factor) == (150.0, 100.0, 3.0)
    db.refresh(meal)
    assert meal.calories == 450.0

    _o, soup = _one(db, owner, "суп", 100.0, 300.0)
    meal2, kept = _one(db, owner, "рис", 200.0, 150.0)
    meal_items.set_quantity(db, owner.id, kept.id, 2.0)
    swapped = meal_items.replace_item(
        db, owner.id, kept.id, food_id=soup.personal_food_id
    ).items[0]
    assert swapped.factor == 2.0
    db.refresh(meal2)
    assert meal2.calories == 200.0


# --- a deleted meal's numbers do not feed a dish ----------------------------


def _deleted_after_a_typed_weight(db, owner):
    """200 kcal at 150 g, typed up to 1500 g, then deleted: 2000 kcal in a bin."""
    meal, item = _one(db, owner, "рис", 200.0, 150.0)
    meal_items.set_grams(db, owner.id, item.id, 1500.0)
    db.refresh(meal)
    assert meal.calories == 2000.0
    dish = db.get(PersonalFood, item.personal_food_id)
    return meal, dish


def test_a_deleted_meal_is_not_the_dishs_last_portion(db, owner):
    first, _item = _one(db, owner, "рис", 200.0, 150.0)
    meal, dish = _deleted_after_a_typed_weight(db, owner)
    assert meal_items.dish_portion(db, dish).numbers["calories"] == 2000.0  # positive

    meal_items.delete_meal(db, owner.id, meal.id)

    portion = meal_items.dish_portion(db, dish)
    assert portion.numbers["calories"] == 200.0 and portion.grams == 150.0
    assert first.deleted_at is None


def test_add_and_replace_do_not_take_a_deleted_meals_numbers(db, owner):
    _kept, _i = _one(db, owner, "рис", 200.0, 150.0)
    gone, dish = _deleted_after_a_typed_weight(db, owner)
    meal_items.delete_meal(db, owner.id, gone.id)

    meal, _soup = _one(db, owner, "суп", 100.0, None)
    added = meal_items.add_item(db, owner.id, meal.id, food_id=dish.id).items[0]
    assert added.base_calories == 200.0

    other, chicken = _one(db, owner, "курица", 100.0, None)
    new = meal_items.replace_item(db, owner.id, chicken.id, food_id=dish.id).items[0]
    assert new.base_calories == 200.0


def test_a_restored_meal_is_a_portion_again(db, owner):
    meal, dish = _deleted_after_a_typed_weight(db, owner)
    event = meal_items.delete_meal(db, owner.id, meal.id).event
    assert meal_items.dish_portion(db, dish).numbers is None
    meal_items.undo(db, owner.id, event.id)
    assert meal_items.dish_portion(db, dish).numbers["calories"] == 2000.0


def test_a_deleted_source_meal_gives_no_numbers_and_no_grams(db, owner):
    source = _meal(
        db, owner, ["борщ"], 900.0, resolution_signals={"portion_grams": 500}
    )
    dish = db.scalars(select(PersonalFood)).one()
    # The source meal's own totals, with its item out of the picture.
    item = crud_meal_item.live(db, source.id)[0]
    item.base_calories = None
    item.numbers_source = "none"
    db.commit()
    assert dish.meal_id == source.id
    assert meal_items.dish_portion(db, dish).numbers["calories"] == 900.0  # positive

    meal_items.delete_meal(db, owner.id, source.id)

    assert meal_items.dish_portion(db, dish).numbers is None
    assert meal_items.dish_portion(db, dish).grams is None


def test_a_deleted_meals_tags_give_a_dish_no_groups(db, owner):
    meal, item = _one(db, owner, "тофу", 100.0, None)
    tags = {
        "food_items": [{"name": "тофу", "groups": ["legumes"]}],
        "food_groups": ["legumes"],
        "food_groups_by_user": False,
    }
    meal.ai_analysis = tags
    meal.nutrients = {**meal.nutrients, **tags}
    db.commit()
    dish = db.get(PersonalFood, item.personal_food_id)
    assert meal_items._groups_of_dish(db, dish.id, "тофу") == ["legumes"]  # positive

    meal_items.delete_meal(db, owner.id, meal.id)

    assert meal_items._groups_of_dish(db, dish.id, "тофу") is None
    saved = meal_items.save_exact(db, owner.id, [dish], meal_time=NOW)
    assert "food_groups" not in saved.nutrients


# --- a dish's star is not the meal's ----------------------------------------


def test_a_dish_star_does_not_read_as_the_whole_meal(db, owner):
    meal = _meal(db, owner, ["рис", "курица"])
    rice = crud_meal_item.live(db, meal.id)[0]

    _tap(db, f"ml:fi:{rice.id}:1:r")
    more = _tap(db, f"ml:mo:{meal.id}:r")
    assert "Весь приём в избранном." not in more.text
    assert _button(more, "⭐ Весь приём") == f"ml:fm:{meal.id}:1:r"

    # Taking the meal's star off leaves the dish's alone.
    assert _tap(db, f"ml:fm:{meal.id}:0:r").toast == "Уже так"
    assert [f.name for f in crud_favorite_meal.list_active(db, owner.id)] == ["рис"]

    # And the meal can have its own beside it.
    on = _tap(db, f"ml:fm:{meal.id}:1:r")
    assert "Весь приём в избранном." in on.text
    names = {f.name for f in crud_favorite_meal.list_active(db, owner.id)}
    assert names == {"рис", "рис, курица"}


def test_a_meal_star_stays_when_the_meal_changes_its_name(db, owner):
    # Control: the source link is how a renamed meal keeps its star.
    meal = _meal(db, owner, ["рис", "курица"])
    _tap(db, f"ml:fm:{meal.id}:1:r")
    meal_items.add_item(db, owner.id, meal.id, name="хлеб")
    db.refresh(meal)
    assert meal.description == "рис, курица, хлеб"
    assert "Весь приём в избранном." in _tap(db, f"ml:mo:{meal.id}:r").text


# --- ×1 returns to what was recorded ----------------------------------------


def test_one_times_returns_a_weight_taken_from_the_dish_to_the_record(db, owner):
    meal, item = _one(db, owner, "рис", 300.0, None)
    dish = db.get(PersonalFood, item.personal_food_id)
    dish.numbers_basis = "per_100"
    dish.per_100g_calories = Decimal("130.00")
    dish.meal_id = None
    db.commit()
    source, factor = item.numbers_source, item.factor

    typed = meal_items.set_grams(db, owner.id, item.id, 200.0)
    assert typed.numbers_changed and item.base_grams == 100.0
    db.refresh(meal)
    assert meal.calories == 260.0

    screen = _tap(db, f"ml:q:{item.id}:1:r")

    assert screen.text.startswith("<i>✓ Как записано</i>")
    db.refresh(item)
    db.refresh(meal)
    assert meal.calories == 300.0
    assert item.base_calories == 300.0 and item.base_grams is None
    assert item.numbers_source == source and item.grams is None
    assert item.factor == factor
    assert _tap(db, f"ml:q:{item.id}:1:r").toast == "Уже так"


def test_a_quantity_after_such_a_weight_counts_from_the_record(db, owner):
    meal, item = _one(db, owner, "рис", 300.0, None)
    dish = db.get(PersonalFood, item.personal_food_id)
    dish.numbers_basis = "per_100"
    dish.per_100g_calories = Decimal("130.00")
    db.commit()
    meal_items.set_grams(db, owner.id, item.id, 200.0)

    meal_items.set_quantity(db, owner.id, item.id, 2.0)

    db.refresh(meal)
    assert meal.calories == 600.0


def test_a_dish_without_a_record_keeps_the_dish_numbers_on_one_times(db, owner):
    # Nothing was recorded, so there is nothing to return to and nothing is wiped.
    meal, _i = _one(db, owner, "суп", 100.0, None)
    dish = _per_100(db, owner, "рис", "130.00")
    item = meal_items.add_item(db, owner.id, meal.id, food_id=dish.id).items[0]
    assert item.numbers_source == "none"
    meal_items.set_grams(db, owner.id, item.id, 200.0)

    meal_items.set_quantity(db, owner.id, item.id, 1.0)

    db.refresh(item)
    assert item.base_calories == 130.0 and item.numbers_source == "dish"


def test_one_times_after_a_scaled_weight_is_the_base_as_before(db, owner):
    # Control: a base with its own grams was never overwritten.
    meal, item = _one(db, owner, "рис", 300.0, 150.0)
    meal_items.set_grams(db, owner.id, item.id, 300.0)
    meal_items.set_quantity(db, owner.id, item.id, 1.0)
    db.refresh(meal)
    assert meal.calories == 300.0 and item.base_grams == 150.0


# --- typed grams with a unit ------------------------------------------------


@pytest.mark.parametrize(
    "text", ["150 гр", "150 грамм", "150g", "150 г.", "150 гр.", "150 граммов", "150 Г"]
)
def test_grams_take_the_units_people_type(text):
    assert meal_card.parse_grams(text) == 150.0


@pytest.mark.parametrize(
    "text", ["150 грамм риса", "150 кг", "150 kg", "0 гр", "5001 г", "сто грамм"]
)
def test_grams_still_refuse_what_is_not_an_answer(text):
    assert meal_card.parse_grams(text) is None


# --- a meal without numbers never reads as 0 --------------------------------


def _bare(db, owner, name="рис"):
    meal = _meal(db, owner, [name], None, numbers_basis="none")
    return meal


def test_a_day_of_only_uncounted_meals_never_reads_0(db, owner):
    meal = _bare(db, owner)
    assert meal.calories is None
    screen = stats_screen.day_screen(
        [meal], date(2026, 10, 26), stats_screen.TODAY, ZONE, opened=True
    )
    assert "0 ккал" not in screen.text and "· Б 0" not in screen.text
    assert "не посчитано" in screen.text
    assert "Не посчитано приёмов: 1" in screen.text


def test_a_day_with_a_counted_meal_says_how_many_are_not(db, owner):
    counted, _i = _one(db, owner, "курица", 700.0, None)
    bare = _bare(db, owner)
    screen = stats_screen.day_screen(
        [counted, bare], date(2026, 10, 26), stats_screen.TODAY, ZONE, opened=True
    )
    assert "≈ 700 ккал" in screen.text  # control: the counted one reads as before
    assert "рис — не посчитано" in screen.text
    assert "≈ 0 ккал" not in screen.text
    assert "Не посчитано приёмов: 1" in screen.text


def test_a_day_of_counted_meals_has_no_such_line(db, owner):
    counted, _i = _one(db, owner, "курица", 700.0, None)
    screen = stats_screen.day_screen(
        [counted], date(2026, 10, 26), stats_screen.TODAY, ZONE, opened=True
    )
    assert "посчитано" not in screen.text


def test_a_counted_zero_still_reads_0(db, owner):
    # Control: only the unknown changed. Water really has none.
    water, _i = _one(db, owner, "вода", 0.0, None)
    screen = stats_screen.day_screen(
        [water], date(2026, 10, 26), stats_screen.TODAY, ZONE, opened=True
    )
    assert "≈ 0 ккал" in screen.text and "посчитано" not in screen.text


def test_the_week_counts_only_what_was_counted(db, owner):
    counted, _i = _one(db, owner, "курица", 700.0, None)
    bare = _bare(db, owner)
    monday, tuesday = date(2026, 10, 26), date(2026, 10, 27)
    days = [(monday, [counted]), (tuesday, [bare])]
    text = stats_screen._week_numbers(days)
    assert "В среднем за день с записями: ≈ 700 ккал" in text
    assert "не посчитано" in text and "Не посчитано приёмов: 1" in text
    only_bare = stats_screen._week_numbers([(tuesday, [bare])])
    assert "0 ккал" not in only_bare and "Не посчитано приёмов: 1" in only_bare


# --- N4: a typed unknown name gets its numbers in the background ------------


REPLY = json.dumps(
    {
        "calories": 400.0,
        "protein": 20.0,
        "fats": 10.0,
        "carbs": 30.0,
        "portion": "1 порция (200 г)",
        "portion_grams": 200.0,
        "foods": ["индейка"],
    }
)


@pytest.fixture
def model(monkeypatch):
    service = SimpleNamespace(
        analyze_food_entry=AsyncMock(return_value=REPLY), model_for=lambda _task: "fake"
    )
    monkeypatch.setattr(tg.telegram_service, "openai_service", service)
    monkeypatch.setattr(
        "app.services.ai_call_log_service.record_ai_call", lambda **_: None
    )
    return service


def _answer(text):
    """The slot's answer through the handler, then whatever it left running."""
    update, message = _text_update(text)
    context = SimpleNamespace(
        bot=SimpleNamespace(edit_message_reply_markup=AsyncMock())
    )

    async def go():
        with pytest.raises(ApplicationHandlerStop):
            await tg.meal_slot_text(update, context)
        await asyncio.gather(*tg._typed_fills, return_exceptions=True)

    asyncio.run(go())
    return message


def _dish_screen(db, item):
    return _tap(db, f"ml:i:{item.id}:r").text


def test_a_typed_unknown_name_gets_numbers_with_no_new_message(db, owner, model):
    meal, _item = _one(db, owner)
    _tap(db, f"ml:pk:{meal.id}:a:r:0", now=datetime.now(UTC))

    message = _answer("индейка")

    assert len(message.replies) == 1
    model.analyze_food_entry.assert_awaited_once_with("индейка")
    db.expire_all()
    item = crud_meal_item.live(db, meal.id)[1]
    assert item.numbers_source == "model"
    assert item.base_calories == 400.0 and item.base_grams == 200.0
    assert meal.calories == 730.0
    assert meal.nutrients["calories"] == 730.0
    assert "Оценка по тексту" in _dish_screen(db, item)
    # The add's own undo still takes the dish away.
    add = _events(db, "add")[-1]
    assert meal_items.undo(db, owner.id, add.id).status == "done"
    db.refresh(meal)
    assert meal.calories == 330.0 and len(crud_meal_item.live(db, meal.id)) == 1


def test_a_typed_replacement_gets_numbers_too_with_the_grams_kept(db, owner, model):
    meal, item = _one(db, owner, "курица", 200.0, 150.0)
    meal_items.set_grams(db, owner.id, item.id, 300.0)
    _tap(db, f"ml:pk:{item.id}:r:r:0", now=datetime.now(UTC))

    _answer("индейка")

    db.expire_all()
    new = crud_meal_item.live(db, meal.id)[0]
    assert new.numbers_source == "model" and new.grams == 300.0
    assert new.factor == 1.5
    assert meal.calories == 600.0


def test_a_failed_model_leaves_the_name_with_the_words(db, owner, model):
    model.analyze_food_entry.side_effect = RuntimeError("no model")
    meal, _item = _one(db, owner)
    _tap(db, f"ml:pk:{meal.id}:a:r:0", now=datetime.now(UTC))

    message = _answer("индейка")

    assert len(message.replies) == 1
    db.expire_all()
    item = crud_meal_item.live(db, meal.id)[1]
    assert item.numbers_source == "none" and item.base_calories is None
    assert "Цифры не посчитаны." in _dish_screen(db, item)
    assert meal.calories == 330.0


def test_a_known_name_is_free_even_when_it_has_no_numbers(db, owner, model):
    meal, _item = _one(db, owner)
    _dish(db, owner, "хлеб")
    _tap(db, f"ml:pk:{meal.id}:a:r:0", now=datetime.now(UTC))

    _answer("хлеб")

    model.analyze_food_entry.assert_not_awaited()
    db.expire_all()
    assert crud_meal_item.live(db, meal.id)[1].numbers_source == "none"


def test_a_typed_weight_calls_no_model(db, owner, model):
    _meal_row, item = _one(db, owner)
    _tap(db, f"ml:g:{item.id}:r", now=datetime.now(UTC))
    _answer("150")
    model.analyze_food_entry.assert_not_awaited()


def _typed_fill(db, owner, name="индейка"):
    meal, _item = _one(db, owner)
    change = meal_items.add_item(db, owner.id, meal.id, name=name)
    return meal, change.items[0], change


NUMBERS = {"calories": 400.0, "protein": 20.0, "fats": 10.0, "carbs": 30.0}


def test_a_late_reply_does_not_land_on_a_meal_that_moved_on(db, owner):
    meal, item, change = _typed_fill(db, owner)
    rev, event = meal.rev, change.event.id
    meal_items.add_item(db, owner.id, meal.id, name="хлеб")
    calories = meal.calories

    assert not meal_items.fill_numbers(db, item.id, rev, event, NUMBERS)

    db.refresh(item)
    db.refresh(meal)
    assert item.numbers_source == "none" and meal.calories == calories


def test_a_late_reply_does_not_land_on_a_deleted_meal(db, owner):
    meal, item, change = _typed_fill(db, owner)
    rev, event = meal.rev, change.event.id
    meal_items.delete_meal(db, owner.id, meal.id)
    assert not meal_items.fill_numbers(db, item.id, rev, event, NUMBERS)
    db.refresh(item)
    assert item.numbers_source == "none"


def test_a_reply_with_no_calories_counts_nothing(db, owner):
    meal, item, change = _typed_fill(db, owner)
    empty = {**NUMBERS, "calories": None}
    assert not meal_items.fill_numbers(db, item.id, meal.rev, change.event.id, empty)
    db.refresh(item)
    assert item.numbers_source == "none"


def test_a_filled_reply_moves_the_rev_and_the_export_sees_it(db, owner):
    meal, item, change = _typed_fill(db, owner)
    rev = meal.rev
    assert meal_items.fill_numbers(db, item.id, rev, change.event.id, NUMBERS)
    db.refresh(meal)
    assert meal.rev == rev + 1 and meal.updated_at is not None


# --- the day-only chip claims no clock --------------------------------------


def _day_only(db, owner):
    meal, _item = _one(db, owner)
    _tap(db, f"ml:t:{meal.id}:t", now=SATURDAY)
    return meal, _tap(
        db,
        _button(_tap(db, f"ml:t:{meal.id}:t", now=SATURDAY), "Не помню"),
        now=SATURDAY,
    )


def test_yesterday_on_a_day_only_meal_stays_without_a_time(db, owner):
    meal, screen = _day_only(db, owner)
    chip = _button(screen, "Вчера, 30 окт")

    moved = _tap(db, chip, now=SATURDAY)

    db.refresh(meal)
    assert meal.time_precision == "day_only"
    assert meal_card.meal_day(meal, owner) == date(2026, 10, 30)
    stamp = meal.meal_time.replace(tzinfo=UTC).astimezone(ZONE)
    assert stamp.strftime("%H:%M") == "12:00"
    assert moved.text.split("\n")[0] == (
        "<i>✓ Время не указано. Приём теперь в пт 30 октября, без времени.</i>"
    )
    assert "≈" not in moved.text
    assert "Записано на: пт 30 октября, без времени." in moved.text
    # The same button again: the target state, no second event.
    events = len(_events(db, "time"))
    assert _tap(db, chip, now=SATURDAY).toast == "Уже так"
    assert len(_events(db, "time")) == events
    # And back.
    _tap(db, _button(moved, UNDO), now=SATURDAY)
    db.refresh(meal)
    assert meal_card.meal_day(meal, owner) == date(2026, 10, 26)
    assert meal.time_precision == "day_only"


def test_yesterday_on_a_meal_with_a_time_is_still_approximate(db, owner):
    # Control: the clock it counts from is real.
    meal, _item = _one(db, owner)
    screen = _tap(db, f"ml:t:{meal.id}:t", now=SATURDAY)
    moved = _tap(db, _button(screen, "Вчера, 30 окт"), now=SATURDAY)
    db.refresh(meal)
    assert meal.time_precision == "approx"
    assert "≈" in moved.text


def test_the_day_only_chip_is_a_known_shape_and_fits(db, owner):
    meal, screen = _day_only(db, owner)
    chip = _button(screen, "Вчера, 30 окт")
    assert ":y:" in chip and meal_card.is_known_shape(chip)
    assert len(chip.encode()) <= 64
