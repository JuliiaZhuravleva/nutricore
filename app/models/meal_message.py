import datetime

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)

from app.db.base import Base
from app.db.base_class import BaseClass

UTC = datetime.timezone.utc

VIEWS = ("pending", "line", "card", "source", "other")


class MealMessage(Base, BaseClass):
    """Which meal a message in the chat is about, so a reply to it finds its meal.

    Holds the bot's line, a card opened from a list and the person's own message.
    A late read edits a message only while its ``view`` is still ``pending``.
    """

    __tablename__ = "meal_messages"
    __table_args__ = (
        UniqueConstraint("chat_id", "message_id", name="uq_meal_messages_message"),
    )

    id = Column(Integer, primary_key=True, index=True)
    chat_id = Column(BigInteger, nullable=False)
    message_id = Column(Integer, nullable=False)
    meal_id = Column(Integer, ForeignKey("meals.id"), nullable=False, index=True)
    view = Column(String, nullable=False)
    created_at = Column(
        DateTime(timezone=True), default=lambda: datetime.datetime.now(UTC)
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.datetime.now(UTC),
        onupdate=lambda: datetime.datetime.now(UTC),
    )
