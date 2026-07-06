import datetime

from sqlalchemy import (JSON, BigInteger, Column, DateTime, Integer, String,
                        Text, func)

from app.db.base import Base
from app.db.base_class import BaseClass

UTC = datetime.timezone.utc


class InboundMessage(Base, BaseClass):
    """A meal message captured the moment it arrives — before any OpenAI call.

    Persist-on-receipt (TD-009): a ``meals`` row is only written after a
    successful, user-confirmed analysis, so a failed or dropped inbound (e.g. the
    deprecated-model outage) used to vanish — Telegram had already ack'd the
    update and the DB held nothing. This is the raw message itself (one level
    above ``ai_call_logs``, which logs the API call): written ``pending`` on
    receipt, then flipped to ``analyzed`` (with the parsed nutrition) or
    ``failed`` (with the error), and re-runnable via ``/reprocess``.

    Not tied to the users table (telegram_id, no FK, BigInteger) so a write never
    needs a user lookup and can't overflow a 32-bit int — same rationale as
    AiCallLog. Photos are kept as the Telegram ``file_id`` (re-fetchable); storing
    the bytes for Telegram-independent archival is deferred (see the tech-debt).
    Pruned by a daily Celery-beat job (INBOUND_MESSAGE_RETENTION_DAYS).
    """

    __tablename__ = "inbound_messages"

    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(BigInteger, nullable=False, index=True)
    kind = Column(String, nullable=False)  # "text" | "image"
    content = Column(Text, nullable=True)  # the meal text / photo caption
    photo_file_id = Column(Text, nullable=True)  # Telegram file_id for image kind
    status = Column(
        String, nullable=False, default="pending", server_default="pending", index=True
    )  # "pending" | "analyzed" | "failed"
    ai_analysis = Column(JSON, nullable=True)  # parsed nutrition once analyzed
    error = Column(Text, nullable=True)  # failure detail when status="failed"
    # created_at is NOT NULL + server_default so a NULL-dated row can never escape
    # the retention purge (created_at < cutoff would skip it) — TD-006 lesson.
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        default=lambda: datetime.datetime.now(UTC),
        index=True,
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        default=lambda: datetime.datetime.now(UTC),
        onupdate=lambda: datetime.datetime.now(UTC),
    )
