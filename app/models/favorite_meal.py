import datetime

from sqlalchemy import JSON, Column, DateTime, Float, ForeignKey, Integer, String

from app.db.base import Base
from app.db.base_class import BaseClass

UTC = datetime.timezone.utc


class FavoriteMeal(Base, BaseClass):
    """A dish the user saved to log again with one tap, without a new analysis.

    Made from a confirmed meal ("⭐ Да, и в избранное"); the numbers are copied, so
    re-logging it never calls OpenAI. Removing it only stamps ``removed_at`` — the
    "↩️ Вернуть" button under the removal clears the stamp again.

    No Pydantic schema: there is no API router for it yet; the bot is the only
    caller and passes plain values (as with AppSetting).
    """

    __tablename__ = "favorite_meals"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    calories = Column(Float)
    proteins = Column(Float)
    fats = Column(Float)
    carbohydrates = Column(Float)
    nutrients = Column(JSON, nullable=True)  # the confirmed nutrition dict, as saved
    # The meal it was made from. SET NULL: deleting that meal keeps the favorite.
    source_meal_id = Column(
        Integer, ForeignKey("meals.id", ondelete="SET NULL"), nullable=True
    )
    use_count = Column(Integer, nullable=False, default=0)
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    removed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(
        DateTime(timezone=True), default=lambda: datetime.datetime.now(UTC)
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.datetime.now(UTC),
        onupdate=lambda: datetime.datetime.now(UTC),
    )
