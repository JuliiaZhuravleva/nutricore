"""Where the dish base meets what was already there: a merge against an undo on screen,
a star, a barcode and a near name; a dish that is hidden; a search that is cut.

On real rows, through the real application at the wire. ``reader`` stands in for the
model, so nothing here reaches the OpenAI client. The ANN statement cannot run on
SQLite, so a session that answers it stands in for the database's half of it.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import event as sa_event
from sqlalchemy import select

from app.crud.crud_favorite_meal import crud_favorite_meal
from app.crud.crud_personal_food import crud_personal_food
from app.models import FavoriteMeal, Meal, MealItem, PersonalFood, User
from app.services import capture, explain_screen, favorites, meal_card, meal_items
from app.services import stats_screen as st
from app.services.numbers import SHOW_NUMBERS
from app.services.product_lookup_service import ImageSignals, SavedFoodRAGStrategy
from tests.test_capture_router import (  # noqa: F401 (fixtures)
    OWNER,
    Session,
    db,
    owner,
    photo,
    reader,
    send,
    text,
)
from tests.test_dishes import (  # noqa: F401 (fixtures)
    GONE,
    _data,
    _dish,
    _eat,
    _labels,
    _no_screen_from_another_test,
    _on,
    _per_100,
    _shown,
    _star,
    _toast,
    _typed_reply,
    hard_day,
    tap,
)

SUN = "20261025"


def _fresh(db, row):
    db.expire_all()
    return db.get(type(row), row.id)


def _flat(params) -> list:
    return sum(_labels(params), [])


# --- an undo on screen, and a merge since --------------------------------------------


def _merged(db, owner):
    source = _dish(db, owner, "омлет с сыром", **_per_100())
    target = _dish(db, owner, "омлет", **_per_100())
    return source, target


def test_a_delete_undone_after_a_merge_brings_the_meal_back(db, owner):
    source, target = _merged(db, owner)
    meal, item = _eat(db, owner, source, base_calories=100.0)
    change = meal_items.remove_item(db, owner.id, item.id)
    assert change.status == meal_items.DELETED
    _shown(f"dh:j:{source.id}:{target.id}")
    db.expire_all()
    undone = meal_items.undo(db, owner.id, change.event.id)
    assert undone.status != meal_items.REFUSED
    assert _fresh(db, meal).deleted_at is None
    item = _fresh(db, item)
    assert item.removed_at is None
    assert item.personal_food_id == target.id  # never back on a merged row


def test_a_portion_undone_after_a_merge_goes_back_and_stays_in_the_dish(db, owner):
    source, target = _merged(db, owner)
    _, item = _eat(db, owner, source, base_calories=100.0, factor=1.0)
    change = meal_items.set_quantity(db, owner.id, item.id, 2.0)
    assert change.status == meal_items.DONE
    _shown(f"dh:j:{source.id}:{target.id}")
    db.expire_all()
    assert meal_items.undo(db, owner.id, change.event.id).status == meal_items.DONE
    item = _fresh(db, item)
    assert item.factor == 1.0
    assert item.personal_food_id == target.id


def test_a_replace_undone_after_its_old_dish_merged_takes_the_merged_dish(db, owner):
    source, target = _merged(db, owner)
    other = _dish(db, owner, "кефир")
    _, item = _eat(db, owner, source, base_calories=100.0)
    change = meal_items.replace_item(db, owner.id, item.id, food_id=other.id)
    made = change.items[0]
    _shown(f"dh:j:{source.id}:{target.id}")
    db.expire_all()
    assert meal_items.undo(db, owner.id, change.event.id).status == meal_items.DONE
    item, made = _fresh(db, item), _fresh(db, made)
    assert item.removed_at is None and item.personal_food_id == target.id
    assert made.removed_at is not None


def test_a_merge_undone_after_that_undo_still_takes_the_item_home(db, owner):
    source, target = _merged(db, owner)
    _, item = _eat(db, owner, source, base_calories=100.0)
    change = meal_items.remove_item(db, owner.id, item.id)
    back = _on(_shown(f"dh:j:{source.id}:{target.id}"), "↩️ Вернуть")
    meal_items.undo(db, owner.id, change.event.id)
    _shown(back)
    assert _fresh(db, item).personal_food_id == source.id


def test_control_a_real_change_since_still_refuses_after_a_merge(db, owner):
    source, target = _merged(db, owner)
    _, item = _eat(db, owner, source, base_calories=100.0, factor=1.0)
    first = meal_items.set_quantity(db, owner.id, item.id, 2.0)
    assert meal_items.set_quantity(db, owner.id, item.id, 3.0).status == meal_items.DONE
    _shown(f"dh:j:{source.id}:{target.id}")
    db.expire_all()
    assert meal_items.undo(db, owner.id, first.event.id).status == meal_items.REFUSED
    assert _fresh(db, item).factor == 3.0


def test_control_an_undo_with_no_merge_leaves_the_link_it_had(db, owner):
    dish = _dish(db, owner, "омлет")
    _, item = _eat(db, owner, dish, base_calories=100.0, factor=1.0)
    change = meal_items.set_quantity(db, owner.id, item.id, 2.0)
    assert meal_items.undo(db, owner.id, change.event.id).status == meal_items.DONE
    assert _fresh(db, item).personal_food_id == dish.id


# --- a star restored after its dish merged -------------------------------------------


def _restored_after_merge(db, owner):
    source = _dish(db, owner, "омлет с сыром", **_per_100())
    target = _dish(db, owner, "омлет", **_per_100())
    fav = _star(db, owner, source, calories=999.0)
    undo = _on(tap(f"fav:r:{fav.id}:0").edits()[-1], "↩️ Вернуть")
    _shown(f"dh:j:{source.id}:{target.id}")
    tap(undo)
    assert _fresh(db, fav).removed_at is None
    return source, target, fav


def test_a_restored_star_belongs_to_the_dish_its_own_went_into(db, owner):
    _, target, fav = _restored_after_merge(db, owner)
    assert _fresh(db, fav).personal_food_id == target.id
    assert crud_favorite_meal.active_of_dish(db, target.id).id == fav.id


def test_a_restored_star_comes_off_from_the_card_and_is_not_doubled(db, owner):
    _, target, fav = _restored_after_merge(db, owner)
    assert "✓ ⭐ В избранном" in _flat(_shown(f"dh:o:{target.id}:l0"))
    assert _toast(tap(f"dh:fv:{target.id}:1")) == meal_items.SAME_TOAST
    tap(f"dh:fv:{target.id}:0")
    assert _fresh(db, fav).removed_at is not None
    tap(f"dh:fv:{target.id}:1")
    active = db.scalars(
        select(FavoriteMeal).where(FavoriteMeal.removed_at.is_(None))
    ).all()
    assert len(active) == 1


def test_a_restored_star_lends_its_portion_and_moves_with_the_next_merge(db, owner):
    _, target, fav = _restored_after_merge(db, owner)
    target = _fresh(db, target)
    target.per_100g_calories = None
    db.commit()
    assert meal_items.dish_portion(db, target).numbers["calories"] == 999.0
    next_one = _dish(db, owner, "яичница")
    _shown(f"dh:j:{target.id}:{next_one.id}")
    assert _fresh(db, fav).personal_food_id == next_one.id


def test_control_a_star_restored_with_no_merge_keeps_its_dish(db, owner):
    dish = _dish(db, owner, "омлет")
    fav = _star(db, owner, dish)
    undo = _on(tap(f"fav:r:{fav.id}:0").edits()[-1], "↩️ Вернуть")
    tap(undo)
    fav = _fresh(db, fav)
    assert fav.removed_at is None and fav.personal_food_id == dish.id


# --- a merge undone, and a target in use since ---------------------------------------


def _merge_onto_hidden(db, owner):
    dish = _dish(db, owner, "омлет с сыром")
    target = _dish(db, owner, "омлет", hidden_at=GONE)
    tap(f"dh:rn:{dish.id}")
    merged = _shown(_data(_typed_reply("омлет"))[0][0])
    return dish, target, _on(merged, "↩️ Вернуть")


def test_a_merge_undone_hides_the_target_again_when_nothing_happened(db, owner):
    _, target, undo = _merge_onto_hidden(db, owner)
    assert _fresh(db, target).hidden_at is None
    _shown(undo)
    assert _fresh(db, target).hidden_at is not None  # known-positive


def test_a_target_eaten_since_stays_in_the_list(db, owner):
    _, target, undo = _merge_onto_hidden(db, owner)
    tap(f"dh:log:{target.id}")
    _shown(undo)
    assert _fresh(db, target).hidden_at is None


def test_a_target_typed_since_stays_in_the_list(db, owner, reader):
    _, target, undo = _merge_onto_hidden(db, owner)
    send(text("омлет"))
    _shown(undo)
    assert _fresh(db, target).hidden_at is None


def test_a_target_starred_since_stays_in_the_list(db, owner):
    _, target, undo = _merge_onto_hidden(db, owner)
    tap(f"dh:fv:{target.id}:1")
    _shown(undo)
    assert _fresh(db, target).hidden_at is None


# --- what a label may claim ----------------------------------------------------------


def _typed_with_no_weight(db, owner):
    """Numbers per 100 g, and no grams anywhere. The last meal's estimate is what logs."""
    dish = _dish(
        db,
        owner,
        "омлет",
        **_per_100(numbers_quality="user", numbers_by_user=True),
    )
    _eat(db, owner, dish, base_calories=420.0, numbers_source="model")
    return dish


