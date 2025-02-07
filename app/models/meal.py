import datetime

from sqlalchemy import Column, Integer, String, Float, JSON, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from app.db.base_class import BaseClass
from app.db.base import Base


UTC = datetime.timezone.utc


class Meal(Base, BaseClass):
    __tablename__ = "meals"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    description = Column(String)
    meal_time = Column(DateTime(timezone=True), default=lambda: datetime.datetime.now(UTC))
    calories = Column(Float)
    proteins = Column(Float)
    fats = Column(Float)
    carbohydrates = Column(Float)
    nutrients = Column(JSON, nullable=True)  # For additional nutritional info
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.datetime.now(UTC))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.datetime.now(UTC), onupdate=lambda: datetime.datetime.now(UTC))

    # Relationship with user
    user = relationship("User", back_populates="meals")