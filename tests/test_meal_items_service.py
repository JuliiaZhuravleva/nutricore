"""Slice N, items at save and item changes, on real rows through the real consumers."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from telegram.ext import ConversationHandler, MessageHandler

from app.api.v1.meals import create_meal
from app.core.config import settings
from app.crud.crud_meal import crud_meal
from app.crud.crud_meal_item import crud_meal_item
from app.crud.crud_personal_food import crud_personal_food
from app.crud.crud_record_event import crud_record_event
from app.db.base import Base
from app.models import FavoriteMeal, Meal, MealItem, PersonalFood, RecordEvent, User
from app.schemas.meal import MealCreate
from app.services import day_card, favorites
from app.services import inbound_message_service as im_service
from app.services import meal_items
from app.services import telegram as tg
from app.services.stats_screen import _meal_name, _sum

NOW = datetime(2026, 10, 26, 13, 20, tzinfo=UTC)
DELAY = "celery_app.tasks.personal_food.embed_and_save_personal_food.delay"


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
    monkeypatch.setattr(im_service, "SessionLocal", factory)
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_IDS", "42")
    yield factory
    engine.dispose()


@pytest.fixture
def db(Session):
    with Session() as session:
        yield session


@pytest.fixture
def owner(db):
    user = User(telegram_id=42, username="owner")
    db.add(user)
    db.commit()
    return user


@pytest.fixture
def delay():
    with patch(DELAY) as mock:
        yield mock


def _update(text):
    message = SimpleNamespace(photo=None, text=text, caption=None, replies=[])

    async def reply_text(body, *args, **kwargs):
        message.replies.append(body)

    message.reply_text = reply_text
    update = SimpleNamespace(
        message=message,
        effective_user=SimpleNamespace(id=42, username="owner"),
    )
    return update, message


def _confirm(draft):
    """The draft saved by the real handler, with a typed «Да»."""
    update, message = _update("Да")
    context = SimpleNamespace(
        user_data={"current_meal": draft, "meal_time": NOW, "meal_type": "Обед"}
    )
    asyncio.run(tg.confirm_meal(update, context))
    assert "✅ Записано" in message.replies[-1]


def _draft(foods, calories=500.0, **fields):
    nutrition = {
        "foods": foods,
        "calories": calories,
        "protein": 20.0,
        "fats": 10.0,
        "carbs": 60.0,
        "portion": "250г",
    }
    return {"nutrition": nutrition, "description": ", ".join(foods), **fields}


def _only_meal(db) -> Meal:
    db.expire_all()
    (meal,) = db.scalars(select(Meal)).all()
    return meal


def _dishes(db) -> list:
    db.expire_all()
    return list(db.scalars(select(PersonalFood).order_by(PersonalFood.id)).all())


def _events(db) -> list:
    db.expire_all()
    return list(db.scalars(select(RecordEvent).order_by(RecordEvent.id)).all())


def _saved(db, owner, foods=("рис",), calories=500.0, **fields) -> Meal:
    """A meal written by the same function the save paths use."""
    tags = fields.pop("tags", None)
    nutrients = {
        "foods": list(foods),
        "calories": calories,
        "protein": 20.0,
        "fats": 10.0,
        "carbs": 60.0,
        "portion": fields.pop("portion", "250г"),
    }
    if tags is not None:
        nutrients.update(tags)
    fields.setdefault("ai_analysis", dict(tags) if tags else None)
    return meal_items.save_meal(
        db,
        MealCreate(
            description=", ".join(foods),
            meal_time=NOW,
            calories=calories,
            proteins=20.0,
            fats=10.0,
            carbohydrates=60.0,
            nutrients=nutrients,
            **fields,
        ),
        owner.id,
    )


def _legacy(db, owner, **fields) -> Meal:
    """A meal from before items: no item row, ``plate`` from the backfill."""
    fields.setdefault("description", "старый обед")
    fields.setdefault("calories", 333.33)
    return crud_meal.create(
        db,
        MealCreate(
            meal_time=NOW, proteins=11.11, fats=7.77, carbohydrates=44.44, **fields
        ),
        owner.id,
    )


def _dish(db, owner, name, **fields) -> PersonalFood:
    fields.setdefault("times_used", 1)
    dish = PersonalFood(user_id=owner.id, canonical_name=name, **fields)
    db.add(dish)
    db.commit()
    return dish


# --- at save ----------------------------------------------------------------


def test_several_foods_give_one_item_per_name_and_a_plate(Session, db, owner, delay):
    _confirm(_draft(["рис", "курица", "овощи"]))

    meal = _only_meal(db)
    items = crud_meal_item.live(db, meal.id)
    assert meal.numbers_basis == "plate"
    assert meal.plate_totals == {
        "calories": 500.0,
        "proteins": 20.0,
        "fats": 10.0,
        "carbohydrates": 60.0,
    }
    assert meal.calories == 500.0
    assert [item.name for item in items] == ["рис", "курица", "овощи"]
    assert [item.position for item in items] == [0, 1, 2]
    # Dish lines carry no numbers of their own: nothing is split by share.
    assert all(item.base_calories is None for item in items)
    assert {item.numbers_source for item in items} == {"plate_share"}


def test_plate_numbers_are_not_written_under_one_food_name(Session, db, owner, delay):
    _confirm(_draft(["рис", "курица"]))

    delay.assert_not_called()
    # Each name is a dish all the same, without numbers.
    dishes = _dishes(db)
    assert [d.canonical_name for d in dishes] == ["рис", "курица"]
    assert all(d.per_100g_calories is None for d in dishes)
    assert {d.numbers_basis for d in dishes} == {"none"}


def test_control_one_food_still_teaches_the_base(Session, db, owner, delay):
    _confirm(_draft(["рис"]))

    delay.assert_called_once()
    kwargs = delay.call_args.kwargs
    assert kwargs["canonical_name"] == "рис"
    assert kwargs["per_100g_calories"] == 200.0  # 500 kcal for 250 g
    assert kwargs["meal_id"] == _only_meal(db).id


def test_one_food_gives_items_and_its_numbers(Session, db, owner, delay):
    _confirm(_draft(["рис"]))

    meal = _only_meal(db)
    (item,) = crud_meal_item.live(db, meal.id)
    assert meal.numbers_basis == "items"
    assert meal.plate_totals is None
    assert meal.meal_type == "Обед"
    assert item.name == "рис"
    assert item.base_calories == 500.0
    assert item.base_proteins == 20.0
    assert item.base_fats == 10.0
    assert item.base_carbohydrates == 60.0
    assert item.factor == 1
    assert item.numbers_source == "model"
    # Model grams are the base; ``grams`` is for what a person typed.
    assert item.base_grams == 250.0
    assert item.grams is None
    assert item.by_user is False
    assert item.origin_meal_id == meal.id
    assert item.personal_food_id == item.original_food_id == _dishes(db)[0].id


def test_a_weight_typed_on_the_draft_is_kept_as_the_persons(Session, db, owner, delay):
    draft = _draft(["рис"], calories=300.0, grams_by_user=True)
    draft["nutrition"]["portion"] = "150г"
    draft["nutrition"]["portion_grams"] = 150.0
    _confirm(draft)

    (item,) = crud_meal_item.live(db, _only_meal(db).id)
    assert item.grams == 150.0
    assert item.base_grams == 150.0
    assert item.base_calories == 300.0
    assert item.by_user is True


def test_nothing_read_gives_none_and_null_totals(Session, db, owner, delay):
    _confirm(_draft(["чай"], calories=None) | {"description": "чай"})
    meal = _only_meal(db)
    # Only the calories are missing here: the others are known, so it is read.
    assert meal.numbers_basis == "items"

    crud_meal.remove(db, meal.id)
    draft = {"nutrition": {"foods": ["чай"]}, "description": "чай"}
    _confirm(draft)

    meal = _only_meal(db)
    (item,) = crud_meal_item.live(db, meal.id)
    assert meal.numbers_basis == "none"
    assert meal.calories is None and meal.plate_totals is None
    assert item.numbers_source == "none"


def test_barcode_links_the_dish_whatever_its_name(Session, db, owner, delay):
    known = _dish(
        db,
        owner,
        "йогурт любимый",
        barcode="4600000000017",
        per_100g_calories=Decimal("97.00"),
    )
    signals = {
        "barcode_raw": "4600000000017",
        "product_name": "Greek Yogurt",
        "portion_grams": 200.0,
    }
    _confirm(
        _draft(
            ["Greek Yogurt"],
            calories=194.0,
            resolution_source="barcode_off",
            resolution_signals=signals,
        )
    )

    meal = _only_meal(db)
    (item,) = crud_meal_item.live(db, meal.id)
    assert item.personal_food_id == known.id
    assert item.name == "Greek Yogurt"  # the item keeps its own copy of the name
    assert item.numbers_source == "barcode"
    assert len(_dishes(db)) == 1
    # The task updates that dish and does not make a second one with this barcode.
    assert delay.call_args.kwargs["canonical_name"] == "йогурт любимый"


def test_a_product_with_two_read_foods_is_one_dish(Session, db, owner, delay):
    signals = {"product_name": "Сырок", "portion_grams": 50.0}
    _confirm(
        _draft(
            ["сырок", "глазурь"],
            calories=120.0,
            resolution_source="name_off",
            resolution_signals=signals,
        )
    )

    meal = _only_meal(db)
    assert meal.numbers_basis == "items"
    (item,) = crud_meal_item.live(db, meal.id)
    assert item.name == "Сырок"
    delay.assert_called_once()


@pytest.mark.parametrize(
    "source, expected",
    [
        ("label_ocr", "label"),
        ("saved_rag", "dish"),
        ("vision", "model"),
        (None, "model"),
    ],
)
def test_numbers_source_follows_the_reading(db, owner, source, expected):
    meal = _saved(db, owner, resolution_source=source)
    (item,) = crud_meal_item.live(db, meal.id)
    assert item.numbers_source == expected


def test_same_name_links_one_dish_ignoring_case(db, owner):
    first = _saved(db, owner, foods=("Омлет",))
    second = _saved(db, owner, foods=("омлет",))
    other = _saved(db, owner, foods=("омлет с сыром",))

    (a,) = crud_meal_item.live(db, first.id)
    (b,) = crud_meal_item.live(db, second.id)
    (c,) = crud_meal_item.live(db, other.id)
    assert a.personal_food_id == b.personal_food_id
    assert c.personal_food_id != a.personal_food_id
    assert a.name == "Омлет" and b.name == "омлет"  # own copy of the name
    dish = db.get(PersonalFood, a.personal_food_id)
    assert dish.times_used == 2


def test_another_persons_dish_is_never_linked(db, owner):
    stranger = User(telegram_id=77, username="stranger")
    db.add(stranger)
    db.commit()
    theirs = _dish(db, stranger, "омлет")

    meal = _saved(db, owner, foods=("омлет",))

    (item,) = crud_meal_item.live(db, meal.id)
    assert item.personal_food_id != theirs.id


def test_the_learning_task_does_not_count_the_same_meal_twice(db, owner):
    meal = _saved(db, owner, foods=("рис",))
    (item,) = crud_meal_item.live(db, meal.id)
    dish = db.get(PersonalFood, item.personal_food_id)
    assert dish.times_used == 1
    assert dish.numbers_basis == "none"

    crud_personal_food.upsert(
        db,
        user_id=owner.id,
        canonical_name="рис",
        meal_id=meal.id,
        per_100g_calories=200.0,
        resolution_source="vision",
    )

    db.refresh(dish)
    assert dish.times_used == 1
    assert dish.numbers_basis == "per_100"
    assert dish.numbers_quality == "unverified"


def test_upsert_records_how_good_the_numbers_are(db, owner):
    barcode = crud_personal_food.upsert(
        db,
        user_id=owner.id,
        canonical_name="йогурт",
        barcode="1",
        per_100g_calories=97.0,
        resolution_source="barcode_off",
    )
    label = crud_personal_food.upsert(
        db,
        user_id=owner.id,
        canonical_name="сырок",
        per_100g_calories=400.0,
        resolution_source="label_ocr",
    )
    bare = crud_personal_food.upsert(db, user_id=owner.id, canonical_name="вода")
    assert barcode.numbers_quality == "barcode"
    assert label.numbers_quality == "label"
    assert bare.numbers_basis == "none"
    assert (barcode.numbers_basis, label.numbers_basis) == ("per_100", "per_100")


def test_save_is_one_transaction(db, owner, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("no item today")

    monkeypatch.setattr(crud_meal_item, "add", boom)
    with pytest.raises(RuntimeError):
        _saved(db, owner, foods=("рис",))
    assert db.scalars(select(Meal)).all() == []
    assert _dishes(db) == []

    monkeypatch.undo()
    # Control: the same call without the planted failure writes the rows.
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_IDS", "42")
    meal = _saved(db, owner, foods=("рис",))
    assert crud_meal_item.live(db, meal.id)


def test_favourite_log_writes_one_item_and_leaves_the_dish_numbers(db, owner):
    fav = FavoriteMeal(
        user_id=owner.id,
        name="сырники",
        calories=420.4,
        proteins=12,
        fats=22.25,
        carbohydrates=42,
        nutrients={"foods": ["творог", "мука"], "calories": 420.4},
    )
    db.add(fav)
    db.commit()

    favorites.handle_callback(db, 42, f"{favorites.CALLBACK_PREFIX}l:{fav.id}:n")

    meal = _only_meal(db)
    (item,) = crud_meal_item.live(db, meal.id)
    assert meal.numbers_basis == "items"
    assert meal.calories == 420.4
    assert item.name == "сырники"
    assert item.numbers_source == "dish"
    assert item.base_calories == 420.4
    (dish,) = _dishes(db)
    assert item.personal_food_id == dish.id
    assert dish.per_100g_calories is None
    assert dish.numbers_basis == "none"
    # A plate row it is not: its one item is the portion.
    assert meal_items.dish_portion(db, dish).numbers["calories"] == 420.4


def test_rest_create_writes_items_as_well(db, owner):
    meal = create_meal(
        MealCreate(description="омлет", meal_time=NOW, calories=300.0),
        owner.id,
        db,
    )
    (item,) = crud_meal_item.live(db, meal.id)
    assert item.name == "омлет"
    assert meal.numbers_basis == "items"


# --- the rules of the sum ---------------------------------------------------


GROUPED = {
    "food_items": [
        {"name": "рис", "groups": ["grain"]},
        {"name": "курица", "groups": ["anchor"]},
    ],
    "food_groups": ["anchor", "grain"],
    "food_groups_by_user": False,
}


def _item_meal(db, owner, *dishes):
    """An ``items`` meal made of dishes that have numbers."""
    made = [
        _dish(
            db,
            owner,
            name,
            per_100g_calories=Decimal(kcal),
            per_100g_proteins=Decimal("10.00"),
            usual_grams=100.0,
        )
        for name, kcal in dishes
    ]
    meal = _saved(
        db,
        owner,
        foods=(made[0].canonical_name,),
        calories=float(dishes[0][1]),
        portion="100г",
    )
    for dish in made[1:]:
        meal_items.add_item(db, owner.id, meal.id, food_id=dish.id)
    return meal


def test_item_change_rewrites_totals_and_every_field_at_once(db, owner):
    meal = _item_meal(db, owner, ("рис", "100.00"), ("курица", "200.00"))
    rev = meal.rev
    db.refresh(meal)
    (rice, chicken) = crud_meal_item.live(db, meal.id)
    assert meal.calories == 300.0

    change = meal_items.set_quantity(db, owner.id, chicken.id, 2.0)

    db.expire_all()
    assert change.status == meal_items.DONE
    assert meal.calories == 500.0
    assert meal.nutrients["calories"] == 500.0
    assert meal.rev > rev

    change = meal_items.remove_item(db, owner.id, rice.id)
    db.expire_all()
    assert change.status == meal_items.DONE
    assert meal.calories == 400.0
    assert meal.description == "курица"
    assert meal.nutrients["foods"] == ["курица"]
    assert [e.kind for e in _events(db)] == ["add", "qty", "remove"]


def test_day_card_and_numbers_agree_after_a_change(db, owner):
    meal = _saved(db, owner, foods=("рис", "курица"), tags=GROUPED, meal_type="Обед")
    (rice, chicken) = crud_meal_item.live(db, meal.id)
    dish = _dish(
        db, owner, "хлеб", per_100g_calories=Decimal("250.00"), usual_grams=40.0
    )

    meal_items.replace_item(db, owner.id, chicken.id, name="индейка")
    meal_items.add_item(db, owner.id, meal.id, food_id=dish.id)

    db.expire_all()
    assert meal.description == "рис, индейка, хлеб"
    assert meal.nutrients["foods"] == ["рис", "индейка", "хлеб"]
    for source in (meal.nutrients, meal.ai_analysis):
        assert [i["name"] for i in source["food_items"]] == ["рис", "индейка", "хлеб"]
        assert source["food_groups"] == ["grain"]
    facts = day_card.facts([meal], ZoneInfo("UTC"), _meal_name)
    assert "курица" not in (facts.foods or "") + (facts.anchor or "")
    assert "индейка" in facts.foods and "хлеб" in facts.foods
    assert facts.grain and "рис" in facts.grain
    assert _meal_name(meal) == "рис, индейка, хлеб"
    assert _sum([meal], "calories") == 600.0  # the plate + the bread added later


def test_groups_set_by_hand_survive_an_item_change(db, owner):
    tags = {**GROUPED, "food_groups": ["veg"], "food_groups_by_user": True}
    meal = _saved(db, owner, foods=("рис", "курица"), tags=tags)
    (_rice, chicken) = crud_meal_item.live(db, meal.id)

    meal_items.remove_item(db, owner.id, chicken.id)

    db.expire_all()
    assert meal.nutrients["food_groups"] == ["veg"]
    assert meal.nutrients["food_groups_by_user"] is True


def test_an_untagged_meal_stays_untagged_for_the_retag_run(db, owner):
    meal = _saved(db, owner, foods=("рис", "курица"))
    (_rice, chicken) = crud_meal_item.live(db, meal.id)

    meal_items.remove_item(db, owner.id, chicken.id)

    db.expire_all()
    assert "food_groups" not in (meal.ai_analysis or {})
    assert "food_groups" not in meal.nutrients
    assert meal.nutrients["foods"] == ["рис"]


def test_json_fields_are_new_dicts_and_really_persist(Session, db, owner):
    meal = _saved(db, owner, foods=("рис", "курица"), tags=GROUPED)
    (_rice, chicken) = crud_meal_item.live(db, meal.id)

    meal_items.remove_item(db, owner.id, chicken.id)

    with Session() as other:
        again = other.get(Meal, meal.id)
        assert again.nutrients["foods"] == ["рис"]
        assert [i["name"] for i in again.ai_analysis["food_items"]] == ["рис"]


def test_item_change_is_one_transaction(db, owner, monkeypatch):
    meal = _item_meal(db, owner, ("рис", "100.00"))
    (item,) = crud_meal_item.live(db, meal.id)

    def boom(*args, **kwargs):
        raise RuntimeError("no event today")

    monkeypatch.setattr(crud_record_event, "add", boom)
    with pytest.raises(RuntimeError):
        meal_items.set_quantity(db, owner.id, item.id, 2.0)

    db.expire_all()
    assert item.factor == 1
    assert meal.calories == 100.0
    assert meal.rev == 0

    monkeypatch.undo()
    # Control: the same change with the event working goes through.
    assert meal_items.set_quantity(db, owner.id, item.id, 2.0).status == meal_items.DONE
    db.expire_all()
    assert item.factor == 2 and meal.calories == 200.0


def test_plate_removal_leaves_the_totals(db, owner):
    meal = _saved(db, owner, foods=("рис", "курица"))
    (rice, chicken) = crud_meal_item.live(db, meal.id)

    meal_items.remove_item(db, owner.id, chicken.id)

    db.expire_all()
    assert meal.calories == 500.0
    assert meal.numbers_basis == "plate"
    assert meal.plate_totals["calories"] == 500.0
    assert rice.base_calories is None  # nothing was split by share
    assert meal.description == "рис"


def test_plate_has_no_quantity(db, owner):
    meal = _saved(db, owner, foods=("рис", "курица"))
    (rice, _chicken) = crud_meal_item.live(db, meal.id)

    change = meal_items.set_quantity(db, owner.id, rice.id, 2.0)

    db.expire_all()
    assert change.status == meal_items.NOT_APPLICABLE
    assert rice.factor == 1
    assert meal.calories == 500.0
    assert _events(db) == []


def test_plate_adds_only_what_was_added_later(db, owner):
    meal = _saved(db, owner, foods=("рис", "курица"))
    bread = _dish(
        db, owner, "хлеб", per_100g_calories=Decimal("250.00"), usual_grams=40.0
    )

    change = meal_items.add_item(db, owner.id, meal.id, food_id=bread.id)
    db.expire_all()
    assert meal.calories == 600.0
    assert meal.plate_totals["calories"] == 500.0

    meal_items.remove_item(db, owner.id, change.items[0].id)
    db.expire_all()
    assert meal.calories == 500.0


def test_none_stays_null_until_a_number_arrives(db, owner):
    meal = meal_items.save_meal(
        db, MealCreate(description="чай", meal_time=NOW), owner.id
    )
    assert meal.numbers_basis == "none"
    water = _dish(db, owner, "вода")

    meal_items.add_item(db, owner.id, meal.id, food_id=water.id)
    db.expire_all()
    assert meal.calories is None
    assert meal.numbers_basis == "none"

    bread = _dish(
        db, owner, "хлеб", per_100g_calories=Decimal("250.00"), usual_grams=40.0
    )
    meal_items.add_item(db, owner.id, meal.id, food_id=bread.id)
    db.expire_all()
    assert meal.calories == 100.0
    assert meal.numbers_basis == "items"


def test_a_legacy_meal_is_never_recomputed_by_an_edit_elsewhere(db, owner):
    legacy = _legacy(db, owner)
    other = _item_meal(db, owner, ("рис", "100.00"))
    (item,) = crud_meal_item.live(db, other.id)

    meal_items.set_quantity(db, owner.id, item.id, 2.0)

    db.expire_all()
    assert other.calories == 200.0  # the positive: the edit did reach its own meal
    assert legacy.calories == 333.33
    assert legacy.plate_totals is None
    assert crud_meal_item.live(db, legacy.id) == []


def test_a_null_legacy_total_stays_null(db, owner):
    legacy = _legacy(db, owner, calories=None)
    bare = _dish(db, owner, "вода")

    meal_items.add_item(db, owner.id, legacy.id, food_id=bare.id)

    db.expire_all()
    assert legacy.calories is None  # not 0, as ``_sum`` would read it
    assert legacy.proteins == 11.11


def test_first_add_on_a_legacy_meal_keeps_its_totals_to_the_kopeck(db, owner):
    tags = {
        "food_items": [{"name": "суп", "groups": ["veg"]}],
        "food_groups": ["veg"],
        "food_groups_by_user": False,
    }
    legacy = _legacy(db, owner, nutrients=tags, ai_analysis=tags)
    water = _dish(db, owner, "вода")

    change = meal_items.add_item(db, owner.id, legacy.id, food_id=water.id)

    db.expire_all()
    assert (legacy.calories, legacy.proteins, legacy.fats, legacy.carbohydrates) == (
        333.33,
        11.11,
        7.77,
        44.44,
    )
    assert legacy.plate_totals == {
        "calories": 333.33,
        "proteins": 11.11,
        "fats": 7.77,
        "carbohydrates": 44.44,
    }
    assert legacy.numbers_basis == "plate"
    (synthetic, added) = crud_meal_item.live(db, legacy.id)
    assert change.items[0].id == added.id
    assert synthetic.name == "старый обед"
    assert synthetic.numbers_source == "plate_share"
    assert synthetic.base_calories is None  # the plate holds them, never twice
    # What the old tags said stays under the old name.
    groups = {i["name"]: i["groups"] for i in legacy.nutrients["food_items"]}
    assert groups["старый обед"] == ["veg"]
    assert legacy.nutrients["food_groups"] == ["veg"]
    assert legacy.description == "старый обед, вода"


# --- quantity and grams -----------------------------------------------------


def test_half_after_double_is_half_of_the_base(db, owner):
    meal = _item_meal(db, owner, ("рис", "100.00"))
    (item,) = crud_meal_item.live(db, meal.id)

    meal_items.set_quantity(db, owner.id, item.id, 2.0)
    meal_items.set_quantity(db, owner.id, item.id, 0.5)

    db.expire_all()
    assert meal.calories == 50.0
    assert item.factor == 0.5
    assert item.base_calories == 100.0


def test_dish_decimal_numbers_do_not_raise(db, owner):
    dish = _dish(
        db,
        owner,
        "сыр",
        per_100g_calories=Decimal("333.33"),
        per_100g_proteins=Decimal("25.50"),
        usual_grams=30.0,
    )
    meal = _saved(db, owner, foods=("рис",))

    change = meal_items.add_item(db, owner.id, meal.id, food_id=dish.id)
    meal_items.set_quantity(db, owner.id, change.items[0].id, 2.0)

    db.expire_all()
    assert change.items[0].base_calories == pytest.approx(100.0, abs=0.01)
    assert isinstance(change.items[0].base_calories, float)
    assert meal.calories == pytest.approx(700.0, abs=0.01)


def test_same_quantity_twice_gives_one_event(db, owner):
    meal = _item_meal(db, owner, ("рис", "100.00"))
    (item,) = crud_meal_item.live(db, meal.id)

    first = meal_items.set_quantity(db, owner.id, item.id, 2.0)
    db.refresh(meal)
    rev = meal.rev
    second = meal_items.set_quantity(db, owner.id, item.id, 2.0)

    db.expire_all()
    assert first.status == meal_items.DONE
    assert second.status == meal_items.SAME
    assert meal_items.SAME_TOAST == "Уже так"
    assert meal.rev == rev
    assert [e.kind for e in _events(db)] == ["qty"]


def test_times_one_returns_to_the_base_from_a_typed_weight(db, owner):
    meal = _item_meal(db, owner, ("рис", "100.00"))
    (item,) = crud_meal_item.live(db, meal.id)

    meal_items.set_grams(db, owner.id, item.id, 150.0)
    db.expire_all()
    assert item.grams == 150.0 and item.factor == 1.5
    assert meal.calories == 150.0

    change = meal_items.set_quantity(db, owner.id, item.id, 1.0)
    db.expire_all()
    assert change.status == meal_items.DONE
    assert item.grams is None and item.factor == 1
    assert meal.calories == 100.0


def test_grams_with_a_base_set_the_factor(db, owner):
    _confirm_free = _saved(db, owner, foods=("рис",))
    (item,) = crud_meal_item.live(db, _confirm_free.id)
    item.base_grams = 200.0
    db.commit()

    change = meal_items.set_grams(db, owner.id, item.id, 100.0)

    db.expire_all()
    assert change.numbers_changed is True
    assert item.factor == 0.5
    assert _confirm_free.calories == 250.0
    assert item.by_user is True


def test_grams_without_a_base_take_numbers_from_the_dish(db, owner):
    meal = _saved(db, owner, foods=("рис", "курица"))
    bare = _dish(db, owner, "сыр")
    added = meal_items.add_item(db, owner.id, meal.id, food_id=bare.id).items[0]
    bare.per_100g_calories = Decimal("300.00")
    bare.numbers_basis = "per_100"
    db.commit()

    change = meal_items.set_grams(db, owner.id, added.id, 50.0)

    db.expire_all()
    assert change.numbers_changed is True
    assert added.numbers_source == "dish"
    assert added.base_grams == 100.0
    assert added.factor == 0.5
    assert added.base_calories == 300.0
    assert meal.calories == 650.0


def test_grams_on_a_line_of_the_plate_never_count_it_twice(db, owner):
    """The control is the test above: the same dish numbers, on a line added later."""
    meal = _saved(db, owner, foods=("рис", "курица"))
    (rice, _chicken) = crud_meal_item.live(db, meal.id)
    dish = db.get(PersonalFood, rice.personal_food_id)
    dish.per_100g_calories = Decimal("300.00")
    dish.numbers_basis = "per_100"
    db.commit()

    change = meal_items.set_grams(db, owner.id, rice.id, 50.0)

    db.expire_all()
    assert change.numbers_changed is False
    assert rice.grams == 50.0 and rice.base_calories is None
    assert meal.calories == 500.0


def test_grams_with_neither_store_the_grams_and_nothing_else(db, owner):
    meal = _saved(db, owner, foods=("рис", "курица"))
    (rice, _chicken) = crud_meal_item.live(db, meal.id)

    change = meal_items.set_grams(db, owner.id, rice.id, 150.0)

    db.expire_all()
    assert change.status == meal_items.DONE
    assert change.numbers_changed is False
    assert rice.grams == 150.0
    assert rice.base_calories is None
    assert meal.calories == 500.0


# --- replace and remove -----------------------------------------------------


def test_replace_keeps_grams_words_and_quantity(db, owner):
    meal = _item_meal(db, owner, ("рис", "100.00"), ("курица", "200.00"))
    (_rice, chicken) = crud_meal_item.live(db, meal.id)
    chicken.amount_text = "две штуки"
    chicken.grams = 100.0
    db.commit()
    turkey = _dish(
        db, owner, "индейка", per_100g_calories=Decimal("150.00"), usual_grams=100.0
    )

    change = meal_items.replace_item(db, owner.id, chicken.id, food_id=turkey.id)

    db.expire_all()
    new = change.items[0]
    assert new.name == "индейка"
    assert new.position == chicken.position
    assert new.amount_text == "две штуки"
    assert new.grams == 100.0
    assert new.numbers_source == "dish"
    assert chicken.removed_at is not None
    assert chicken.replaced_by_item_id == new.id
    assert meal.calories == 250.0
    assert [e.kind for e in _events(db)][-1] == "replace"


def test_replace_on_a_plate_keeps_the_totals(db, owner):
    meal = _saved(db, owner, foods=("рис", "курица"))
    (_rice, chicken) = crud_meal_item.live(db, meal.id)
    turkey = _dish(
        db, owner, "индейка", per_100g_calories=Decimal("150.00"), usual_grams=100.0
    )

    change = meal_items.replace_item(db, owner.id, chicken.id, food_id=turkey.id)

    db.expire_all()
    assert meal.calories == 500.0
    assert change.items[0].base_calories is None
    assert meal.description == "рис, индейка"


def test_replace_by_an_unknown_name_makes_a_new_dish(db, owner):
    meal = _item_meal(db, owner, ("рис", "100.00"))
    (rice,) = crud_meal_item.live(db, meal.id)

    change = meal_items.replace_item(db, owner.id, rice.id, name="гречка")

    db.expire_all()
    new = change.items[0]
    assert new.numbers_source == "none"
    assert db.get(PersonalFood, new.personal_food_id).canonical_name == "гречка"
    assert meal.calories is None  # nothing known is left to sum


def test_last_dish_removal_soft_deletes_and_keeps_everything(db, owner):
    meal = _item_meal(db, owner, ("рис", "100.00"))
    (item,) = crud_meal_item.live(db, meal.id)

    change = meal_items.remove_item(db, owner.id, item.id)

    db.expire_all()
    assert change.status == meal_items.DELETED
    assert crud_meal.get(db, meal.id) is None
    kept = crud_meal.get_any(db, meal.id)
    assert kept.calories == 100.0
    assert kept.description == "рис"
    assert crud_meal_item.live(db, meal.id) == [item]
    assert [e.kind for e in _events(db)] == ["delete"]

    undone = meal_items.undo(db, owner.id, change.event.id)
    db.expire_all()
    assert undone.status == meal_items.DONE
    assert crud_meal.get(db, meal.id) is not None
    (event, back) = _events(db)
    assert (back.kind, back.undoes_event_id) == ("restore", event.id)


def test_a_foreign_id_changes_nothing_and_the_owners_does(db, owner):
    stranger = User(telegram_id=77, username="stranger")
    db.add(stranger)
    db.commit()
    meal = _item_meal(db, owner, ("рис", "100.00"))
    (item,) = crud_meal_item.live(db, meal.id)

    for call in (
        lambda: meal_items.set_quantity(db, stranger.id, item.id, 2.0),
        lambda: meal_items.set_grams(db, stranger.id, item.id, 50.0),
        lambda: meal_items.remove_item(db, stranger.id, item.id),
        lambda: meal_items.replace_item(db, stranger.id, item.id, name="x"),
        lambda: meal_items.add_item(db, stranger.id, meal.id, name="x"),
    ):
        assert call().status == meal_items.STALE
    db.expire_all()
    assert meal.calories == 100.0
    assert _events(db) == []

    assert meal_items.set_quantity(db, owner.id, item.id, 2.0).status == meal_items.DONE


def test_a_stale_item_changes_nothing(db, owner):
    meal = _item_meal(db, owner, ("рис", "100.00"), ("курица", "200.00"))
    (_rice, chicken) = crud_meal_item.live(db, meal.id)
    meal_items.remove_item(db, owner.id, chicken.id)
    events = len(_events(db))

    change = meal_items.set_quantity(db, owner.id, chicken.id, 2.0)

    assert change.status == meal_items.STALE
    assert len(_events(db)) == events


def test_a_change_on_a_deleted_meal_changes_nothing(db, owner):
    meal = _item_meal(db, owner, ("рис", "100.00"), ("курица", "200.00"))
    (rice, chicken) = crud_meal_item.live(db, meal.id)
    assert meal_items.delete_meal(db, owner.id, meal.id).status == meal_items.DELETED

    assert (
        meal_items.set_quantity(db, owner.id, rice.id, 2.0).status == meal_items.STALE
    )
    assert (
        meal_items.add_item(db, owner.id, meal.id, name="x").status == meal_items.STALE
    )
    assert meal_items.delete_meal(db, owner.id, meal.id).status == meal_items.STALE


def test_a_foreign_dish_cannot_be_added(db, owner):
    stranger = User(telegram_id=77, username="stranger")
    db.add(stranger)
    db.commit()
    theirs = _dish(db, stranger, "чужое", per_100g_calories=Decimal("9.00"))
    meal = _saved(db, owner)

    assert (
        meal_items.add_item(db, owner.id, meal.id, food_id=theirs.id).status
        == meal_items.STALE
    )
    assert len(crud_meal_item.live(db, meal.id)) == 1


# --- undo -------------------------------------------------------------------


def test_undo_restores_exactly_and_can_itself_be_undone(db, owner):
    meal = _item_meal(db, owner, ("рис", "100.00"), ("курица", "200.00"))
    (_rice, chicken) = crud_meal_item.live(db, meal.id)

    change = meal_items.remove_item(db, owner.id, chicken.id)
    db.expire_all()
    assert meal.calories == 100.0

    back = meal_items.undo(db, owner.id, change.event.id)
    db.expire_all()
    assert back.status == meal_items.DONE
    assert meal.calories == 300.0
    assert meal.description == "рис, курица"
    assert chicken.removed_at is None
    assert back.event.undoes_event_id == change.event.id

    again = meal_items.undo(db, owner.id, back.event.id)
    db.expire_all()
    assert again.status == meal_items.DONE
    assert meal.calories == 100.0


def test_undo_guard_refuses_after_a_later_change(db, owner):
    meal = _item_meal(db, owner, ("рис", "100.00"), ("курица", "200.00"))
    (rice, chicken) = crud_meal_item.live(db, meal.id)
    old = meal_items.set_quantity(db, owner.id, chicken.id, 2.0)

    # Control: with nothing planted this undo would apply; see the test above.
    meal_items.set_quantity(db, owner.id, rice.id, 2.0)  # the planted later change
    db.expire_all()
    assert meal.calories == 600.0
    events = len(_events(db))

    refused = meal_items.undo(db, owner.id, old.event.id)

    db.expire_all()
    assert refused.status == meal_items.REFUSED
    assert meal_items.UNDO_REFUSED == (
        "<i>С тех пор запись менялась, поэтому возврат не применён.</i>"
    )
    assert meal.calories == 600.0
    assert chicken.factor == 2
    assert len(_events(db)) == events


def test_undoing_twice_is_refused_the_second_time(db, owner):
    meal = _item_meal(db, owner, ("рис", "100.00"), ("курица", "200.00"))
    (_rice, chicken) = crud_meal_item.live(db, meal.id)
    change = meal_items.set_quantity(db, owner.id, chicken.id, 2.0)

    assert meal_items.undo(db, owner.id, change.event.id).status == meal_items.DONE
    assert meal_items.undo(db, owner.id, change.event.id).status == meal_items.REFUSED
    db.expire_all()
    assert meal.calories == 300.0


def test_undo_of_a_legacy_first_add_gives_the_legacy_meal_back(db, owner):
    legacy = _legacy(db, owner)
    water = _dish(db, owner, "вода")
    change = meal_items.add_item(db, owner.id, legacy.id, food_id=water.id)

    meal_items.undo(db, owner.id, change.event.id)

    db.expire_all()
    assert legacy.plate_totals is None
    assert legacy.calories == 333.33
    assert legacy.description == "старый обед"
    assert crud_meal_item.live(db, legacy.id) == []


def test_foreign_undo_changes_nothing(db, owner):
    stranger = User(telegram_id=77, username="stranger")
    db.add(stranger)
    db.commit()
    meal = _item_meal(db, owner, ("рис", "100.00"))
    (item,) = crud_meal_item.live(db, meal.id)
    change = meal_items.set_quantity(db, owner.id, item.id, 2.0)

    assert meal_items.undo(db, stranger.id, change.event.id).status == meal_items.STALE
    db.expire_all()
    assert meal.calories == 200.0


# --- the past stays ---------------------------------------------------------


def test_an_item_keeps_its_copy_after_a_dish_upsert(db, owner):
    meal = _saved(db, owner, foods=("рис",))
    (item,) = crud_meal_item.live(db, meal.id)

    crud_personal_food.upsert(
        db, user_id=owner.id, canonical_name="рис", per_100g_calories=999.0
    )

    db.expire_all()
    dish = db.get(PersonalFood, item.personal_food_id)
    assert float(dish.per_100g_calories) == 999.0  # the positive: the dish did move
    assert item.name == "рис"
    assert item.base_calories == 500.0
    assert meal.calories == 500.0


def test_numbers_typed_by_a_person_survive_upsert(db, owner):
    typed = _dish(
        db,
        owner,
        "рис",
        per_100g_calories=Decimal("100.00"),
        numbers_by_user=True,
        numbers_quality="user",
    )

    crud_personal_food.upsert(
        db,
        user_id=owner.id,
        canonical_name="рис",
        per_100g_calories=999.0,
        resolution_source="barcode_off",
    )

    db.refresh(typed)
    assert float(typed.per_100g_calories) == 100.0
    assert typed.numbers_quality == "user"


# --- the free exact-name path, through the real handler ---------------------


def _meal_text_handler():
    """The callback the running bot calls for text at the meal step."""
    application = tg.create_bot_application()
    handlers = [h for group in application.handlers.values() for h in group]
    (conv,) = [h for h in handlers if isinstance(h, ConversationHandler)]
    (handler,) = [
        h
        for h in conv.states[tg.ADDING_MEAL]
        if isinstance(h, MessageHandler) and h.callback is tg.process_meal_input
    ]
    return handler.callback


def _send(text, **user_data):
    """Text at the meal step; the model call is a fake that must stay untouched."""
    update, message = _update(text)
    context = SimpleNamespace(user_data={"meal_time": NOW, **user_data})
    analysis = AsyncMock(return_value=tg.CONFIRMING_MEAL)
    with patch.object(tg, "_run_meal_analysis", analysis):
        state = asyncio.run(_meal_text_handler()(update, context))
    return state, message, analysis, context


def test_exact_name_is_written_at_once_with_no_model(Session, db, owner):
    dish = _dish(
        db,
        owner,
        "Омлет",
        per_100g_calories=Decimal("150.00"),
        per_100g_proteins=Decimal("12.50"),
        usual_grams=200.0,
    )

    state, message, analysis, context = _send("омлет", meal_type="Завтрак")

    analysis.assert_not_called()
    assert state == tg.CHOOSING_ACTION
    assert message.replies == ["Записано: Омлет."]
    assert context.user_data == {}
    meal = _only_meal(db)
    (item,) = crud_meal_item.live(db, meal.id)
    assert meal.meal_type == "Завтрак"
    assert meal.numbers_basis == "items"
    assert meal.calories == 300.0
    assert meal.proteins == 25.0
    assert meal.description == "Омлет"
    assert meal.resolution_source == "exact_name"
    assert meal.nutrients["foods"] == ["Омлет"]
    assert item.personal_food_id == dish.id
    assert item.numbers_source == "dish"
    assert item.base_grams == 200.0
    db.refresh(dish)
    assert dish.times_used == 2


def test_every_comma_part_matching_is_one_meal_of_dishes(Session, db, owner):
    _dish(db, owner, "кофе", per_100g_calories=Decimal("2.00"), usual_grams=200.0)
    _dish(db, owner, "печенье", per_100g_calories=Decimal("450.00"), usual_grams=20.0)

    _state, message, analysis, _context = _send("Кофе, печенье")

    analysis.assert_not_called()
    assert message.replies == ["Записано: кофе и печенье."]
    meal = _only_meal(db)
    assert meal.numbers_basis == "items"
    assert meal.calories == 94.0
    assert [i.name for i in crud_meal_item.live(db, meal.id)] == ["кофе", "печенье"]


def test_three_names_read_as_a_list(Session, db, owner):
    for name in ("кофе", "печенье", "сыр"):
        _dish(db, owner, name)

    _state, message, _analysis, _context = _send("кофе, печенье, сыр")

    assert message.replies == ["Записано: кофе, печенье и сыр."]


def test_whole_text_with_a_comma_can_be_a_dish(Session, db, owner):
    _dish(db, owner, "рис, жареный")

    _state, message, analysis, _context = _send("рис, жареный")

    analysis.assert_not_called()
    assert message.replies == ["Записано: рис, жареный."]


@pytest.mark.parametrize("text", ["омлет с сыром", "омлет, сыр", "омлет,", "2 омлета"])
def test_not_an_exact_match_goes_to_the_model(Session, db, owner, text):
    _dish(db, owner, "омлет")

    _state, _message, analysis, _context = _send(text)

    analysis.assert_called_once()
    assert db.scalars(select(Meal)).all() == []


def test_another_persons_dish_is_not_a_match(Session, db, owner):
    stranger = User(telegram_id=77, username="stranger")
    db.add(stranger)
    db.commit()
    _dish(db, stranger, "омлет")

    _state, _message, analysis, _context = _send("омлет")

    analysis.assert_called_once()


def test_an_unverified_row_is_copied_and_says_so(Session, db, owner):
    _dish(
        db,
        owner,
        "омлет",
        per_100g_calories=Decimal("150.00"),
        usual_grams=100.0,
        numbers_quality="unverified",
    )

    _send("омлет")

    meal = _only_meal(db)
    assert meal.calories == 150.0
    assert meal.resolution_signals["numbers_unverified"] is True
    assert meal_items.numbers_tag(meal) == "из твоих блюд, не проверено"


def test_a_verified_row_is_only_from_the_dishes(Session, db, owner):
    _dish(
        db,
        owner,
        "йогурт",
        per_100g_calories=Decimal("97.00"),
        usual_grams=100.0,
        numbers_quality="barcode",
    )

    _send("йогурт")

    meal = _only_meal(db)
    assert meal.resolution_signals["numbers_unverified"] is False
    assert meal_items.numbers_tag(meal) == "из твоих блюд"


def test_a_deleted_meal_does_not_feed_the_next_saving_by_name(Session, db, owner):
    """A meal a person threw away is no portion, through the real text handler."""
    _send_first = _saved(db, owner, foods=("сырники",), calories=900.0)
    item = crud_meal_item.live(db, _send_first.id)[0]
    dish = db.get(PersonalFood, item.personal_food_id)
    assert meal_items.dish_portion(db, dish).numbers["calories"] == 900.0  # positive
    meal_items.delete_meal(db, owner.id, _send_first.id)

    _state, message, analysis, _context = _send("сырники")

    analysis.assert_not_called()
    assert message.replies == ["Записано: сырники."]
    meal = db.scalars(select(Meal).where(Meal.resolution_source == "exact_name")).one()
    assert meal.calories is None and meal.numbers_basis == "none"


def test_a_kept_meal_still_feeds_the_next_saving_by_name(Session, db, owner):
    # Control for the test above: the same steps with the meal left alone.
    _saved(db, owner, foods=("сырники",), calories=900.0)

    _send("сырники")

    meal = db.scalars(select(Meal).where(Meal.resolution_source == "exact_name")).one()
    assert meal.calories == 900.0


def test_a_plate_row_is_never_served_as_numbers(Session, db, owner):
    source = _legacy(
        db,
        owner,
        description="рис, курица",
        nutrients={"foods": ["рис", "курица"]},
    )
    row = _dish(
        db,
        owner,
        "рис",
        meal_id=source.id,
        per_100g_calories=Decimal("333.00"),
        usual_grams=100.0,
    )
    assert meal_items.dish_portion(db, row).numbers is None

    _state, message, analysis, _context = _send("рис")

    analysis.assert_not_called()
    assert message.replies == ["Записано: рис."]
    meal = db.scalars(select(Meal).where(Meal.resolution_source == "exact_name")).one()
    (item,) = crud_meal_item.live(db, meal.id)
    assert item.numbers_source == "none"
    assert item.base_calories is None
    assert meal.calories is None
    assert meal.numbers_basis == "none"


def test_control_the_same_row_from_a_one_food_meal_is_served(Session, db, owner):
    source = _legacy(db, owner, description="рис", nutrients={"foods": ["рис"]})
    row = _dish(
        db,
        owner,
        "рис",
        meal_id=source.id,
        per_100g_calories=Decimal("333.00"),
        usual_grams=100.0,
    )

    assert meal_items.dish_portion(db, row).numbers["calories"] == 333.0


def test_portion_is_the_usual_one_else_the_last_one(db, owner):
    meal = _saved(db, owner, foods=("рис",))
    (item,) = crud_meal_item.live(db, meal.id)
    item.base_grams = 250.0
    db.commit()
    dish = db.get(PersonalFood, item.personal_food_id)

    # No numbers on the dish and no usual portion: the last one, as recorded.
    portion = meal_items.dish_portion(db, dish)
    assert portion.numbers["calories"] == 500.0
    assert portion.grams == 250.0

    # With numbers on the dish the last portion's grams count at today's numbers.
    dish.per_100g_calories = Decimal("100.00")
    dish.numbers_basis = "per_100"
    db.commit()
    assert meal_items.dish_portion(db, dish).numbers["calories"] == 250.0

    # The usual portion comes first.
    dish.usual_grams = 50.0
    db.commit()
    assert meal_items.dish_portion(db, dish).numbers["calories"] == 50.0


def test_the_synthetic_plate_share_is_never_a_last_portion(db, owner):
    dish = _dish(db, owner, "суп", per_100g_calories=None, numbers_basis="none")
    legacy = _legacy(db, owner, description="суп")
    meal_items.add_item(db, owner.id, legacy.id, name="вода")
    (synthetic, _water) = crud_meal_item.live(db, legacy.id)
    assert synthetic.numbers_source == "plate_share"
    assert synthetic.personal_food_id == dish.id
    assert synthetic.base_calories is None

    assert meal_items.dish_portion(db, dish).numbers is None


def test_a_legacy_description_never_becomes_a_dish(db, owner):
    legacy = _legacy(db, owner, description="суп, котлета и компот")

    meal_items.add_item(db, owner.id, legacy.id, name="вода")

    (synthetic, _water) = crud_meal_item.live(db, legacy.id)
    assert synthetic.personal_food_id is None
    assert (
        crud_personal_food.find_by_name(db, owner.id, "суп, котлета и компот") is None
    )
    assert crud_personal_food.find_by_name(db, owner.id, "вода") is not None


def test_free_path_marks_the_inbound_row_so_reprocess_does_not_pay(Session, db, owner):
    from app.models.inbound_message import InboundMessage

    _dish(db, owner, "омлет")

    _send("омлет")

    (row,) = db.scalars(select(InboundMessage)).all()
    assert row.status == "analyzed"


def test_tags_carry_from_the_last_time_the_dish_was_eaten(Session, db, owner):
    tags = {
        "food_items": [{"name": "рис", "groups": ["grain"]}],
        "food_groups": ["grain"],
        "food_groups_by_user": False,
    }
    _saved(db, owner, foods=("рис",), tags=tags)

    _send("рис")

    db.expire_all()
    meal = db.scalars(select(Meal).where(Meal.resolution_source == "exact_name")).one()
    assert meal.nutrients["food_groups"] == ["grain"]
    assert meal.ai_analysis["food_items"] == [{"name": "рис", "groups": ["grain"]}]


def test_an_unknown_dish_leaves_the_meal_untagged_for_the_retag_run(Session, db, owner):
    _dish(db, owner, "рис")

    _send("рис")

    meal = _only_meal(db)
    assert "food_groups" not in (meal.ai_analysis or {})
    assert "food_groups" not in meal.nutrients


def test_favourite_undo_still_works_with_items(db, owner):
    fav = FavoriteMeal(user_id=owner.id, name="сырники", calories=420.0)
    db.add(fav)
    db.commit()
    favorites.handle_callback(db, 42, f"{favorites.CALLBACK_PREFIX}l:{fav.id}:n")
    meal = _only_meal(db)

    screen = favorites.handle_callback(
        db, 42, f"{favorites.CALLBACK_PREFIX}u:{meal.id}"
    )

    assert "Отменено" in screen.text
    # Soft, as every delete (3.3); the items stay for the way back.
    assert crud_meal.get(db, meal.id) is None
    assert _only_meal(db).deleted_at is not None
    assert all(item.removed_at is None for item in db.scalars(select(MealItem)))