def test_typed_numbers_with_no_weight_count_a_hundred_grams_and_say_so(db, owner):
    dish = _typed_with_no_weight(db, owner)
    per_100 = float(dish.per_100g_calories)
    shown = tap(f"dh:log:{dish.id}").edits()[-1]
    meal = db.get(Meal, shown_meal_id(db, owner))
    item = meal_items.crud_meal_item.live(db, meal.id)[0]
    assert item.numbers_source == "dish" and item.base_calories == per_100
    assert item.base_grams == meal_items.DEFAULT_GRAMS
    assert (
        meal_card.meal_items.TAG_DISH_UNVERIFIED[1:]
        in _shown(f"ml:i:{item.id}:r")["text"]
    )


def test_a_typed_portion_replaces_the_default_of_a_hundred_grams(db, owner):
    dish = _typed_with_no_weight(db, owner)
    dish.usual_grams, dish.usual_grams_by_user = 250.0, True
    db.commit()
    portion = meal_items.dish_portion(db, dish)
    assert (portion.grams, portion.assumed) == (250.0, False)
    assert meal_items.numbers_checked(dish, portion.grams)


def test_a_typed_hundred_grams_is_checked_and_a_default_one_is_not(db, owner):
    dish = _typed_with_no_weight(db, owner)
    assert not meal_items.numbers_checked(dish, meal_items.DEFAULT_GRAMS)
    dish.usual_grams, dish.usual_grams_by_user = 100.0, True
    assert meal_items.numbers_checked(dish, meal_items.DEFAULT_GRAMS)


