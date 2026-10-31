import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    false,
    func,
)

from app.db.base import Base
from app.db.base_class import BaseClass

UTC = datetime.timezone.utc

NUMBERS_SOURCES = ("barcode", "label", "dish", "model", "plate_share", "user", "none")


class MealItem(Base, BaseClass):
    """A dish inside one meal, with its own copy of the name and the numbers.

    A correction to the dish in the base never reaches a past meal, because nothing
    here is read through ``personal_food_id``. Shown numbers are base × ``factor``,
    so ½ after ×2 is half of the base and not a quarter of it.

    ``origin_meal_id`` and ``original_food_id`` are plain integers on purpose: a
    foreign key's SET NULL would rewrite the two fields that make a separate and a
    dish merge undoable at any time.

    No Pydantic schema: the bot is the only caller (as with FavoriteMeal).
    """

    __tablename__ = "meal_items"

    id = Column(Integer, primary_key=True, index=True)
    meal_id = Column(
        Integer,
        ForeignKey("meals.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    origin_meal_id = Column(Integer, nullable=False)
    personal_food_id = Column(
        Integer,
        ForeignKey("personal_foods.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    original_food_id = Column(Integer, nullable=True)
    position = Column(Integer, nullable=False, default=0, server_default="0")
    name = Column(String(120), nullable=False)
    amount_text = Column(String, nullable=True)  # the person's own words
    grams = Column(Float, nullable=True)
    base_grams = Column(Float, nullable=True)
    # As read. Float, while dishes keep Numeric: convert where the two meet, because
    # Decimal * float raises TypeError.
    base_calories = Column(Float, nullable=True)
    base_proteins = Column(Float, nullable=True)
    base_fats = Column(Float, nullable=True)
    base_carbohydrates = Column(Float, nullable=True)
    factor = Column(Float, nullable=False, default=1, server_default="1")
    # One of NUMBERS_SOURCES.
    numbers_source = Column(
        String, nullable=False, default="none", server_default="none"
    )
    # A person's edit: a re-read leaves this item alone.
    by_user = Column(Boolean, nullable=False, default=False, server_default=false())
    replaced_by_item_id = Column(
        Integer, ForeignKey("meal_items.id", ondelete="SET NULL"), nullable=True
    )
    removed_at = Column(DateTime(timezone=True), nullable=True)
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
