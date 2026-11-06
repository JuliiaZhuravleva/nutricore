import datetime

from sqlalchemy import (
    BigInteger,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)

from app.db.base import Base
from app.db.base_class import BaseClass

UTC = datetime.timezone.utc


class ReminderSend(Base, BaseClass):
    """One 🔔 reminder: claimed before it is sent, then what came of it (stage 6, §6.2).

    The row is the claim: ``UNIQUE(user_id, local_day, slot)`` lets two overlapping
    sweeps send it once. It holds no text and no dish; what was logged from it points
    back here from the meal's ``resolution_signals``. The pause it feeds lives in
    ``User.settings``, so the 60-day purge never ends a pause.
    """

    __tablename__ = "reminder_sends"
    __table_args__ = (
        UniqueConstraint("user_id", "local_day", "slot"),
        Index("ix_reminder_sends_user_sent", "user_id", "sent_at"),
        Index("ix_reminder_sends_chat_message", "chat_id", "message_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    local_day = Column(Date, nullable=False)  # the person's date at the slot
    slot = Column(String(5), nullable=False)  # "21:00"
    kind = Column(String(8), nullable=False)  # evening, meal
    centre = Column(String(5), nullable=True)  # meal slots
    sent_at = Column(DateTime(timezone=True), nullable=False)  # claim, then send
    chat_id = Column(BigInteger, nullable=False)
    message_id = Column(BigInteger, nullable=True)
    status = Column(String(8), nullable=False)  # claimed, sent, maybe, blocked
    answer = Column(String(8), nullable=True)  # fav, words, none, ok, off
    answered_at = Column(DateTime(timezone=True), nullable=True)
    closed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(
        DateTime(timezone=True), default=lambda: datetime.datetime.now(UTC)
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.datetime.now(UTC),
        onupdate=lambda: datetime.datetime.now(UTC),
    )