def test_an_estimated_dish_keeps_the_plate_it_was_eaten_on(db, owner):
    """Nobody wrote these numbers: a real plate says more than an assumed 100 g."""
    dish = _dish(db, owner, "омлет", **_per_100())
    _eat(db, owner, dish, base_calories=420.0, numbers_source="model")
    portion = meal_items.dish_portion(db, dish)
    assert (portion.numbers["calories"], portion.assumed) == (420.0, False)


def test_an_estimated_dish_with_nothing_else_counts_a_hundred_grams(db, owner):
    dish = _dish(db, owner, "омлет", **_per_100())
    portion = meal_items.dish_portion(db, dish)
    assert (portion.grams, portion.assumed) == (meal_items.DEFAULT_GRAMS, True)


def test_the_dish_card_says_the_hundred_grams_are_assumed(db, owner):
    owner.settings = {**owner.settings, SHOW_NUMBERS: True}
    db.commit()
    dish = _typed_with_no_weight(db, owner)
    card = _shown(f"dh:n:{dish.id}:1:f7")["text"]
    assert "Порция не указана: считаю 100 г." in card
    assert "Обычная порция" not in card


def shown_meal_id(db, owner) -> int:
    db.expire_all()
    return db.scalars(
        select(Meal.id).where(Meal.user_id == owner.id).order_by(Meal.id.desc())
    ).first()


