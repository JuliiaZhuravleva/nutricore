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
        # A replayed update finds its row here: one message makes one meal.
        UniqueConstraint(
            "chat_id", "message_id", "kind", name="uq_meal_sources_message"
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
    created_at = Column(
        DateTime(timezone=True), default=lambda: datetime.datetime.now(UTC)
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.datetime.now(UTC),
        onupdate=lambda: datetime.datetime.now(UTC),
    )
