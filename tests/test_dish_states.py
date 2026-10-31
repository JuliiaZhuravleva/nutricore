"""A dish hidden, merged or renamed (3.3), and a favourite that became a dish.

On real rows. Typing goes through the real application at the wire (``create_bot_
application``), and the barcode through the real strategy. The model is a fake and is
asserted not to be asked: ``reader`` stands in for it, and nothing here reaches the
OpenAI client. The ANN statement is asserted at compile level on the Postgres dialect,
because SQLite has no ``<=>``.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from app.crud.crud_favorite_meal import crud_favorite_meal
from app.crud.crud_personal_food import crud_personal_food
from app.models import FavoriteMeal, Meal, MealItem, PersonalFood, User
from app.services import meal_items
from app.services import telegram as tg
from app.services.favorites import save_from_meal
from app.services.product_lookup_service import ImageSignals, SavedFoodRAGStrategy
from tests.test_capture_corrections import tap
from tests.test_capture_router import (  # noqa: F401 (fixtures)
    OWNER,
    Session,
    db,
    owner,
    reader,
    send,
    text,
)
from tests.test_meal_card import _one

GONE = datetime(2026, 10, 1, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _no_screen_from_another_test(monkeypatch):
    """The delivery remembers what a message shows, and skips an unchanged edit."""
    monkeypatch.setattr(tg._card_delivery, "_screens", {})


def _dish(db, owner, name, **columns) -> PersonalFood:
    dish = PersonalFood(user_id=owner.id, canonical_name=name, **columns)
    db.add(dish)
    db.commit()
    return dish


@pytest.fixture
def stranger(db):
    user = User(telegram_id=77)
    db.add(user)
    db.commit()
    return user


def _rows(db):
    db.expire_all()
    return list(db.scalars(select(PersonalFood).order_by(PersonalFood.id)).all())


def _only_meal(db) -> Meal:
    db.expire_all()
    return db.scalars(select(Meal)).one()


def _items(db, meal) -> list:
    return list(db.scalars(select(MealItem).where(MealItem.meal_id == meal.id)))


# --- a name always finds the row that holds it ------------------------------------


def test_a_hidden_dish_is_still_found_by_name(db, owner):
    hidden = _dish(db, owner, "Омлет", hidden_at=GONE)
    assert crud_personal_food.find_by_name(db, owner.id, "ОМЛЕТ").id == hidden.id


def test_a_merged_name_finds_the_dish_it_went_into_along_the_whole_chain(db, owner):
    last = _dish(db, owner, "Омлет")
    middle = _dish(db, owner, "Яичница", merged_into_id=last.id)
    first = _dish(db, owner, "Омлет с сыром", merged_into_id=middle.id)
    assert crud_personal_food.find_by_name(db, owner.id, "омлет с сыром").id == last.id
    assert crud_personal_food.find_by_name(db, owner.id, "яичница").id == last.id
    assert crud_personal_food.live_of(db, first).id == last.id
    assert crud_personal_food.live_of(db, last).id == last.id


def test_a_loop_of_merges_ends_instead_of_hanging(db, owner):
    one = _dish(db, owner, "Один")
    two = _dish(db, owner, "Два", merged_into_id=one.id)
    one.merged_into_id = two.id
    db.commit()
    assert crud_personal_food.find_by_name(db, owner.id, "один") is not None
    assert crud_personal_food.live_of(db, one) is not None


def test_an_old_name_finds_the_renamed_dish_and_a_real_name_beats_it(db, owner):
    renamed = _dish(db, owner, "Омлет", aliases=["Омлет с сыром", "Яичница"])
    real = _dish(db, owner, "Яичница")
    found = crud_personal_food.find_by_name(db, owner.id, "омлет с сыром")
    assert found.id == renamed.id
    assert crud_personal_food.find_by_name(db, owner.id, "ЯИЧНИЦА").id == real.id


def test_another_persons_names_stay_theirs(db, owner, stranger):
    _dish(db, stranger, "Омлет", aliases=["Яичница"])
    assert crud_personal_food.find_by_name(db, owner.id, "омлет") is None
    assert crud_personal_food.find_by_name(db, owner.id, "яичница") is None


# --- lists, barcode, ANN --------------------------------------------------------


def test_the_list_holds_only_live_dishes(db, owner, stranger):
    live = _dish(db, owner, "Рис")
    _dish(db, owner, "Хлеб", hidden_at=GONE)
    _dish(db, owner, "Суп", merged_into_id=live.id)
    _dish(db, stranger, "Чужое")
    assert [d.id for d in crud_personal_food.list_live(db, owner.id)] == [live.id]


def test_get_for_user_sees_every_state_and_no_other_person(db, owner, stranger):
    hidden = _dish(db, owner, "Хлеб", hidden_at=GONE)
    assert crud_personal_food.get_for_user(db, hidden.id, owner.id).id == hidden.id
    assert crud_personal_food.get_for_user(db, hidden.id, stranger.id) is None


def test_a_barcode_passes_over_hidden_and_merged_rows(db, owner):
    live = _dish(db, owner, "Йогурт", barcode="4600", times_used=1)
    _dish(db, owner, "Йогурт скрытый", barcode="4600", times_used=9, hidden_at=GONE)
    _dish(
        db, owner, "Йогурт старый", barcode="4600", times_used=8, merged_into_id=live.id
    )
    assert crud_personal_food.get_by_barcode(db, barcode="4600", user_id=owner.id) == (
        live
    )
    live.hidden_at = GONE
    db.commit()
    assert (
        crud_personal_food.get_by_barcode(db, barcode="4600", user_id=owner.id) is None
    )


def _scan(barcode: str) -> ImageSignals:
    return ImageSignals(
        image_data_url="data:",
        barcode=barcode,
        vision_result=None,
        portion_grams=100.0,
        telegram_id=OWNER,
    )


def test_a_scan_is_not_served_from_a_hidden_dish(db, owner):
    dish = _dish(db, owner, "Йогурт", barcode="4600", per_100g_calories=97)
    strategy = SavedFoodRAGStrategy()
    served = asyncio.run(strategy.resolve(_scan("4600"), db))
    assert (
        served is not None and served.source
    )  # known-positive: it is served when live
    dish.hidden_at = GONE
    db.commit()
    assert asyncio.run(strategy.resolve(_scan("4600"), db)) is None


class _CapturingSession:
    def __init__(self):
        self.stmt = None

    def execute(self, stmt, params=None):
        self.stmt = stmt
        return SimpleNamespace(all=lambda: [])


def test_the_ann_statement_filters_inside_the_query_for_the_next_best_match():
    session = _CapturingSession()
    crud_personal_food.find_similar(
        session, embedding=[0.1, 0.2], threshold=0.15, user_id=1
    )
    sql = " ".join(str(session.stmt.compile(dialect=postgresql.dialect())).split())
    where = sql.split("WHERE", 1)[1].split("ORDER BY", 1)[0]
    assert "pf.user_id" in where  # known-positive: this is the clause that scopes
    # A merged row's words still count: the Python past the query answers with its dish.
    assert "pf.hidden_at IS NULL OR pf.merged_into_id IS NOT NULL" in where


# --- the learning write-back ------------------------------------------------------


def _learn(db, owner, name, meal_id=None, calories=200.0):
    return crud_personal_food.upsert(
        db,
        user_id=owner.id,
        canonical_name=name,
        meal_id=meal_id,
        per_100g_calories=calories,
    )


def test_upsert_of_a_hidden_name_brings_it_back_and_adds_no_row(db, owner):
    """A second row would be an IntegrityError on Postgres, where the key lives."""
    hidden = _dish(db, owner, "Омлет", hidden_at=GONE)
    assert _learn(db, owner, "омлет").id == hidden.id
    assert [row.id for row in _rows(db)] == [hidden.id]
    assert hidden.hidden_at is None


def test_a_retry_for_a_meal_already_counted_does_not_bring_it_back(db, owner):
    """Hidden after the meal, while its task was retried: it stays as asked."""
    dish = _dish(db, owner, "Омлет", meal_id=None, hidden_at=GONE)
    meal = Meal(user_id=owner.id)
    db.add(meal)
    db.commit()
    dish.meal_id = meal.id
    db.commit()
    _learn(db, owner, "Омлет", meal_id=meal.id)
    db.refresh(dish)
    assert dish.hidden_at is not None


def test_upsert_of_a_merged_name_writes_to_the_target(db, owner):
    target = _dish(db, owner, "Омлет")
    source = _dish(db, owner, "Омлет с сыром", merged_into_id=target.id)
    assert _learn(db, owner, "Омлет с сыром", calories=222.0).id == target.id
    assert len(_rows(db)) == 2
    assert target.per_100g_calories == 222.0
    assert source.per_100g_calories is None


def test_upsert_keeps_what_a_person_typed_on_the_target(db, owner):
    target = _dish(db, owner, "Омлет", per_100g_calories=150, numbers_by_user=True)
    _dish(db, owner, "Омлет с сыром", merged_into_id=target.id)
    _learn(db, owner, "Омлет с сыром", calories=999.0)
    db.refresh(target)
    assert float(target.per_100g_calories) == 150


# --- rename ------------------------------------------------------------------------


def test_rename_makes_the_old_name_an_alias_that_survives_a_real_commit(db, owner):
    dish = _dish(db, owner, "Омлет с сыром")
    crud_personal_food.rename(db, dish, "  Омлет ")
    db.commit()
    db.expire_all()
    assert dish.canonical_name == "Омлет"
    assert dish.aliases == ["Омлет с сыром"]
    crud_personal_food.rename(db, dish, "Яичница")
    db.commit()
    db.expire_all()
    # A second one: an in-place append to a JSON column would be lost here.
    assert dish.aliases == ["Омлет с сыром", "Омлет"]
    assert crud_personal_food.find_by_name(db, owner.id, "омлет с сыром").id == dish.id


def test_rename_back_takes_the_name_out_of_the_aliases(db, owner):
    dish = _dish(db, owner, "Омлет с сыром")
    crud_personal_food.rename(db, dish, "Омлет")
    crud_personal_food.rename(db, dish, "ОМЛЕТ С СЫРОМ")
    db.commit()
    db.expire_all()
    assert dish.canonical_name == "ОМЛЕТ С СЫРОМ"
    assert dish.aliases == ["Омлет"]


def test_a_change_of_case_is_no_alias(db, owner):
    dish = _dish(db, owner, "омлет")
    crud_personal_food.rename(db, dish, "Омлет")
    db.commit()
    db.expire_all()
    assert dish.canonical_name == "Омлет"
    assert not dish.aliases


def test_a_name_is_held_by_any_row_the_unique_key_sees(db, owner, stranger):
    """Hidden and merged rows keep their name in the key: a rename onto one would be
    an IntegrityError on Postgres, so the check cannot stop at live dishes."""
    live = _dish(db, owner, "Омлет")
    hidden = _dish(db, owner, "Хлеб", hidden_at=GONE)
    merged = _dish(db, owner, "Суп", merged_into_id=live.id)
    held = crud_personal_food.holder_of_name
    assert held(db, owner.id, "ОМЛЕТ").id == live.id
    assert held(db, owner.id, "хлеб").id == hidden.id
    assert held(db, owner.id, " суп ").id == merged.id
    assert held(db, owner.id, "омлет", without=live.id) is None  # its own case change
    assert held(db, stranger.id, "омлет") is None
    assert held(db, owner.id, "каша") is None


def test_a_name_only_the_database_folds_together_is_held_and_found(db, owner):
    """Python lowers Σ at a word's end to ς, Postgres to σ: its key refuses the second."""
    held = _dish(db, owner, "ασ")
    typed = "ΑΣ"
    # The control: with SQLite's own lower() neither side folds them, so nothing is held.
    assert crud_personal_food.holder_of_name(db, owner.id, typed) is None
    assert crud_personal_food.find_by_name(db, owner.id, typed) is None
    raw = db.connection().connection.driver_connection
    raw.create_function("lower", 1, lambda value: value.lower().replace("ς", "σ"))
    assert crud_personal_food.holder_of_name(db, owner.id, typed).id == held.id
    assert (
        crud_personal_food.holder_of_name(db, owner.id, typed, without=held.id) is None
    )
    assert crud_personal_food.find_by_name(db, owner.id, typed).id == held.id


