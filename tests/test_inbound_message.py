"""Tests for inbound_messages (TD-009): CRUD lifecycle, the best-effort service,
the reprocess queue, the retention purge, and config defaults."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.crud.crud_inbound_message import crud_inbound_message
from app.db.base import Base
from app.models.inbound_message import InboundMessage
from app.schemas.inbound_message import InboundMessageCreate


def _memory_engine():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=engine)
    return engine


# --- CRUD lifecycle --------------------------------------------------------


def test_create_defaults_to_pending(db_session):
    obj = crud_inbound_message.create(
        db_session,
        InboundMessageCreate(telegram_id=42, kind="text", content="apple"),
    )
    assert obj.id is not None
    assert obj.status == "pending"
    assert obj.content == "apple"
    assert obj.created_at is not None


def test_mark_analyzed_and_failed(db_session):
    obj = crud_inbound_message.create(
        db_session, InboundMessageCreate(telegram_id=1, kind="text", content="x")
    )

    crud_inbound_message.mark_analyzed(db_session, obj.id, {"calories": 100})
    db_session.refresh(obj)
    assert obj.status == "analyzed"
    assert obj.ai_analysis == {"calories": 100}
    assert obj.error is None

    crud_inbound_message.mark_failed(db_session, obj.id, "boom")
    db_session.refresh(obj)
    assert obj.status == "failed"
    assert obj.error == "boom"


def test_mark_returns_bool(db_session):
    obj = crud_inbound_message.create(
        db_session, InboundMessageCreate(telegram_id=1, kind="text", content="x")
    )
    # True when a row is updated; False for a missing id (no raise either way) —
    # the reprocess counter relies on this to not overstate persisted writes.
    assert crud_inbound_message.mark_analyzed(db_session, obj.id, {"c": 1}) is True
    assert crud_inbound_message.mark_failed(db_session, obj.id, "e") is True
    assert crud_inbound_message.mark_analyzed(db_session, 999999, {"c": 1}) is False
    assert crud_inbound_message.mark_failed(db_session, 999999, "err") is False


def test_get_reprocessable_excludes_analyzed_oldest_first(db_session):
    db_session.query(InboundMessage).delete()
    db_session.commit()
    now = datetime.now(timezone.utc)
    db_session.add_all(
        [
            InboundMessage(
                telegram_id=1,
                kind="text",
                status="failed",
                created_at=now - timedelta(hours=2),
            ),
            InboundMessage(
                telegram_id=1,
                kind="text",
                status="pending",
                created_at=now - timedelta(hours=1),
            ),
            InboundMessage(
                telegram_id=1, kind="text", status="analyzed", created_at=now
            ),
        ]
    )
    db_session.commit()

    rows = crud_inbound_message.get_reprocessable(db_session)

    # analyzed excluded; failed + pending returned oldest-first.
    assert [r.status for r in rows] == ["failed", "pending"]


def test_delete_older_than(db_session):
    db_session.query(InboundMessage).delete()
    db_session.commit()
    now = datetime.now(timezone.utc)
    db_session.add_all(
        [
            InboundMessage(
                telegram_id=1,
                kind="text",
                status="pending",
                created_at=now - timedelta(days=100),
            ),
            InboundMessage(
                telegram_id=1,
                kind="text",
                status="pending",
                created_at=now - timedelta(days=1),
            ),
        ]
    )
    db_session.commit()

    deleted = crud_inbound_message.delete_older_than(
        db_session, now - timedelta(days=60)
    )

    assert deleted == 1
    assert db_session.query(InboundMessage).count() == 1


# --- best-effort service ---------------------------------------------------


def test_service_record_and_reprocessable(monkeypatch):
    import app.services.inbound_message_service as svc

    engine = _memory_engine()
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(svc, "SessionLocal", Session)

    msg_id = svc.record_inbound(
        telegram_id=7, kind="image", content=None, photo_file_id="FID"
    )
    assert msg_id is not None

    # Returned read models are detached but usable after the session closed.
    rows = svc.get_reprocessable()
    assert len(rows) == 1
    assert rows[0].photo_file_id == "FID"
    assert rows[0].status == "pending"

    assert svc.mark_analyzed(msg_id, {"calories": 200}) is True
    assert svc.get_reprocessable() == []  # analyzed drops out of the queue

    # None id is a safe no-op and reports False (nothing persisted).
    assert svc.mark_analyzed(None, {"x": 1}) is False
    assert svc.mark_failed(None, "err") is False


def test_service_record_swallows_db_errors(monkeypatch):
    import app.services.inbound_message_service as svc

    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(svc, "SessionLocal", _boom)
    # Best-effort: a DB failure returns None instead of raising into the flow.
    assert (
        svc.record_inbound(telegram_id=1, kind="text", content="x", photo_file_id=None)
        is None
    )


# --- retention purge task --------------------------------------------------


def test_purge_task_deletes_only_old_rows(monkeypatch):
    import celery_app.tasks.periodic as periodic

    engine = _memory_engine()
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(periodic, "SessionLocal", Session)

    now = datetime.now(timezone.utc)
    with Session() as db:
        db.add_all(
            [
                InboundMessage(
                    telegram_id=1,
                    kind="text",
                    status="pending",
                    created_at=now - timedelta(days=100),
                ),
                InboundMessage(
                    telegram_id=1,
                    kind="text",
                    status="pending",
                    created_at=now - timedelta(days=1),
                ),
            ]
        )
        db.commit()

    deleted = periodic.purge_inbound_messages()

    assert deleted == 1  # only the row past the 60-day default window
    with Session() as db:
        assert db.query(InboundMessage).count() == 1


def test_retention_config_default():
    assert settings.INBOUND_MESSAGE_RETENTION_DAYS == 60
