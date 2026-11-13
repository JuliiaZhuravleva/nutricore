"""``dish_ops``: dish changes with no tap, and one undo for a group of them.

On SQLite always, and on Postgres when ``TWO_PEOPLE_DB_URL`` is set (the lock and the
JSON columns behave differently there). Nothing here reaches a model.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy import text as sql
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.crud.crud_meal_item import crud_meal_item
from app.db.base import Base
from app.models import FavoriteMeal, Meal, MealItem, PersonalFood, RecordEvent, User
from app.services import dish_ops

PG_URL = os.environ.get("TWO_PEOPLE_DB_URL")
NOW = datetime(2026, 11, 15, 9, 0, tzinfo=UTC)
BACKENDS = pytest.mark.parametrize(
    "factory",
    [
        "sqlite",
        pytest.param(
            "postgres",
            marks=pytest.mark.skipif(not PG_URL, reason="TWO_PEOPLE_DB_URL unset"),
        ),
    ],
    indirect=True,
)
POSTGRES_ONLY = pytest.mark.skipif(not PG_URL, reason="TWO_PEOPLE_DB_URL unset")


def _engine(backend: str):
    if backend == "postgres":
        engine = create_engine(PG_URL)
        names = ", ".join(t.name for t in Base.metadata.sorted_tables)
        with engine.begin() as connection:
            connection.execute(sql(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
        return engine
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return engine


@pytest.fixture
def factory(request):
    engine = _engine(getattr(request, "param", "sqlite"))
    make = sessionmaker(bind=engine)
    with make() as session:
        session.add(User(id=1, telegram_id=42, settings={}))
        session.add(User(id=2, telegram_id=43, settings={}))
        session.commit()
    yield make
    engine.dispose()


@pytest.fixture
def db(factory):
    with factory() as session:
        yield session


def _user(db, user_id=1) -> User:
    return db.get(User, user_id)


def _dish(db, name, user_id=1, **columns) -> PersonalFood:
    dish = PersonalFood(user_id=user_id, canonical_name=name, **columns)
    db.add(dish)
    db.commit()
    return dish


def _eat(db, dish, user_id=1) -> MealItem:
    meal = Meal(user_id=user_id, description=dish.canonical_name, meal_time=NOW)
    db.add(meal)
    db.flush()
    item = crud_meal_item.add(
        db, meal_id=meal.id, name=dish.canonical_name, personal_food_id=dish.id
    )
    db.commit()
    return item


def _star(db, dish, user_id=1) -> FavoriteMeal:
    fav = FavoriteMeal(
        user_id=user_id,
        name=dish.canonical_name,
        personal_food_id=dish.id,
        calories=100,
        proteins=1,
        fats=1,
        carbohydrates=1,
    )
    db.add(fav)
    db.commit()
    return fav


def _events(db) -> int:
    return db.scalar(select(func.count()).select_from(RecordEvent))


# --- flush only ---------------------------------------------------------------


@BACKENDS
def test_a_rollback_leaves_nothing_and_a_commit_keeps_it(db):
    source, target = _dish(db, "томатный соус"), _dish(db, "соус томатный")
    item = _eat(db, source)
    user = _user(db)
    dish_ops.lock_user(db, user)
    dish_ops.merge(db, user, source, target, now=NOW, group_id="g1")
    db.rollback()
    assert _events(db) == 0
    assert db.get(PersonalFood, source.id).merged_into_id is None
    assert db.get(MealItem, item.id).personal_food_id == source.id
    # The control: the same call, committed, stays.
    dish_ops.lock_user(db, user)
    event = dish_ops.merge(db, user, source, target, now=NOW, group_id="g1")
    db.commit()
    assert _events(db) == 1
    assert event.group_id == "g1"
    assert db.get(MealItem, item.id).personal_food_id == target.id


@BACKENDS
def test_merge_moves_items_and_a_star_and_unmerge_moves_them_back(db):
    source, target = _dish(db, "гречка отварная"), _dish(db, "гречка")
    item, fav = _eat(db, source), _star(db, source)
    user = _user(db)
    event = dish_ops.merge(db, user, source, target, now=NOW)
    db.commit()
    assert event.after["items"] == [item.id]
    assert event.after["stars_moved"] == [fav.id]
    assert db.get(FavoriteMeal, fav.id).personal_food_id == target.id
    back = dish_ops.unmerge(db, user, db.get(PersonalFood, source.id), event)
    db.commit()
    assert back.undoes_event_id == event.id
    assert db.get(PersonalFood, source.id).merged_into_id is None
    assert db.get(MealItem, item.id).personal_food_id == source.id
    assert db.get(FavoriteMeal, fav.id).personal_food_id == source.id


@BACKENDS
def test_a_source_no_longer_live_is_refused_with_its_name(db):
    source, target = _dish(db, "сырок"), _dish(db, "сырок глазированный")
    source.hidden_at = NOW
    db.commit()
    with pytest.raises(dish_ops.Refused) as refused:
        dish_ops.merge(db, _user(db), source, target, now=NOW)
    assert refused.value.name == "сырок"
    assert _events(db) == 0


@BACKENDS
def test_another_persons_target_is_refused(db):
    source = _dish(db, "кефир")
    foreign = _dish(db, "кефир", user_id=2)
    with pytest.raises(dish_ops.Refused):
        dish_ops.merge(db, _user(db), source, foreign, now=NOW)


# --- ⭐ -------------------------------------------------------------------------


@BACKENDS
def test_star_writes_a_favourite_and_its_event_and_a_second_is_a_no_op(db):
    dish = _dish(db, "овсянка", per_100g_calories=90, numbers_basis="per_100")
    user = _user(db)
    event = dish_ops.star(db, user, dish, group_id="g")
    db.commit()
    assert event.kind == "food_star"
    fav = db.get(FavoriteMeal, event.after["favourite"])
    assert fav.personal_food_id == dish.id and fav.removed_at is None
    assert dish_ops.star(db, user, dish, group_id="g") is None
    assert _events(db) == 1


# --- the group undo ---------------------------------------------------------------


@BACKENDS
def test_undo_group_takes_the_star_back_before_its_merge(db):
    source, target = _dish(db, "белый рис"), _dish(db, "рис белый")
    item = _eat(db, source)
    user = _user(db)
    merged = dish_ops.merge(db, user, source, target, now=NOW, group_id="g")
    starred = dish_ops.star(db, user, target, group_id="g")
    db.commit()
    result = dish_ops.undo_group(db, user, "g")
    db.commit()
    assert (result.undone, result.refused_names, result.already) == (2, [], False)
    undos = list(
        db.scalars(
            select(RecordEvent)
            .where(RecordEvent.undoes_event_id.is_not(None))
            .order_by(RecordEvent.id)
        )
    )
    assert [u.undoes_event_id for u in undos] == [starred.id, merged.id]
    assert len({u.group_id for u in undos}) == 1 and undos[0].group_id != "g"
    fav = db.get(FavoriteMeal, starred.after["favourite"])
    assert fav.removed_at is not None
    assert db.get(MealItem, item.id).personal_food_id == source.id


@BACKENDS
def test_a_second_undo_group_writes_nothing(db):
    source, target = _dish(db, "соус"), _dish(db, "соус томатный")
    user = _user(db)
    dish_ops.merge(db, user, source, target, now=NOW, group_id="g")
    db.commit()
    dish_ops.undo_group(db, user, "g")
    db.commit()
    count = _events(db)
    again = dish_ops.undo_group(db, user, "g")
    db.commit()
    assert again.already and again.undone == 0
    assert _events(db) == count


@BACKENDS
def test_a_partial_refusal_is_named_and_the_rest_is_undone(db):
    one, two, target = _dish(db, "сырок а"), _dish(db, "сырок б"), _dish(db, "сырок")
    user = _user(db)
    dish_ops.merge(db, user, one, target, now=NOW, group_id="g")
    dish_ops.merge(db, user, two, target, now=NOW, group_id="g")
    db.commit()
    # «сырок б» was unmerged by hand and merged into another dish since.
    other = _dish(db, "творожок")
    two = db.get(PersonalFood, two.id)
    two.merged_into_id = other.id
    db.commit()
    result = dish_ops.undo_group(db, user, "g")
    db.commit()
    assert result.undone == 1
    assert result.refused_names == ["сырок б"]
    assert db.get(PersonalFood, one.id).merged_into_id is None
    assert db.get(PersonalFood, two.id).merged_into_id == other.id


@BACKENDS
def test_undo_group_never_touches_another_persons_group(db):
    mine, target = _dish(db, "суп"), _dish(db, "суп куриный")
    theirs, their_target = _dish(db, "суп", 2), _dish(db, "суп куриный", 2)
    dish_ops.merge(db, _user(db, 2), theirs, their_target, now=NOW, group_id="g")
    db.commit()
    result = dish_ops.undo_group(db, _user(db), "g")
    assert result.already and result.undone == 0
    assert db.get(PersonalFood, theirs.id).merged_into_id == their_target.id
    assert mine.merged_into_id is None and target.merged_into_id is None


# --- the lock ---------------------------------------------------------------------


@POSTGRES_ONLY
@pytest.mark.parametrize("factory", ["postgres"], indirect=True)
def test_lock_user_makes_a_second_writer_wait(factory):
    with factory() as first, factory() as second:
        dish_ops.lock_user(first, first.get(User, 1))
        second.execute(sql("SET lock_timeout = '200ms'"))
        with pytest.raises(OperationalError, match="lock timeout"):
            dish_ops.lock_user(second, second.get(User, 1))
        second.rollback()
        first.commit()
        # The control: once the first commits, the second goes through.
        second.execute(sql("SET lock_timeout = '200ms'"))
        dish_ops.lock_user(second, second.get(User, 1))
        second.commit()