def test_a_portion_with_no_weight_is_not_called_checked_when_typed(db, owner, reader):
    dish = _typed_with_no_weight(db, owner)
    send(text(dish.canonical_name))
    meal = db.get(Meal, shown_meal_id(db, owner))
    assert meal.resolution_signals["numbers_unverified"] is True
    assert meal_items.numbers_tag(meal) == meal_items.TAG_DISH_UNVERIFIED


def test_control_a_checked_dish_with_grams_is_called_checked(db, owner, reader):
    dish = _dish(
        db,
        owner,
        "омлет",
        **_per_100(numbers_quality="user", numbers_by_user=True),
        usual_grams=200.0,
    )
    send(text(dish.canonical_name))
    meal = db.get(Meal, shown_meal_id(db, owner))
    assert meal.resolution_signals["numbers_unverified"] is False
    item = meal_items.crud_meal_item.live(db, meal.id)[0]
    assert meal_card.meal_items.TAG_DISH[1:] in _shown(f"ml:i:{item.id}:r")["text"]


def test_a_star_with_no_weight_keeps_its_mark_where_the_numbers_show(db, owner):
    owner.settings = {**owner.settings, SHOW_NUMBERS: True}
    db.commit()
    dish = _dish(
        db, owner, "омлет", **_per_100(numbers_quality="user", numbers_by_user=True)
    )
    fav = _star(db, owner, dish, calories=360.0)
    assert "≈ 180 ккал" in str(_labels(_shown("fav:p:0")))
    assert "🔥 ≈ 180 ккал" in _shown(f"fav:o:{fav.id}:0")["text"]
    assert "≈ 180 ккал" in tap(f"fav:l:{fav.id}:n").edits()[-1]["text"]


def test_a_log_from_the_card_with_no_weight_keeps_its_mark(db, owner):
    owner.settings = {**owner.settings, SHOW_NUMBERS: True}
    db.commit()
    dish = _typed_with_no_weight(db, owner)
    assert "≈ 180 ккал" in tap(f"dh:log:{dish.id}").edits()[-1]["text"]


def test_control_a_star_at_grams_of_a_checked_dish_has_no_mark(db, owner):
    owner.settings = {**owner.settings, SHOW_NUMBERS: True}
    db.commit()
    dish = _dish(
        db,
        owner,
        "омлет",
        **_per_100(kcal=180, numbers_quality="user", numbers_by_user=True),
    )
    fav = _star(db, owner, dish, calories=360.0)
    fav.usual_grams = 200.0
    db.commit()
    assert "🔥 360 ккал" in _shown(f"fav:o:{fav.id}:0")["text"]
    assert "· 360 ккал" in tap(f"fav:l:{fav.id}:n").edits()[-1]["text"]


# --- an old name two dishes answered to ----------------------------------------------


def test_an_old_name_two_dishes_held_goes_to_the_same_one_in_any_row_order(db, owner):
    """SQLite hands rows back in id order, Postgres in whatever order it likes, so the
    order is asked for and the statement is what is read."""
    first = _dish(db, owner, "борщ", aliases=["суп"])
    second = _dish(db, owner, "солянка", aliases=["суп"])
    seen = []

    def read(conn, cursor, statement, *_):
        seen.append(statement)

    engine = db.get_bind()
    sa_event.listen(engine, "before_cursor_execute", read)
    try:
        names = crud_personal_food.names_of(db, owner.id)
    finally:
        sa_event.remove(engine, "before_cursor_execute", read)
    assert names["суп"].id == second.id != first.id
    assert any(
        "FROM personal_foods" in sql and "ORDER BY personal_foods.id" in sql
        for sql in seen
    ), seen


# --- a search cut to its rows ------------------------------------------------------


def _soups(db, owner, count):
    return [_dish(db, owner, f"суп {i}") for i in range(count)]


def test_a_search_with_more_than_fits_says_so_and_keeps_listening(db, owner):
    _soups(db, owner, 6)
    tap("dh:f")
    shown = _typed_reply("суп")
    assert shown["text"].splitlines()[-1] == "Показаны 4 из 6. Напиши точнее."
    assert len(_labels(shown)) <= 5
    narrowed = _typed_reply("суп 5")
    assert "Показаны" not in narrowed["text"]
    assert _flat(narrowed)[0].startswith("Суп 5")


