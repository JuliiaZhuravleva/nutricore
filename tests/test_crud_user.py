import pytest
from app.crud.crud_user import crud_user
from app.schemas.user import UserCreate, UserUpdate


def test_create_user(db_session):
    """Test user creation."""
    user_in = UserCreate(
        telegram_id=123456789,
        username="test_user",
        diet_preferences={"vegan": True},
        target_metrics={"calories": 2000}
    )
    user = crud_user.create(db_session, obj_in=user_in)
    assert user.telegram_id == 123456789
    assert user.username == "test_user"
    assert user.diet_preferences == {"vegan": True}
    assert user.target_metrics == {"calories": 2000}


def test_get_user_by_telegram_id(db_session):
    """Test getting user by telegram_id."""
    user_in = UserCreate(
        telegram_id=987654321,
        username="another_user"
    )
    user = crud_user.create(db_session, obj_in=user_in)
    found_user = crud_user.get_by_telegram_id(db_session, telegram_id=987654321)
    assert found_user
    assert found_user.telegram_id == user.telegram_id
    assert found_user.username == user.username


def test_get_user(db_session):
    user_in = UserCreate(
        telegram_id=987654321,
        username="another_user"
    )
    user = crud_user.create(db_session, obj_in=user_in)
    fetched = crud_user.get(db_session, user.id)
    assert fetched is not None
    assert fetched.id == user.id


def test_update_user(db_session):
    """Test user update."""
    user_in = UserCreate(
        telegram_id=111222333,
        username="update_user"
    )
    user = crud_user.create(db_session, obj_in=user_in)
    
    # Update user preferences
    user.diet_preferences = {"vegetarian": True}
    db_session.commit()
    
    updated_user = crud_user.get_by_telegram_id(db_session, telegram_id=111222333)
    assert updated_user.diet_preferences == {"vegetarian": True}


def test_remove_user(db_session):
    """Test user removal."""
    user_in = UserCreate(
        telegram_id=444555666,
        username="remove_user"
    )
    user = crud_user.create(db_session, obj_in=user_in)
    db_session.delete(user)
    db_session.commit()
    
    deleted_user = crud_user.get_by_telegram_id(db_session, telegram_id=444555666)
    assert deleted_user is None