def test_an_alias_holds_no_place_in_the_key(db, owner):
    _dish(db, owner, "Омлет", aliases=["Яичница"])
    assert crud_personal_food.holder_of_name(db, owner.id, "яичница") is None


# --- through the bot: typing a name ------------------------------------------------


def test_typing_a_hidden_dish_records_it_and_it_returns(db, owner, reader):
    dish = _dish(db, owner, "Омлет", hidden_at=GONE)
    assert dish not in crud_personal_food.list_live(db, owner.id)  # known-positive

    wire = send(text("омлет"))

    assert reader.text == []
    assert wire.sent()[0]["text"].startswith("Записано: Омлет.")
    assert [i.personal_food_id for i in _items(db, _only_meal(db))] == [dish.id]
    db.refresh(dish)
    assert dish.hidden_at is None
    assert dish in crud_personal_food.list_live(db, owner.id)


def test_typing_a_merged_name_records_the_target(db, owner, reader):
    target = _dish(db, owner, "Омлет")
    _dish(db, owner, "Омлет с сыром", merged_into_id=target.id)

    wire = send(text("омлет с сыром"))

    assert reader.text == []
    assert wire.sent()[0]["text"].startswith("Записано: Омлет.")
    item = _items(db, _only_meal(db))[0]
    assert (item.personal_food_id, item.original_food_id) == (target.id, target.id)
    assert len(_rows(db)) == 2