def test_control_a_search_that_fits_says_nothing_of_it(db, owner):
    _soups(db, owner, 4)
    tap("dh:f")
    shown = _typed_reply("суп")
    assert "Показаны" not in shown["text"]
    assert meal_card.has_slot(db, OWNER) is False


def test_a_merge_search_with_more_than_fits_says_so_and_keeps_listening(db, owner):
    dish = _dish(db, owner, "омлет")
    _soups(db, owner, 6)
    tap(f"dh:jf:{dish.id}")
    shown = _typed_reply("суп")
    assert shown["text"].splitlines()[-1] == "Показаны 4 из 6. Напиши точнее."
    narrowed = _typed_reply("суп 5")
    assert "Показаны" not in narrowed["text"]


# --- a hidden card offers only what answers ------------------------------------------


def test_a_hidden_card_has_no_button_that_only_comes_back_to_it(db, owner):
    owner.settings = {**owner.settings, SHOW_NUMBERS: True}
    db.commit()
    dish = _dish(db, owner, "омлет", **_per_100(), hidden_at=GONE)
    labels = _labels(_shown(f"dh:o:{dish.id}:l0"))
    assert "Ещё ›" not in sum(labels, [])
    assert "✏️ Править цифры" not in sum(labels, [])
    assert "🍽 Записать" in sum(labels, [])
    assert [] not in labels  # no empty row is drawn


def test_control_a_live_card_keeps_them(db, owner):
    owner.settings = {**owner.settings, SHOW_NUMBERS: True}
    db.commit()
    dish = _dish(db, owner, "омлет", **_per_100())
    labels = sum(_labels(_shown(f"dh:o:{dish.id}:l0")), [])
    assert "Ещё ›" in labels and "✏️ Править цифры" in labels


def test_a_hidden_card_keeps_its_way_back(db, owner):
    dish = _dish(db, owner, "омлет")
    undo = _on(_shown(f"dh:x:{dish.id}"), "↩️ Вернуть")
    shown = _shown(f"dh:o:{dish.id}:l0")
    assert _on(shown, "↩️ Вернуть") == undo
    assert "Ещё ›" not in _flat(shown)


# --- a barcode a merged dish brought -------------------------------------------------


def _scan(barcode: str) -> ImageSignals:
    return ImageSignals(
        image_data_url="data:",
        barcode=barcode,
        vision_result=None,
        portion_grams=100.0,
        telegram_id=OWNER,
    )


def _two_barcodes(db, owner):
    target = _dish(
        db, owner, "йогурт", barcode="4700", per_100g_calories=90, times_used=3
    )
    source = _dish(
        db,
        owner,
        "йогурт клубничный",
        barcode="4600",
        per_100g_calories=97,
        times_used=2,
    )
    _shown(f"dh:j:{source.id}:{target.id}")
    return source, target


def _serves(db, owner, barcode):
    db.expire_all()
    return crud_personal_food.get_by_barcode(db, barcode=barcode, user_id=owner.id)


def test_a_merged_dish_barcode_serves_the_dish_it_went_into(db, owner):
    _, target = _two_barcodes(db, owner)
    assert _serves(db, owner, "4600").id == target.id
    served = asyncio.run(SavedFoodRAGStrategy().resolve(_scan("4600"), db))
    assert served is not None and served.source == "saved_rag"


def test_a_merged_dish_barcode_serves_along_the_chain(db, owner):
    _, target = _two_barcodes(db, owner)
    last = _dish(db, owner, "кефир", per_100g_calories=50)
    _shown(f"dh:j:{target.id}:{last.id}")
    assert _serves(db, owner, "4600").id == last.id


def test_a_merged_dish_barcode_goes_quiet_while_the_dish_is_hidden(db, owner):
    _, target = _two_barcodes(db, owner)
    target = _fresh(db, target)
    target.hidden_at = GONE
    db.commit()
    assert _serves(db, owner, "4600") is None


def test_a_dish_barcode_of_its_own_beats_a_merged_one(db, owner):
    _, target = _two_barcodes(db, owner)
    own = _dish(db, owner, "другой", barcode="4600", times_used=1)
    assert _serves(db, owner, "4600").id == own.id
    assert own.id != target.id


