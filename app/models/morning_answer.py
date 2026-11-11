import datetime

from sqlalchemy import (
    BigInteger,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)

from app.db.base import Base
from app.db.base_class import BaseClass

UTC = datetime.timezone.utc


class MorningAnswer(Base, BaseClass):
    """One ☀️ morning question about a finished day: claimed before it is sent, then
    what the person answered (stage 6, D10-D14).

    ``UNIQUE(user_id, day)`` is the send claim: a day is asked about once. ``day`` is the
    day asked about (yesterday on the person's clock), not the day of the send. Not
    purged: the 7-day averages read the answers of the days they cover. The ON/OFF
    switch lives in ``User.settings``.
    """

    __tablename__ = "morning_answers"
    __table_args__ = (UniqueConstraint("user_id", "day"),)

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    day = Column(Date, nullable=False)
    sent_at = Column(DateTime(timezone=True), nullable=False)  # claim, then send
    chat_id = Column(BigInteger, nullable=False)
    message_id = Column(BigInteger, nullable=True)
    status = Column(String(8), nullable=False)  # claimed, sent, maybe, blocked
    answer = Column(String(8), nullable=True)  # all, part, little
    answered_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(
        DateTime(timezone=True), default=lambda: datetime.datetime.now(UTC)
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.datetime.now(UTC),
        onupdate=lambda: datetime.datetime.now(UTC),
    )