def test_typing_an_old_name_records_the_renamed_dish(db, owner, reader):
    dish = _dish(db, owner, "Омлет", aliases=["Омлет с сыром"])
    send(text("омлет с сыром"))
    assert reader.text == []
    assert _items(db, _only_meal(db))[0].personal_food_id == dish.id


def test_a_name_nobody_holds_still_goes_to_the_model(db, owner, reader):
    """The control for all three above: the fake model does get asked."""
    _dish(db, owner, "Омлет")
    send(text("каша с изюмом"))
    assert reader.text == ["каша с изюмом"]


# --- the picker ---------------------------------------------------------------------


def _meal_with_one_dish(db, owner) -> Meal:
    from app.schemas.meal import MealCreate

    return meal_items.save_meal(
        db,
        MealCreate(
            description="суп",
            meal_time=datetime(2026, 10, 26, 9, 0, tzinfo=UTC),
            calories=100.0,
        ),
        owner.id,
    )


def test_the_picker_offers_neither_hidden_nor_merged(db, owner):
    meal = _meal_with_one_dish(db, owner)
    live = _dish(db, owner, "Рис")
    _dish(db, owner, "Хлеб", hidden_at=GONE)
    _dish(db, owner, "Каша", merged_into_id=live.id)
    shown = str(tap(f"ml:pk:{meal.id}:a:r:0").calls)
    assert "Рис" in shown  # known-positive
    assert "Хлеб" not in shown
    assert "Каша" not in shown


