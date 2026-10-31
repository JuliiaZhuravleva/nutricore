"""Slice N's data layer on real rows: items, events, soft delete, the dish guard."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.v1.meals import delete_meal, read_meal, update_meal
from app.crud.crud_meal import crud_meal
from app.crud.crud_meal_item import crud_meal_item
from app.crud.crud_personal_food import crud_personal_food
from app.crud.crud_record_event import crud_record_event
from app.crud.crud_user import crud_user
from app.db.base import Base
from app.models import Meal, MealItem, PersonalFood, RecordEvent
from app.schemas.meal import MealCreate, MealUpdate
from app.schemas.user import UserCreate
from app.services import meal_items

NOW = datetime(2026, 10, 26, 13, 20, tzinfo=UTC)


def _naive(value: datetime) -> datetime:
    """SQLite drops tzinfo on read-back."""
    return value.replace(tzinfo=None)


@pytest.fixture
def user(db_session):
    return crud_user.create(db_session, obj_in=UserCreate(telegram_id=555001))


def _meal(db, user, **fields) -> Meal:
    fields.setdefault("meal_time", NOW)
    fields.setdefault("description", "омлет")
    return crud_meal.create(db, MealCreate(**fields), user.id)


def _soft_delete(db, meal) -> None:
    crud_meal.update(db, meal, MealUpdate(deleted_at=NOW))


@pytest.fixture
def real_session():
    """Foreign keys on and a real commit: conftest's session has neither.

    The conftest session lives inside an outer transaction, so it cannot tell a
    commit from a flush, and SQLite ignores ON DELETE unless the pragma is set.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _foreign_keys_on(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


# ---------------------------------------------------------------------------
# meals: defaults and field lists
# ---------------------------------------------------------------------------


def test_a_new_meal_gets_the_column_defaults(db_session, user):
    meal = _meal(db_session, user)

    assert meal.time_precision == "exact"
    assert meal.time_source == "sent"
    assert meal.time_phrase is None
    assert meal.numbers_basis == "plate"
    assert meal.plate_totals is None
    assert meal.deleted_at is None
    assert meal.rev == 0


def test_sent_at_falls_back_to_now_when_not_given(db_session, user):
    before = _naive(datetime.now(UTC)) - timedelta(seconds=5)
    meal = _meal(db_session, user)
    assert _naive(meal.sent_at) >= before


def test_create_stores_every_field_its_schema_accepts(db_session, user):
    """The constructor lists columns by hand: a forgotten one is silently dropped."""
    given = {
        "sent_at": datetime(2026, 10, 26, 13, 21, tzinfo=UTC),
        "time_precision": "approx",
        "time_source": "said",
        "time_phrase": "час назад",
        "numbers_basis": "items",
        "plate_totals": {"calories": 500},
    }
    meal = _meal(db_session, user, **given)

    assert _naive(meal.sent_at) == _naive(given["sent_at"])
    for field in set(given) - {"sent_at"}:
        assert getattr(meal, field) == given[field], field
    # And the schema has no field this list forgets.
    declared = set(MealCreate.model_fields)
    row_columns = {c.name for c in Meal.__table__.columns}
    assert declared <= row_columns
    assert row_columns - declared == {
        "id",
        "user_id",
        "created_at",
        "updated_at",
        "deleted_at",
        "rev",
    }


def test_plate_totals_none_is_sql_null_not_json_null(db_session, user):
    meal = _meal(db_session, user, plate_totals=None)
    found = db_session.execute(
        text("SELECT plate_totals IS NULL FROM meals WHERE id = :id"), {"id": meal.id}
    ).scalar()
    assert found == 1


def test_update_applies_the_new_fields(db_session, user):
    meal = _meal(db_session, user)
    given = {
        "time_precision": "day_only",
        "time_source": "edited",
        "time_phrase": "вчера",
        "numbers_basis": "none",
        "plate_totals": {"calories": 1},
    }
    crud_meal.update(db_session, meal, MealUpdate(**given))
    db_session.refresh(meal)
    for field, value in given.items():
        assert getattr(meal, field) == value, field


def test_update_never_moves_sent_at_or_takes_rev_from_outside(db_session, user):
    sent = datetime(2026, 10, 26, 13, 21, tzinfo=UTC)
    meal = _meal(db_session, user, sent_at=sent)

    crud_meal.update(
        db_session,
        meal,
        MealUpdate(sent_at=NOW, rev=99, time_phrase="утром"),
    )
    db_session.refresh(meal)

    assert _naive(meal.sent_at) == _naive(sent)
    assert meal.rev == 1
    assert meal.time_phrase == "утром"  # control: the update itself went through


@pytest.mark.parametrize("field", ["time_precision", "time_source", "numbers_basis"])
def test_a_not_null_column_cannot_be_nulled_or_misspelled(field):
    with pytest.raises(ValidationError):
        MealUpdate(**{field: None})
    with pytest.raises(ValidationError):
        MealCreate(meal_time=NOW, **{field: "nonsense"})


def test_rev_counts_changes_only(db_session, user):
    meal = _meal(db_session, user, description="омлет")

    crud_meal.update(db_session, meal, MealUpdate(description="омлет"))
    assert meal.rev == 0  # same value: not a change

    crud_meal.update(db_session, meal, MealUpdate(description="рис"))
    assert meal.rev == 1

    crud_meal.update(db_session, meal, MealUpdate(description="рис", calories=5))
    assert meal.rev == 2


# ---------------------------------------------------------------------------
# Soft delete: get vs get_any, and the list filters
# ---------------------------------------------------------------------------


def test_get_hides_a_deleted_meal_and_get_any_finds_it(db_session, user):
    meal = _meal(db_session, user)
    assert crud_meal.get(db_session, meal.id).id == meal.id  # known positive

    _soft_delete(db_session, meal)

    assert crud_meal.get(db_session, meal.id) is None
    assert crud_meal.get_any(db_session, meal.id).id == meal.id
    assert crud_meal.get_any(db_session, meal.id + 1000) is None


@pytest.mark.parametrize(
    "read",
    [
        lambda db, user: crud_meal.get_multi(db),
        lambda db, user: crud_meal.get_for_export(db),
        lambda db, user: crud_meal.get_for_export(
            db, since=datetime(2026, 1, 1, tzinfo=UTC)
        ),
        lambda db, user: crud_meal.get_user_meals(db, user.id),
        lambda db, user: crud_meal.get_user_meals_between(
            db, user.id, NOW - timedelta(days=1), NOW + timedelta(days=1)
        ),
    ],
    ids=["get_multi", "export", "export_since", "user_meals", "between"],
)
def test_lists_leave_out_a_deleted_meal(db_session, user, read):
    kept = _meal(db_session, user, description="живой")
    gone = _meal(db_session, user, description="удалённый")
    assert {m.id for m in read(db_session, user)} >= {kept.id, gone.id}  # positive

    _soft_delete(db_session, gone)

    ids = {m.id for m in read(db_session, user)}
    assert kept.id in ids
    assert gone.id not in ids


def test_the_rest_routes_answer_404_on_a_deleted_meal(db_session, user):
    meal = _meal(db_session, user)
    assert read_meal(meal.id, db_session).id == meal.id  # known positive

    _soft_delete(db_session, meal)

    for call in (
        lambda: read_meal(meal.id, db_session),
        lambda: update_meal(meal.id, MealUpdate(description="x"), db_session),
        lambda: delete_meal(meal.id, db_session),
    ):
        with pytest.raises(HTTPException) as caught:
            call()
        assert caught.value.status_code == 404
    assert crud_meal.get_any(db_session, meal.id).description == "омлет"


# ---------------------------------------------------------------------------
# meal_items
# ---------------------------------------------------------------------------


def test_an_item_starts_with_its_origin_and_defaults(db_session, user):
    meal = _meal(db_session, user)
    food = crud_personal_food.upsert(db_session, user_id=user.id, canonical_name="рис")

    item = crud_meal_item.add(
        db_session, meal_id=meal.id, name="рис", personal_food_id=food.id
    )

    assert item.origin_meal_id == meal.id
    assert item.original_food_id == food.id
    assert item.factor == 1
    assert item.position == 0
    assert item.numbers_source == "none"
    assert item.by_user is False
    assert item.removed_at is None
    assert item.created_at is not None
    assert item.updated_at is not None


def test_an_item_name_is_cut_to_its_column(db_session, user):
    meal = _meal(db_session, user)
    item = crud_meal_item.add(db_session, meal_id=meal.id, name="  " + "я" * 130)
    assert item.name == "я" * 120
    assert MealItem.__table__.c.name.type.length == 120


def test_live_items_are_in_order_and_leave_out_removed(db_session, user):
    meal = _meal(db_session, user)
    other = _meal(db_session, user)
    second = crud_meal_item.add(db_session, meal_id=meal.id, name="б", position=1)
    first = crud_meal_item.add(db_session, meal_id=meal.id, name="а", position=0)
    gone = crud_meal_item.add(db_session, meal_id=meal.id, name="в", position=2)
    crud_meal_item.add(db_session, meal_id=other.id, name="чужой")

    assert [i.name for i in crud_meal_item.live(db_session, meal.id)] == [
        "а",
        "б",
        "в",
    ]  # known positive: "в" is there until it is removed

    gone.removed_at = NOW
    db_session.flush()

    assert [i.id for i in crud_meal_item.live(db_session, meal.id)] == [
        first.id,
        second.id,
    ]
    assert crud_meal_item.get(db_session, gone.id).id == gone.id


def test_item_and_event_writes_flush_and_leave_the_commit_to_the_caller(real_session):
    """An item change and its totals must land in one transaction (section 3.1)."""
    db = real_session
    user = crud_user.create(db, obj_in=UserCreate(telegram_id=555002))
    meal = _meal(db, user)  # crud_meal.create commits: the known positive
    meal_id, user_id = meal.id, user.id

    item = crud_meal_item.add(db, meal_id=meal_id, name="рис")
    logged = crud_record_event.add(db, user_id=user_id, kind="add", meal_id=meal_id)
    assert item.id is not None and logged.id is not None

    db.rollback()

    assert db.get(Meal, meal_id) is not None
    assert db.scalars(select(MealItem)).all() == []
    assert db.scalars(select(RecordEvent)).all() == []


def test_deleting_a_meal_row_takes_its_items(real_session):
    db = real_session
    user = crud_user.create(db, obj_in=UserCreate(telegram_id=555003))
    meal, other = _meal(db, user), _meal(db, user)
    crud_meal_item.add(db, meal_id=meal.id, name="рис")
    kept = crud_meal_item.add(db, meal_id=other.id, name="суп")
    db.commit()

    crud_meal.remove(db, meal.id)

    assert [i.id for i in db.scalars(select(MealItem)).all()] == [kept.id]


def test_soft_delete_keeps_the_items(real_session):
    db = real_session
    user = crud_user.create(db, obj_in=UserCreate(telegram_id=555004))
    meal = _meal(db, user)
    crud_meal_item.add(db, meal_id=meal.id, name="рис")
    db.commit()

    _soft_delete(db, meal)

    assert len(crud_meal_item.live(db, meal.id)) == 1


def test_deleting_a_dish_unlinks_the_item_and_keeps_its_copy(real_session):
    db = real_session
    user = crud_user.create(db, obj_in=UserCreate(telegram_id=555005))
    meal = _meal(db, user)
    food = crud_personal_food.upsert(db, user_id=user.id, canonical_name="рис")
    food_id = food.id
    item = crud_meal_item.add(
        db,
        meal_id=meal.id,
        name="рис",
        personal_food_id=food_id,
        base_calories=200.0,
    )
    db.commit()
    assert item.personal_food_id == food_id  # known positive

    db.delete(food)
    db.commit()
    db.refresh(item)

    assert item.personal_food_id is None
    assert item.original_food_id == food_id
    assert item.name == "рис"
    assert item.base_calories == 200.0


def test_an_item_keeps_its_name_and_numbers_after_a_dish_upsert(db_session, user):
    food = crud_personal_food.upsert(
        db_session, user_id=user.id, canonical_name="рис", per_100g_calories=100.0
    )
    meal = _meal(db_session, user)
    item = crud_meal_item.add(
        db_session,
        meal_id=meal.id,
        name="рис",
        personal_food_id=food.id,
        base_calories=150.0,
        base_grams=150.0,
    )

    food = crud_personal_food.upsert(
        db_session, user_id=user.id, canonical_name="РИС", per_100g_calories=300.0
    )
    db_session.refresh(item)

    assert float(food.per_100g_calories) == 300.0  # control: the dish did change
    assert item.name == "рис"
    assert item.base_calories == 150.0


# ---------------------------------------------------------------------------
# record_events
# ---------------------------------------------------------------------------


def test_an_event_stores_what_it_was_given(db_session, user):
    meal = _meal(db_session, user)
    first = crud_record_event.add(
        db_session,
        user_id=user.id,
        kind="qty",
        meal_id=meal.id,
        before={"factor": 1.0},
        after={"factor": 0.5},
    )
    undo = crud_record_event.add(
        db_session,
        user_id=user.id,
        kind="qty",
        meal_id=meal.id,
        before={"factor": 0.5},
        after={"factor": 1.0},
        undoes_event_id=first.id,
    )
    db_session.refresh(undo)

    found = crud_record_event.get(db_session, first.id)
    assert found.before == {"factor": 1.0}
    assert found.after == {"factor": 0.5}
    assert undo.undoes_event_id == first.id
    assert undo.created_at is not None
    assert undo.updated_at is not None
    assert crud_record_event.get(db_session, undo.id + 1000) is None


def test_an_events_empty_json_is_sql_null(db_session, user):
    logged = crud_record_event.add(db_session, user_id=user.id, kind="delete")
    found = db_session.execute(
        text(
            'SELECT "before" IS NULL AND "after" IS NULL FROM record_events '
            "WHERE id = :id"
        ),
        {"id": logged.id},
    ).scalar()
    assert found == 1


def test_an_unknown_event_kind_is_refused(db_session, user):
    with pytest.raises(ValueError, match="kindness"):
        crud_record_event.add(db_session, user_id=user.id, kind="kindness")


def test_events_outlive_a_hard_deleted_meal_and_dish(real_session):
    db = real_session
    user = crud_user.create(db, obj_in=UserCreate(telegram_id=555006))
    meal = _meal(db, user)
    food = crud_personal_food.upsert(db, user_id=user.id, canonical_name="рис")
    logged = crud_record_event.add(
        db, user_id=user.id, kind="add", meal_id=meal.id, food_id=food.id
    )
    db.commit()
    assert logged.meal_id == meal.id  # known positive

    crud_meal.remove(db, meal.id)
    db.delete(food)
    db.commit()
    db.refresh(logged)

    assert logged.meal_id is None
    assert logged.food_id is None


# ---------------------------------------------------------------------------
# personal_foods
# ---------------------------------------------------------------------------


def test_a_new_dish_gets_the_column_defaults(db_session, user):
    food = crud_personal_food.upsert(db_session, user_id=user.id, canonical_name="рис")

    assert food.usual_grams is None
    assert food.usual_grams_by_user is False
    assert food.numbers_basis == "none"  # no macros were given: it says so
    assert food.numbers_quality == "unverified"
    assert food.numbers_by_user is False


MACROS = {
    "per_100g_calories": 111.0,
    "per_100g_proteins": 11.0,
    "per_100g_fats": 1.0,
    "per_100g_carbs": 21.0,
}
NEW_MACROS = {key: value * 2 for key, value in MACROS.items()}


def _dish(db, user, **fields) -> PersonalFood:
    food = crud_personal_food.upsert(
        db, user_id=user.id, canonical_name="рис", **MACROS
    )
    for key, value in fields.items():
        setattr(food, key, value)
    db.flush()
    return food


def test_an_ordinary_upsert_still_overwrites_macros(db_session, user):
    """Known positive for the guard below."""
    _dish(db_session, user)

    food = crud_personal_food.upsert(
        db_session, user_id=user.id, canonical_name="рис", **NEW_MACROS
    )

    for key, value in NEW_MACROS.items():
        assert float(getattr(food, key)) == value, key


def test_upsert_leaves_macros_a_person_typed(db_session, user):
    _dish(db_session, user, numbers_by_user=True)

    food = crud_personal_food.upsert(
        db_session,
        user_id=user.id,
        canonical_name="рис",
        meal_id=None,
        barcode="4600000000000",
        **NEW_MACROS,
    )

    for key, value in MACROS.items():
        assert float(getattr(food, key)) == value, key
    # Only the macros are protected: the confirmation is still counted.
    assert food.times_used == 2
    assert food.barcode == "4600000000000"
    assert food.numbers_by_user is True


# --- REST: a delete is soft, and the totals belong to the items -------------


def _with_items(db_session, user):
    return meal_items.save_meal(
        db_session,
        MealCreate(
            meal_time=NOW,
            description="омлет",
            calories=300.0,
            nutrients={"foods": ["омлет"], "calories": 300.0},
        ),
        user.id,
    )


def test_rest_delete_is_soft_and_the_export_learns_of_it(db_session, user):
    meal = _with_items(db_session, user)

    answer = delete_meal(meal.id, db_session)

    assert answer.id == meal.id and answer.deleted_at is not None
    kept = crud_meal.get_any(db_session, meal.id)
    assert kept is not None and kept.calories == 300.0
    assert crud_meal_item.live(db_session, meal.id)  # the items stay, for the undo
    pull = crud_meal.get_for_export(db_session, updated_since=NOW - timedelta(days=1))
    assert meal.id in [row.id for row in pull]
    kinds = db_session.scalars(
        select(RecordEvent.kind).where(RecordEvent.meal_id == meal.id)
    ).all()
    assert "delete" in kinds
    with pytest.raises(HTTPException) as caught:
        delete_meal(meal.id, db_session)
    assert caught.value.status_code == 404


@pytest.mark.parametrize(
    "fields",
    [
        {"calories": 1.0},
        {"proteins": 1.0},
        {"fats": 1.0},
        {"carbohydrates": 1.0},
        {"description": "x"},
        {"nutrients": {"foods": ["x"]}},
        {"ai_analysis": {"food_groups": []}},
        {"numbers_basis": "none"},
        {"plate_totals": {"calories": 1}},
    ],
)
def test_rest_update_refuses_what_the_items_own(db_session, user, fields):
    meal = _with_items(db_session, user)
    rev = meal.rev

    with pytest.raises(HTTPException) as caught:
        update_meal(meal.id, MealUpdate(**fields), db_session)

    assert caught.value.status_code == 409
    db_session.refresh(meal)
    assert meal.rev == rev and meal.calories == 300.0


def test_rest_update_never_deletes_past_the_event(db_session, user):
    for meal in (_with_items(db_session, user), _meal(db_session, user)):
        with pytest.raises(HTTPException) as caught:
            update_meal(meal.id, MealUpdate(deleted_at=NOW), db_session)
        assert caught.value.status_code == 409
        db_session.refresh(meal)
        assert meal.deleted_at is None


def test_rest_update_keeps_what_is_not_the_items(db_session, user):
    # Controls: a label on a meal with items, and anything on a legacy one.
    meal = _with_items(db_session, user)
    assert update_meal(meal.id, MealUpdate(meal_type="Обед"), db_session).meal_type == (
        "Обед"
    )
    legacy = _meal(db_session, user)
    assert crud_meal_item.live(db_session, legacy.id) == []
    answer = update_meal(legacy.id, MealUpdate(calories=5.0), db_session)
    assert answer.calories == 5.0