def test_a_stranger_merged_barcode_serves_nothing(db, owner):
    _two_barcodes(db, owner)
    other = User(telegram_id=77)
    db.add(other)
    db.commit()
    assert _serves(db, other, "4600") is None


def test_the_write_back_of_a_merged_barcode_leaves_the_target_own(db, owner):
    _, target = _two_barcodes(db, owner)
    db.expire_all()
    crud_personal_food.upsert(
        db,
        user_id=owner.id,
        canonical_name="йогурт",
        barcode="4600",
        resolution_source="saved_rag",
    )
    assert _fresh(db, target).barcode == "4700"
    assert _serves(db, owner, "4700").id == target.id
    assert _serves(db, owner, "4600").id == target.id


def test_control_a_new_barcode_still_lands_on_the_dish(db, owner):
    _, target = _two_barcodes(db, owner)
    db.expire_all()
    crud_personal_food.upsert(
        db, user_id=owner.id, canonical_name="йогурт", barcode="4800"
    )
    assert _fresh(db, target).barcode == "4800"


# --- a near name a merged dish brought -----------------------------------------------


class _AnnSession:
    """The database's half of the ANN query: rows in distance order, on real rows."""

    def __init__(self, db, rows):
        self.db, self.rows, self.stmt = db, rows, None

    def execute(self, stmt, params=None):
        self.stmt = stmt
        return SimpleNamespace(all=lambda: self.rows)

    def get(self, *args):
        return self.db.get(*args)


def _near(food_id, distance):
    return SimpleNamespace(personal_food_id=food_id, cosine_distance=distance)


def _similar(db, owner, *rows, threshold=0.15):
    db.expire_all()
    session = _AnnSession(db, list(rows))
    found = crud_personal_food.find_similar(
        session, embedding=[0.1], threshold=threshold, user_id=owner.id
    )
    return found, session


def test_a_merged_dish_near_name_serves_the_dish_it_went_into(db, owner):
    source, target = _two_barcodes(db, owner)
    (dish, distance), session = _similar(db, owner, _near(source.id, 0.05))
    assert dish.id == target.id and distance == 0.05
    sql = str(session.stmt)
    assert "pf.merged_into_id IS NULL" not in sql.replace(
        "pf.merged_into_id IS NOT NULL", ""
    )
    assert "pf.hidden_at IS NULL" in sql and "pf.user_id = :user_id" in sql


def test_a_near_name_passes_over_a_dish_hidden_since_for_the_next(db, owner):
    source, target = _two_barcodes(db, owner)
    target = _fresh(db, target)
    target.hidden_at = GONE
    db.commit()
    other = _dish(db, owner, "кефир")
    found, _ = _similar(db, owner, _near(source.id, 0.05), _near(other.id, 0.1))
    assert found[0].id == other.id


def test_control_a_near_name_past_the_threshold_is_no_match(db, owner):
    source, _ = _two_barcodes(db, owner)
    assert _similar(db, owner, _near(source.id, 0.5))[0] is None
    assert _similar(db, owner)[0] is None


# --- the card of an empty past day --------------------------------------------------


def _day_screen(db):
    db.expire_all()
    now = datetime(2026, 10, 26, 9, 0, tzinfo=UTC)
    return st.open_view(db, OWNER, st.YESTERDAY, now, False), now


def test_an_empty_yesterday_opens_its_day_and_that_takes_a_meal(db, owner):
    screen, _ = _day_screen(db)
    labels = {
        b.text: b.callback_data for row in screen.markup.inline_keyboard for b in row
    }
    assert labels["🍽 Приёмы"] == f"ml:dl:{SUN}:y"
    day = _shown(labels["🍽 Приёмы"])
    assert "➕ Приём в этот день" in _flat(day)


def test_an_empty_today_has_no_day_to_open(db, owner):
    now = datetime(2026, 10, 26, 9, 0, tzinfo=UTC)
    screen = st.open_view(db, OWNER, st.TODAY, now, False)
    assert "🍽 Приёмы" not in [b.text for r in screen.markup.inline_keyboard for b in r]


# --- a record on a chosen day says the day -------------------------------------------


