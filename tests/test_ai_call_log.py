"""Tests for the ai_call_logs debug table: CRUD, retention purge, config."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.crud.crud_ai_call_log import crud_ai_call_log
from app.db.base import Base
from app.models.ai_call_log import AiCallLog
from app.schemas.ai_call_log import AiCallLogCreate


def test_create_ai_call_log(db_session):
    obj = crud_ai_call_log.create(
        db_session,
        AiCallLogCreate(
            telegram_id=123,
            kind="text",
            input_ref="apple",
            model="gpt-4o-mini",
            raw_response='{"x": 1}',
            parsed_result={"x": 1},
            status="ok",
            latency_ms=42,
        ),
    )
    assert obj.id is not None
    assert obj.kind == "text"
    assert obj.parsed_result == {"x": 1}
    assert obj.created_at is not None


def test_delete_older_than(db_session):
    # Deterministic regardless of any leaked rows from other tests.
    db_session.query(AiCallLog).delete()
    db_session.commit()
    now = datetime.now(timezone.utc)
    db_session.add_all(
        [
            AiCallLog(kind="text", status="ok", created_at=now - timedelta(days=100)),
            AiCallLog(kind="text", status="ok", created_at=now - timedelta(days=1)),
        ]
    )
    db_session.commit()

    deleted = crud_ai_call_log.delete_older_than(db_session, now - timedelta(days=60))

    # Only the 100-day-old row is past the window; the 1-day-old row survives.
    assert deleted == 1
    assert db_session.query(AiCallLog).count() == 1


def test_purge_task_deletes_only_old_rows(monkeypatch):
    import celery_app.tasks.periodic as periodic

    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(periodic, "SessionLocal", Session)

    now = datetime.now(timezone.utc)
    with Session() as db:
        db.add_all(
            [
                AiCallLog(
                    kind="text", status="ok", created_at=now - timedelta(days=100)
                ),
                AiCallLog(kind="text", status="ok", created_at=now - timedelta(days=1)),
            ]
        )
        db.commit()

    deleted = periodic.purge_ai_call_logs()

    assert deleted == 1  # only the row past the 60-day default window
    with Session() as db:
        assert db.query(AiCallLog).count() == 1


def test_debug_config_defaults():
    assert settings.LOG_LEVEL == "INFO"
    assert settings.DEBUG_LOG_RETENTION_DAYS == 60
