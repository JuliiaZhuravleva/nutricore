import pytest
from pydantic import ValidationError
from app.schemas.user import UserCreate


def test_user_create():
    """Test UserCreate schema."""
    user = UserCreate(telegram_id=12345, username="TestUser")
    assert user.telegram_id == 12345
    assert user.username == "TestUser"


def test_user_create_validation():
    """Test UserCreate schema validation."""
    with pytest.raises(ValidationError):
        UserCreate(telegram_id=None)  # telegram_id по схеме обязателен
