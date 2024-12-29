import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.db.base import Base

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