def test_a_picker_on_screen_finds_a_hidden_or_merged_dish_gone(db, owner):
    meal = _meal_with_one_dish(db, owner)
    live = _dish(db, owner, "Рис")
    hidden = _dish(db, owner, "Хлеб", hidden_at=GONE)
    merged = _dish(db, owner, "Каша", merged_into_id=live.id)
    before = len(_items(db, meal))
    # Each on its own message: an unchanged screen is not edited a second time.
    for message_id, dish in ((4901, hidden), (4902, merged)):
        wire = tap(f"ml:pa:{meal.id}:{dish.id}:r", message_id=message_id)
        assert "Этого блюда уже нет в списке." in str(wire.calls)
    db.expire_all()
    assert len(_items(db, meal)) == before
    # Known-positive: the same tap on a live dish adds it.
    assert "Этого блюда уже нет" not in str(tap(f"ml:pa:{meal.id}:{live.id}:r").calls)
    db.expire_all()
    assert len(_items(db, meal)) == before + 1


# --- a favourite is a dish ----------------------------------------------------------


def _saved_meal(db, owner, description="Самса") -> Meal:
    meal = _meal_with_one_dish(db, owner)
    meal.description = description
    db.commit()
    return meal


def test_a_new_favourite_finds_its_dish_and_leaves_its_numbers(db, owner):
    dish = _dish(db, owner, "Самса", per_100g_calories=300, numbers_basis="per_100")
    fav, created = save_from_meal(db, _saved_meal(db, owner), "самса")
    assert created and fav.personal_food_id == dish.id
    db.refresh(dish)
    assert dish.numbers_basis == "per_100"
    assert float(dish.per_100g_calories) == 300


def test_a_new_favourite_with_no_dish_makes_one_for_a_portion(db, owner):
    fav, _ = save_from_meal(db, _saved_meal(db, owner), "рис, курица и овощи")
    dish = db.get(PersonalFood, fav.personal_food_id)
    assert dish.canonical_name == "рис, курица и овощи"
    assert dish.numbers_basis == "per_portion"
    assert dish.per_100g_calories is None
    assert dish.times_used == 0
    assert fav.usual_grams is None


def test_a_favourite_of_a_hidden_dish_brings_it_back(db, owner):
    """Asking for it by a star is asking for it: a ⭐ on a hidden dish lists nowhere."""
    hidden = _dish(db, owner, "Самса", hidden_at=GONE)
    fav, _ = save_from_meal(db, _saved_meal(db, owner), "Самса")
    assert fav.personal_food_id == hidden.id
    db.refresh(hidden)
    assert hidden.hidden_at is None


