import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    false,
)

from app.db.base import Base
from app.db.base_class import BaseClass

UTC = datetime.timezone.utc

KINDS = ("vision", "text", "transcript", "exact_name")


class MealReading(Base, BaseClass):
    """One reading of a meal's sources, applied or not. No purge.

    A late answer is kept here with ``applied=false``, so what the model said is never
    lost and never overwrites the person. ``error`` is the exception's class, never a
    body: logs and this column carry no transcript and no message text.
    """

    __tablename__ = "meal_readings"

    id = Column(Integer, primary_key=True, index=True)
    meal_id = Column(Integer, ForeignKey("meals.id"), nullable=False, index=True)
    source_ids = Column(JSON, nullable=True)
    kind = Column(String, nullable=False)
    backend = Column(String, nullable=True)
    model = Column(String, nullable=True)
    prompt_version = Column(String, nullable=True)
    output = Column(JSON(none_as_null=True), nullable=True)
    status = Column(String, nullable=False)
    error = Column(String, nullable=True)
    applied = Column(Boolean, nullable=False, default=False, server_default=false())
    created_at = Column(
        DateTime(timezone=True), default=lambda: datetime.datetime.now(UTC)
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.datetime.now(UTC),
        onupdate=lambda: datetime.datetime.now(UTC),
    )
