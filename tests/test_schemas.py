import pytest
from pydantic import ValidationError
from app.schemas.user import UserCreate


def test_user_create_ok():
    user = UserCreate(telegram_id="12345", username="TestUser")
    assert user.telegram_id == "12345"


def test_user_create_fail():
    # Пробуем вызвать ошибку валидации
    with pytest.raises(ValidationError):
        UserCreate(telegram_id=None)  # telegram_id по схеме обязателен
