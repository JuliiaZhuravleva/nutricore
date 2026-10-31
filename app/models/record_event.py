import datetime

from sqlalchemy import JSON, Column, DateTime, ForeignKey, Integer, String, func

from app.db.base import Base
from app.db.base_class import BaseClass

UTC = datetime.timezone.utc

EVENT_KINDS = (
    "qty",
    "grams",
    "replace",
    "add",
    "remove",
    "time",
    "join",
    "split",
    "delete",
    "restore",
    "rename",
    "numbers",
    "food_merge",
    "food_hide",
    "reread",
    "write",
    "groups",
)


class RecordEvent(Base, BaseClass):
    """One change to the record, with what it replaced.

    ``↩️ Вернуть`` reads an event, checks that the fields still hold ``after``, and
    writes a new event with ``undoes_event_id``, so an undo can itself be undone.
    An event outlives the meal and the dish it names, hence SET NULL.

    ``source_id`` has no foreign key yet: ``meal_sources`` arrives with slice M.
    No Pydantic schema: the bot is the only caller (as with FavoriteMeal).
    """

    __tablename__ = "record_events"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    meal_id = Column(
        Integer,
        ForeignKey("meals.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    food_id = Column(
        Integer,
        ForeignKey("personal_foods.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    kind = Column(String, nullable=False)  # one of EVENT_KINDS
    # none_as_null: a missing side is SQL NULL, not the JSON word null.
    before = Column(JSON(none_as_null=True), nullable=True)
    after = Column(JSON(none_as_null=True), nullable=True)
    source_id = Column(Integer, nullable=True)
    undoes_event_id = Column(
        Integer, ForeignKey("record_events.id", ondelete="SET NULL"), nullable=True
    )
    # NOT NULL + server_default (TD-006 lesson)
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
