import datetime

from sqlalchemy import JSON, BigInteger, Column, DateTime, Integer, String, Text, func

from app.db.base import Base
from app.db.base_class import BaseClass

UTC = datetime.timezone.utc

TEXT = "text"
VOICE = "voice"
PHOTO = "photo"
KINDS = (TEXT, VOICE, PHOTO)


class Feedback(Base, BaseClass):
    """One message a person meant for the owner, saved before anything is sent.

    Keyed by telegram_id with no foreign key: someone with no diary yet can write too.
    ``sent_at`` empty means no admin has a whole copy. ``original`` is the message as
    Telegram gave it, so that «↩️ Это была еда» can put the very same one through the
    diary. ``journal`` is what the owner was shown: lines from closed lists, no food.
    """

    __tablename__ = "feedback"

    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(BigInteger, index=True, nullable=False)
    kind = Column(String(16), nullable=False)
    text = Column(Text, nullable=True)  # the words, or a photo's caption
    file_id = Column(String(255), nullable=True)
    transcript = Column(Text, nullable=True)
    journal = Column(Text, nullable=True)
    original = Column(JSON, nullable=False)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    was_food_at = Column(DateTime(timezone=True), nullable=True)
    read_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        default=lambda: datetime.datetime.now(UTC),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        default=lambda: datetime.datetime.now(UTC),
        onupdate=lambda: datetime.datetime.now(UTC),
    )