def test_a_meal_on_a_chosen_day_says_the_day_in_its_reply(db, owner, reader):
    tap(f"ml:da:{SUN}:t")
    wire = send(text("суп с курицей"))
    said = [p["text"] for p in wire.sent()]
    assert said[0].startswith("Записано на вс 25 октября, без времени: суп с курицей.")
    assert not [line for line in said if line.startswith("Не получилось")]


def test_a_photo_on_a_chosen_day_says_the_day_and_no_failure(db, owner, reader):
    tap(f"ml:da:{SUN}:t")
    wire = send(photo(caption="утром"))
    assert wire.edits()[0]["text"].startswith("Записано на вс 25 октября, без времени")
    assert "Не получилось" not in str(wire.sent())


def test_control_a_clock_set_by_hand_stays_plain(db, owner):
    dish = _dish(db, owner, "омлет")
    meal, _ = _eat(db, owner, dish)
    meal.time_source, meal.time_precision = "edited", "exact"
    db.commit()
    assert meal_card.line_head(meal, None, "Записано") == "Записано"


# --- the topic's way to the groups ---------------------------------------------------


def _walk(first: dict, *labels) -> dict:
    shown = first
    for label in labels:
        shown = _shown(_on(shown, label))
    return shown


def test_the_groups_topic_is_a_way_that_exists_on_one_dish_and_on_many(db, owner):
    how = explain_screen.TOPICS["groups"].how
    for label in ("«✏️ Исправить»", "«Ещё ›»", "«🏷 Группы»", "«⬅️ К приёму»"):
        assert label in how
    one = _dish(db, owner, "омлет")
    meal, item = _eat(db, owner, one)
    fixed = _shown(f"ml:i:{item.id}:r")  # what ✏️ Исправить opens on one dish
    assert "Ещё ›" not in _flat(fixed)
    groups = _walk(fixed, "⬅️ К приёму", "Ещё ›", "🏷 Группы")
    assert "🏷 Группы" in groups["text"]
    second = meal_items.add_item(db, owner.id, meal.id, name="кефир").items[0]
    assert second is not None
    many = _shown(f"ml:o:{meal.id}:r")  # and on many
    assert "🏷 Группы" in _walk(many, "Ещё ›", "🏷 Группы")["text"]


def test_control_the_fix_button_goes_where_the_walk_starts(db, owner):
    one = _dish(db, owner, "омлет")
    meal, item = _eat(db, owner, one)
    row = meal_card.line_markup(db, meal).inline_keyboard[0]
    assert row[0].text == "✏️ Исправить"
    assert row[0].callback_data == f"ml:i:{item.id}:r"


# --- the rollout line waits, while it would be false ---------------------------------


def _user_settings(db, owner):
    db.expire_all()
    return db.get(User, owner.id).settings


def test_m12_waits_for_drafts_to_go_off_and_is_then_sent_once(db, owner, reader):
    rest = {k: v for k, v in owner.settings.items() if k != capture.ROLLOUT_SEEN}
    owner.settings = {**rest, "draft_mode": True}
    db.add(Meal(user_id=owner.id, meal_time=date(2026, 9, 1), description="x"))
    db.commit()
    assert capture.M12_ROLLOUT not in [p["text"] for p in send(text("рис")).sent()]
    assert capture.ROLLOUT_SEEN not in _user_settings(db, owner)
    owner = db.get(User, owner.id)
    owner.settings = {**owner.settings, "draft_mode": False}
    db.commit()
    said = [p["text"] for p in send(text("суп")).sent()]
    assert said.count(capture.M12_ROLLOUT) == 1
    assert _user_settings(db, owner)[capture.ROLLOUT_SEEN] is True
    assert capture.M12_ROLLOUT not in [p["text"] for p in send(text("чай")).sent()]


def test_a_newcomer_with_drafts_on_has_nothing_to_wait_for(db, owner, reader):
    rest = {k: v for k, v in owner.settings.items() if k != capture.ROLLOUT_SEEN}
    owner.settings = {**rest, "draft_mode": True}
    db.commit()
    assert capture.M12_ROLLOUT not in [p["text"] for p in send(text("рис")).sent()]
    assert _user_settings(db, owner)[capture.ROLLOUT_SEEN] is True
