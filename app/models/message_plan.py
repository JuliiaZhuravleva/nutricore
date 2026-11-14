import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)

from app.db.base import Base
from app.db.base_class import BaseClass

UTC = datetime.timezone.utc

# reading → done | failed | single (it fell back to one meal, as before plans).
STATUSES = ("reading", "done", "failed", "single")
WANTS = ("dish", "meals")


def _now() -> datetime.datetime:
    return datetime.datetime.now(UTC)


class MessagePlan(Base, BaseClass):
    """One message read as several actions: meals, each with its own time, and dish
    changes made in words.

    ``UNIQUE(chat_id, message_id)`` is the claim: a replayed or edited message never
    plans twice. The row keeps the message itself (kind, words, times), so 🔁 and the
    sweep can run it again with nothing else at hand. ``group_id`` ties the dish
    changes' record events, so «↩️ Вернуть всё» takes them back together.
    """

    __tablename__ = "message_plans"
    __table_args__ = (
        UniqueConstraint("chat_id", "message_id", name="uq_message_plans_message"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chat_id = Column(BigInteger, nullable=False)
    message_id = Column(BigInteger, nullable=False)
    kind = Column(String(16), nullable=False)  # text, voice
    text = Column(Text, nullable=True)  # the words planned: typed, or heard
    sent_at = Column(DateTime(timezone=True), nullable=True)
    reply_to_message_id = Column(BigInteger, nullable=True)
    forward_date = Column(DateTime(timezone=True), nullable=True)
    want = Column(String(8), nullable=False)  # one of WANTS
    status = Column(String(12), nullable=False)  # one of STATUSES
    commands = Column(
        JSON(none_as_null=True), nullable=True
    )  # the checked lines, as run
    meal_ids = Column(
        JSON(none_as_null=True), nullable=True
    )  # the parts' meals, in order
    line_ids = Column(
        JSON(none_as_null=True), nullable=True
    )  # their lines' message ids, in order
    group_id = Column(String(36), nullable=True)
    line_message_id = Column(BigInteger, nullable=True)  # the «читаю…» line
    result_message_id = Column(BigInteger, nullable=True)  # the dish changes' answer
    # NOT NULL + server_default, as the migration (TD-006 lesson)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), default=_now
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        default=_now,
        onupdate=_now,
    )
