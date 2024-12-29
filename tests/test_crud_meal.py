import pytest
from datetime import datetime

from app.crud.crud_meal import crud_meal
from app.crud.crud_user import crud_user
from app.schemas.meal import MealCreate, MealUpdate
from app.schemas.user import UserCreate


def test_create_meal(db_session):
    # Сначала создаем пользователя, к которому привяжется Meal
    user_in = UserCreate(telegram_id="meal_user_1", username="meal_user")
    user = crud_user.create(db_session, user_in)

    meal_in = MealCreate(
        description="Test Meal",
        meal_time=datetime(2024, 1, 1, 8, 0, 0),
        calories=300
    )
    meal = crud_meal.create(db_session, meal_in, user_id=user.id)
    assert meal.id is not None
    assert meal.description == "Test Meal"
    assert meal.calories == 300
    assert meal.user_id == user.id


def test_update_meal(db_session):
    user_in = UserCreate(telegram_id="meal_user_2")
    user = crud_user.create(db_session, user_in)

    meal_in = MealCreate(
        description="Breakfast",
        meal_time=datetime(2024, 1, 1, 9, 0, 0),
        calories=400
    )
    meal = crud_meal.create(db_session, meal_in, user_id=user.id)

    meal_update = MealUpdate(
        description="Updated Breakfast",
        calories=450
    )
    updated_meal = crud_meal.update(db_session, meal, meal_update)
    assert updated_meal.description == "Updated Breakfast"
    assert updated_meal.calories == 450


def test_remove_meal(db_session):
    user_in = UserCreate(telegram_id="meal_user_3")
    user = crud_user.create(db_session, user_in)

    meal_in = MealCreate(
        description="Lunch",
        meal_time=datetime(2024, 1, 1, 13, 0, 0),
        calories=600
    )
    meal = crud_meal.create(db_session, meal_in, user_id=user.id)
    removed = crud_meal.remove(db_session, meal.id)
    assert removed.id == meal.id

    # Убеждаемся, что в БД его больше нет
    fetched = crud_meal.get(db_session, meal.id)
    assert fetched is None
