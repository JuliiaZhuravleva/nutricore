import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
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

# claimed → ready → sent | maybe | retry | blocked | failed; ready with no rows → empty;
# a sent one → applied | undone | retired (a newer batch replaced it).
BATCH_STATES = (
    "claimed",
    "ready",
    "sent",
    "maybe",
    "retry",
    "blocked",
    "failed",
    "empty",
    "applied",
    "undone",
    "retired",
)
ITEM_STATES = ("proposed", "applied", "declined", "stale", "undone", "rolled_back")


def _now() -> datetime.datetime:
    return datetime.datetime.now(UTC)


class DishTidyBatch(Base, BaseClass):
    """One 🍲 tidy-up: the dishes merged by itself and the proposals sent with them.

    ``UNIQUE(user_id, week)`` is the weekly claim (``week`` is the local Sunday); an
    on-demand batch has no week. ``group_id`` ties the batch's record events, so
    «↩️ Вернуть всё» takes them back together.
    """

    __tablename__ = "dish_tidy_batches"
    __table_args__ = (UniqueConstraint("user_id", "week"),)

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    week = Column(Date, nullable=True)
    group_id = Column(String(36), nullable=False)
    state = Column(String(12), nullable=False)  # one of BATCH_STATES
    chat_id = Column(BigInteger, nullable=True)
    message_id = Column(BigInteger, nullable=True)
    tries = Column(Integer, nullable=False, default=0)
    claimed_at = Column(DateTime(timezone=True), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=True)  # the proposals' end
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)


class DishTidyItem(Base, BaseClass):
    """One row of a batch: a merge done by itself (``same``), a proposed merge
    (``merge``) or a proposed ⭐ (``star``). ``label`` keeps the names as sent, so the
    message reads the same after a rename. ``fingerprint`` lets a declined row come
    back only when a name or the calories change."""

    __tablename__ = "dish_tidy_items"

    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(
        Integer,
        ForeignKey("dish_tidy_batches.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    idx = Column(Integer, nullable=False)  # the row's place, its bit in the mask
    kind = Column(String(8), nullable=False)  # same, merge, star
    source_id = Column(
        Integer, ForeignKey("personal_foods.id", ondelete="SET NULL"), nullable=True
    )
    target_id = Column(
        Integer, ForeignKey("personal_foods.id", ondelete="SET NULL"), nullable=True
    )
    key = Column(String(32), nullable=False)  # m:<low>:<high> or s:<id>
    label = Column(JSON, nullable=False)  # the names as sent
    fingerprint = Column(String(16), nullable=False)
    ticked = Column(Boolean, nullable=False, default=True)  # as sent
    days = Column(Integer, nullable=False, default=0)
    event_id = Column(
        Integer, ForeignKey("record_events.id", ondelete="SET NULL"), nullable=True
    )
    state = Column(String(12), nullable=False)  # one of ITEM_STATES
    decided_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)