def test_a_dish_star_on_the_card_keeps_the_grams_of_that_portion(db, owner):
    meal, item = _one(db, owner, "Самса", 300.0, 150.0)
    tap(f"ml:fi:{item.id}:1:r")
    db.expire_all()
    fav = db.scalars(select(FavoriteMeal)).one()
    assert (fav.name, fav.calories, fav.usual_grams) == ("Самса", 300.0, 150.0)
    assert fav.personal_food_id == item.personal_food_id
    assert fav.personal_food_id is not None


def test_a_whole_meal_star_on_the_card_is_a_portion_with_no_grams(db, owner):
    meal, item = _one(db, owner, "Самса", 300.0, 150.0)
    tap(f"ml:fm:{meal.id}:1:t")
    db.expire_all()
    fav = db.scalars(select(FavoriteMeal)).one()
    assert fav.personal_food_id is not None
    assert fav.usual_grams is None


def _old_favourite(db, owner, name="Самса", **columns) -> FavoriteMeal:
    fav = FavoriteMeal(user_id=owner.id, name=name, calories=420.0, **columns)
    db.add(fav)
    db.commit()
    return fav


def test_a_row_from_an_old_container_is_linked_when_it_is_asked_for(db, owner):
    fav = _old_favourite(db, owner)
    assert fav.personal_food_id is None
    dish = crud_favorite_meal.dish_of(db, fav)
    assert dish.canonical_name == "Самса"
    assert fav.personal_food_id == dish.id
    assert crud_favorite_meal.dish_of(db, fav).id == dish.id
    assert len(_rows(db)) == 1


def test_a_link_is_followed_past_a_merge(db, owner):
    target = _dish(db, owner, "Омлет")
    source = _dish(db, owner, "Омлет с сыром", merged_into_id=target.id)
    fav = _old_favourite(db, owner, "Омлет с сыром", personal_food_id=source.id)
    assert crud_favorite_meal.dish_of(db, fav).id == target.id


def test_active_of_dish_is_the_last_used_and_never_a_removed_one(db, owner):
    dish = _dish(db, owner, "Самса", numbers_basis="per_portion")
    old = _old_favourite(
        db,
        owner,
        personal_food_id=dish.id,
        last_used_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    recent = _old_favourite(
        db,
        owner,
        "самса",
        personal_food_id=dish.id,
        last_used_at=datetime(2026, 9, 1, tzinfo=UTC),
    )
    assert crud_favorite_meal.active_of_dish(db, dish.id).id == recent.id
    crud_favorite_meal.set_removed(db, recent, True)
    assert crud_favorite_meal.active_of_dish(db, dish.id).id == old.id
    crud_favorite_meal.set_removed(db, old, True)
    assert crud_favorite_meal.active_of_dish(db, dish.id) is None


def test_typing_a_favourites_name_records_its_own_numbers(db, owner, reader):
    """The backfill makes the name known, and a known name costs no model. Its dish has
    no numbers of its own, so without this the day would lose them without a word."""
    dish = _dish(db, owner, "Самса", numbers_basis="per_portion")
    _old_favourite(db, owner, personal_food_id=dish.id, proteins=12.0, usual_grams=150)

    send(text("самса"))

    assert reader.text == []
    meal = _only_meal(db)
    assert meal.calories == 420.0
    assert meal.numbers_basis == "items"
    item = _items(db, meal)[0]
    assert (item.base_calories, item.base_proteins) == (420.0, 12.0)
    assert item.base_grams == 150
    assert item.numbers_source == "dish"
    db.refresh(dish)
    assert dish.per_100g_calories is None  # the portion stays the favourite's


def test_a_removed_favourite_no_longer_lends_its_numbers(db, owner, reader):
    dish = _dish(db, owner, "Самса", numbers_basis="per_portion")
    _old_favourite(db, owner, personal_food_id=dish.id, removed_at=GONE)
    send(text("самса"))
    meal = _only_meal(db)
    assert meal.calories is None
    assert meal.numbers_basis == "none"


def test_the_dish_s_own_portion_comes_before_the_favourite(db, owner):
    dish = _dish(
        db,
        owner,
        "Самса",
        per_100g_calories=200,
        usual_grams=100.0,
        numbers_basis="per_100",
    )
    _old_favourite(db, owner, personal_food_id=dish.id)
    assert meal_items.dish_portion(db, dish).numbers["calories"] == 200.0
