from sqlalchemy import Column, Integer, Float, JSON, String, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from app.db.base_class import BaseClass
from app.db.base import Base


class Activity(Base, BaseClass):
    __tablename__ = "activities"

    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    timestamp = Column(DateTime, nullable=False)
    activity_type = Column(String)
    duration = Column(Float)  # in minutes
    calories_burned = Column(Float)
    metrics = Column(JSON)  # Additional metrics from Samsung Health

    user = relationship("User", backref="activities")