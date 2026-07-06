from datetime import UTC, datetime
from typing import List

import pytest

from app.crud.crud_meal import crud_meal
from app.crud.crud_user import crud_user
from app.schemas.meal import MealCreate, MealUpdate
from app.schemas.user import UserCreate


def test_create_meal(db_session):
    """Test meal creation."""
    # Create a user first
    user_in = UserCreate(telegram_id=111111111, username="meal_user")
    user = crud_user.create(db_session, obj_in=user_in)

    meal_in = MealCreate(
        description="Test meal",
        calories=500,
        proteins=30,
        fats=20,
        carbohydrates=50,
        meal_time=datetime.now(UTC),
        nutrients={"vitamins": ["A", "C"]},
        photos=["photo1.jpg"],
        ai_analysis={"sentiment": "positive"},
    )

    meal = crud_meal.create(db_session, obj_in=meal_in, user_id=user.id)
    assert meal.description == "Test meal"
    assert meal.calories == 500
    assert meal.user_id == user.id


def test_get_user_meals(db_session):
    """Test getting user meals."""
    user_in = UserCreate(telegram_id=222222222)
    user = crud_user.create(db_session, obj_in=user_in)

    # Create two meals for the user
    meal1 = MealCreate(
        description="Breakfast", calories=300, meal_time=datetime.now(UTC)
    )
    meal2 = MealCreate(description="Lunch", calories=600, meal_time=datetime.now(UTC))

    crud_meal.create(db_session, obj_in=meal1, user_id=user.id)
    crud_meal.create(db_session, obj_in=meal2, user_id=user.id)

    meals = crud_meal.get_user_meals(db_session, user_id=user.id)
    assert len(meals) == 2


def test_update_meal(db_session):
    user_in = UserCreate(telegram_id=444444444)
    user = crud_user.create(db_session, obj_in=user_in)

    meal_in = MealCreate(
        description="Breakfast", meal_time=datetime(2024, 1, 1, 9, 0, 0), calories=400
    )
    meal = crud_meal.create(db_session, meal_in, user_id=user.id)

    meal_update = MealUpdate(description="Updated Breakfast", calories=450)
    updated_meal = crud_meal.update(db_session, meal, meal_update)
    assert updated_meal.description == "Updated Breakfast"
    assert updated_meal.calories == 450


def test_remove_meal(db_session):
    """Test meal removal."""
    user_in = UserCreate(telegram_id=333333333)
    user = crud_user.create(db_session, obj_in=user_in)

    meal_in = MealCreate(
        description="To be deleted", calories=400, meal_time=datetime.now(UTC)
    )

    meal = crud_meal.create(db_session, obj_in=meal_in, user_id=user.id)
    removed = crud_meal.remove(db_session, meal.id)
    assert removed.id == meal.id

    # Убеждаемся, что в БД его больше нет
    fetched = crud_meal.get(db_session, meal.id)
    assert fetched is None
