import os

# The app's pydantic Settings loads at import time (app.db.base → app.core.config)
# and requires these vars. Seed them before any app import so the suite runs
# without a real .env. setdefault → never override real/CI-provided values.
_TEST_ENV_DEFAULTS = {
    "POSTGRES_SERVER": "localhost",
    "POSTGRES_USER": "test",
    "POSTGRES_PASSWORD": "test",
    "POSTGRES_DB": "test",
    "POSTGRES_PORT": "5432",
    "REDIS_HOST": "localhost",
    "REDIS_PORT": "6379",
    "SECRET_KEY": "test-secret-key",
    "TELEGRAM_BOT_TOKEN": "test-bot-token",
    "OPENAI_API_KEY": "test-openai-key",
}
for _k, _v in _TEST_ENV_DEFAULTS.items():
    os.environ.setdefault(_k, _v)

import pytest  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.db.base import Base  # noqa: E402

# Создаем тестовый движок в памяти (SQLite in-memory)
# Если вы хотите использовать реальную БД, укажите PostgreSQL URL
TEST_DATABASE_URL = "sqlite:///:memory:"


@pytest.fixture(scope="session")
def test_engine():
    engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
    # Создаем все таблицы
    Base.metadata.create_all(bind=engine)
    yield engine
    # Удаляем таблицы (опционально)
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture(scope="function")
def db_session(test_engine):
    """
    Создает новую сессию БД для каждого теста.
    После теста откатывает все изменения.
    """
    connection = test_engine.connect()
    trans = connection.begin()
    TestingSessionLocal = sessionmaker(bind=connection)
    session = TestingSessionLocal()

    try:
        yield session
    finally:
        session.close()
        trans.rollback()
        connection.close()


@pytest.fixture(autouse=True)
def _hermetic_openai_model_override(monkeypatch):
    """TD-007: ``OpenAIService()`` now reads a persisted model override from the DB
    on construction. Default every test to "no override" so pure-unit tests that
    build a service stay hermetic (never touch a real Postgres via the unpatched
    ``SessionLocal``). Tests that exercise the override patch this back or drive the
    ``model_selection`` module directly (self-heal tests are unaffected — they call
    ``model_selection.get_persisted_model`` / ``apply_persisted_model``, not this).
    """
    monkeypatch.setattr(
        "app.services.openai_service.get_persisted_model",
        lambda: None,
        raising=False,
    )
    # ADR-0004: per-task picks are read the same way — and a dead model alerts the
    # admin through the DB + Telegram. Tests that exercise either patch it back.
    monkeypatch.setattr(
        "app.services.openai_service.get_persisted_task_models",
        lambda: {},
        raising=False,
    )

    async def _no_alert(task, ref, err):
        return False

    monkeypatch.setattr(
        "app.services.openai_service.report_model_failure",
        _no_alert,
        raising=False,
    )


@pytest.fixture(autouse=True)
def celery_delay(monkeypatch):
    """TD-025: saving a meal enqueues the personal-food task with ``.delay``,
    which publishes to Redis synchronously. With no broker that publish blocks
    ~20 s per save before failing, so every confirm test waited on it. Stub it
    for every test; a test that asserts the enqueue requests ``celery_delay``.
    Tests that ``patch`` ``.delay`` themselves still override this stub.
    """
    from unittest.mock import MagicMock

    from celery_app.tasks.personal_food import embed_and_save_personal_food

    delay = MagicMock()
    monkeypatch.setattr(embed_and_save_personal_food, "delay", delay)
    return delay
