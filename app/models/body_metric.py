import datetime

from sqlalchemy import Column, Integer, Float, JSON, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from app.db.base_class import BaseClass
from app.db.base import Base


UTC = datetime.timezone.utc

class BodyMetric(Base, BaseClass):
    __tablename__ = "body_metrics"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    timestamp = Column(DateTime(timezone=True), default=lambda: datetime.datetime.now(UTC))
    weight = Column(Float)
    metrics = Column(JSON, nullable=True)  # Other metrics from Mi Scale
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.datetime.now(UTC))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.datetime.now(UTC), onupdate=lambda: datetime.datetime.now(UTC))

    user = relationship("User", back_populates="body_metrics")