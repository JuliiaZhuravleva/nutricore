from sqlalchemy import Column, BigInteger, String, JSON, Integer, DateTime
from sqlalchemy.orm import relationship
from datetime import datetime
from app.db.base_class import Base, BaseClass


class User(Base, BaseClass):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(BigInteger, unique=True, index=True, nullable=False)
    username = Column(String, index=True)
    diet_preferences = Column(JSON)
    target_metrics = Column(JSON)  # For storing KBJU norms
    settings = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    meals = relationship("Meal", back_populates="user")
    body_metrics = relationship("BodyMetric", back_populates="user")
    activities = relationship("Activity", back_populates="user")
    analysis_reports = relationship("AnalysisReport", back_populates="user")
    subscription = relationship("Subscription", back_populates="user", uselist=False, foreign_keys="Subscription.user_id")
