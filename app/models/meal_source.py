import datetime

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    text,
)

from app.db.base import Base
from app.db.base_class import BaseClass

UTC = datetime.timezone.utc

KINDS = ("photo", "document_photo", "text", "voice", "favorite", "picker")


class MealSource(Base, BaseClass):
    """What the person sent, exactly as it came, kept for good.

    No update path except the current ``meal_id``: an answer of the model never
    overwrites it. ``meal_id`` is empty for what is kept without a meal (the stop
    list, a text with no food). ``origin_meal_id`` is the meal it was born as.

    ``inbound_messages`` stays a 60-day diagnostic log; this is the record. Photo
    bytes are not stored (``file_id`` is), voice bytes are. EXIF and location never.
    """

    __tablename__ = "meal_sources"
    __table_args__ = (
        # A replayed update finds its row here. A message is part 0; a message read
        # as several meals keeps one row per meal, parts 1..N. An edited message keeps
        # a new row beside it, pointing back with ``supersedes_id``.
        Index(
            "uq_meal_sources_message",
            "chat_id",
            "message_id",
            "kind",
            "part",
            unique=True,
            sqlite_where=text("supersedes_id IS NULL"),
            postgresql_where=text("supersedes_id IS NULL"),
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    meal_id = Column(Integer, ForeignKey("meals.id"), nullable=True, index=True)
    origin_meal_id = Column(Integer, nullable=True)
    kind = Column(String, nullable=False)
    chat_id = Column(BigInteger, nullable=False)
    message_id = Column(Integer, nullable=False)
    media_group_id = Column(String, nullable=True, index=True)
    reply_to_message_id = Column(Integer, nullable=True)
    forward_date = Column(DateTime(timezone=True), nullable=True)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    text = Column(String, nullable=True)
    file_id = Column(String, nullable=True)
    file_unique_id = Column(String, nullable=True)
    duration_s = Column(Integer, nullable=True)
    audio_path = Column(String, nullable=True)
    supersedes_id = Column(Integer, nullable=True)
    # 0: the message itself; 1..N: one of the meals a message was read as (its words)
    part = Column(SmallInteger, nullable=False, default=0, server_default="0")
    created_at = Column(
        DateTime(timezone=True), default=lambda: datetime.datetime.now(UTC)
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.datetime.now(UTC),
        onupdate=lambda: datetime.datetime.now(UTC),
    )
