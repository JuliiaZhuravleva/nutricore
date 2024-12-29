import pytest
from app.crud.crud_user import crud_user
from app.schemas.user import UserCreate, UserUpdate


def test_create_user(db_session):
    user_in = UserCreate(
        telegram_id="test_telegram_id",
        username="test_user"
    )
    user = crud_user.create(db_session, user_in)
    assert user.id is not None
    assert user.telegram_id == "test_telegram_id"
    assert user.username == "test_user"


def test_get_user(db_session):
    user_in = UserCreate(
        telegram_id="another_telegram_id",
        username="another_user"
    )
    user = crud_user.create(db_session, user_in)
    fetched = crud_user.get(db_session, user.id)
    assert fetched is not None
    assert fetched.id == user.id


def test_update_user(db_session):
    user_in = UserCreate(
        telegram_id="update_telegram_id",
        username="old_name"
    )
    user = crud_user.create(db_session, user_in)
    update_data = UserUpdate(username="new_name")
    updated_user = crud_user.update(db_session, user, update_data)
    assert updated_user.username == "new_name"
    assert updated_user.id == user.id


def test_remove_user(db_session):
    user_in = UserCreate(
        telegram_id="remove_telegram_id",
        username="deleter"
    )
    user = crud_user.create(db_session, user_in)
    removed = crud_user.remove(db_session, user.id)
    # Проверяем, что пользователь удален
    get_removed = crud_user.get(db_session, removed.id)
    assert get_removed is None
