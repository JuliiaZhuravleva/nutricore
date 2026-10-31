import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    false,
)
from sqlalchemy.orm import relationship

from app.db.base import Base
from app.db.base_class import BaseClass

UTC = datetime.timezone.utc


class Meal(Base, BaseClass):
    __tablename__ = "meals"
    __table_args__ = (Index("ix_meals_user_id_meal_time", "user_id", "meal_time"),)

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    description = Column(String)
    meal_time = Column(
        DateTime(timezone=True), default=lambda: datetime.datetime.now(UTC)
    )
    # Which meal of the day the user said this was: "Завтрак" | "Обед" | "Ужин",
    # or NULL when they picked "Сейчас" (no label given, none invented).
    # The bot asked this from the beginning; until 2026-09-13 the answer was
    # collected in user_data and never stored anywhere.
    meal_type = Column(String, nullable=True)
    calories = Column(Float)
    proteins = Column(Float)
    fats = Column(Float)
    carbohydrates = Column(Float)
    nutrients = Column(JSON, nullable=True)  # For additional nutritional info
    photos = Column(JSON, nullable=True)  # Telegram file references for the meal
    ai_analysis = Column(JSON, nullable=True)  # Raw OpenAI analysis, kept for coaching
    # Pipeline resolution tracking (photo-product-lookup, A1).
    # resolution_source: which strategy produced the final numbers.
    #   "barcode_off" | "name_off" | "label_ocr" | "vision"
    #   NULL means the legacy flow (before this column existed) or plain vision.
    resolution_source = Column(String, nullable=True, index=True)
    # resolution_signals: key intermediate values for transparency + misprediction
    # analysis — e.g. {"barcode_raw": "4607195501226", "product_name": "...",
    # "portion_grams": 150.0, "confidence_tier": "high", "lookup_latency_ms": 320}.
    # Not just a flat source enum (per A1 spec); gives A5 enough data to surface
    # the resolution path in the reply and A7 enough data to run regression checks.
    resolution_signals = Column(JSON, nullable=True)
    # When the message was sent. Written once, never updated; ``meal_time`` is when
    # it was eaten. Not in MealUpdate, so no edit can move it.
    sent_at = Column(
        DateTime(timezone=True), default=lambda: datetime.datetime.now(UTC)
    )
    # "exact" | "approx" | "day_only".
    time_precision = Column(
        String, nullable=False, default="exact", server_default="exact"
    )
    # "sent" | "said" | "forward" | "edited", plus the person's words for it.
    time_source = Column(String, nullable=False, default="sent", server_default="sent")
    time_phrase = Column(String, nullable=True)
    # What the totals stand on: "items" | "plate" | "none". See the rules of the sum
    # in docs/stages/05-gentle-review-screens.md, section 3.1.
    numbers_basis = Column(
        String, nullable=False, default="plate", server_default="plate"
    )
    # Whole-plate numbers, written once at reading, never split. none_as_null: an
    # absent value is SQL NULL, not the JSON word null.
    plate_totals = Column(JSON(none_as_null=True), nullable=True)
    # Soft delete: "удалено" keeps the row, so ↩️ Вернуть can restore it exactly.
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    # +1 on any change. A late model result applies only if ``rev`` still matches.
    rev = Column(Integer, nullable=False, default=0, server_default="0")
    # "pending" | "read" | "failed" | "no_food" | "no_speech". A meal is in the day
    # from its first moment, read or not.
    reading_status = Column(
        String, nullable=False, default="read", server_default="read"
    )
    read_attempts = Column(Integer, nullable=False, default=0, server_default="0")
    # Not in any reader until the person finishes it.
    is_draft = Column(Boolean, nullable=False, default=False, server_default=false())
    # The meal this one was born as and then joined into; with ``deleted_at`` set.
    merged_into_id = Column(Integer, ForeignKey("meals.id"), nullable=True)
    created_at = Column(
        DateTime(timezone=True), default=lambda: datetime.datetime.now(UTC)
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.datetime.now(UTC),
        onupdate=lambda: datetime.datetime.now(UTC),
    )

    # Relationship with user
    user = relationship("User", back_populates="meals")
