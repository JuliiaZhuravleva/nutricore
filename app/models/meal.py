import datetime

from sqlalchemy import JSON, Column, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from app.db.base import Base
from app.db.base_class import BaseClass

UTC = datetime.timezone.utc


class Meal(Base, BaseClass):
    __tablename__ = "meals"

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
