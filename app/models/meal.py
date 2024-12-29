from sqlalchemy import Column, Integer, String, Float, JSON, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from app.db.base_class import BaseClass
from app.db.base import Base


class Meal(Base, BaseClass):
    __tablename__ = "meals"

    user_id = Column(Integer, ForeignKey("user.id"), nullable=False)
    description = Column(String)
    meal_time = Column(DateTime, nullable=False)
    calories = Column(Float)
    proteins = Column(Float)
    fats = Column(Float)
    carbohydrates = Column(Float)
    nutrients = Column(JSON)  # Additional nutrients like vitamins, minerals
    photos = Column(JSON)  # Array of photo URLs/IDs
    ai_analysis = Column(JSON)  # Raw AI analysis response

    user = relationship("User", backref="meals")