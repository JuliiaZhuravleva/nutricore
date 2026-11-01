import datetime

from sqlalchemy import Column, Date, DateTime, ForeignKey, Integer, String
from sqlalchemy import UniqueConstraint

from app.db.base import Base
from app.db.base_class import BaseClass

UTC = datetime.timezone.utc


class RoutineTick(Base, BaseClass):
    """A routine the user ticked on one of their calendar days (stage 5, G5-R08).

    A row exists only for a tick: an unticked day leaves no trace, so nothing can
    ever count it as a miss. The routines themselves live in ``User.settings``;
    ``routine_id`` points there and outlives a removal, because a removal can be
    undone and the tick still happened.
    """

    __tablename__ = "routine_ticks"
    __table_args__ = (UniqueConstraint("user_id", "routine_id", "day"),)

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    routine_id = Column(String, nullable=False)
    day = Column(Date, nullable=False)
    created_at = Column(
        DateTime(timezone=True), default=lambda: datetime.datetime.now(UTC)
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.datetime.now(UTC),
        onupdate=lambda: datetime.datetime.now(UTC),
    )
