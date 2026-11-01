"""Periodic (Celery-beat) maintenance tasks."""

import logging
from datetime import datetime, timedelta, timezone

from app.core.config import settings
from app.crud.crud_action_event import crud_action_event
from app.crud.crud_ai_call_log import crud_ai_call_log
from app.crud.crud_inbound_message import crud_inbound_message
from app.db.session import SessionLocal
from celery_app.celery_app import app

logger = logging.getLogger(__name__)


@app.task
def purge_ai_call_logs() -> int:
    """Delete ai_call_logs rows older than DEBUG_LOG_RETENTION_DAYS."""
    cutoff = datetime.now(timezone.utc) - timedelta(
        days=settings.DEBUG_LOG_RETENTION_DAYS
    )
    with SessionLocal() as db:
        deleted = crud_ai_call_log.delete_older_than(db, cutoff)
    logger.info("purge_ai_call_logs: removed %s rows older than %s", deleted, cutoff)
    return deleted


@app.task
def purge_inbound_messages() -> int:
    """Delete inbound_messages rows older than INBOUND_MESSAGE_RETENTION_DAYS."""
    cutoff = datetime.now(timezone.utc) - timedelta(
        days=settings.INBOUND_MESSAGE_RETENTION_DAYS
    )
    with SessionLocal() as db:
        deleted = crud_inbound_message.delete_older_than(db, cutoff)
    logger.info(
        "purge_inbound_messages: removed %s rows older than %s", deleted, cutoff
    )
    return deleted


@app.task
def purge_action_events() -> int:
    """Delete action_events rows older than ACTION_EVENT_RETENTION_DAYS. The cost ledger
    has no purge: it holds no text and is kept for good."""
    cutoff = datetime.now(timezone.utc) - timedelta(
        days=settings.ACTION_EVENT_RETENTION_DAYS
    )
    with SessionLocal() as db:
        deleted = crud_action_event.delete_older_than(db, cutoff)
    logger.info("purge_action_events: removed %s rows older than %s", deleted, cutoff)
    return deleted
