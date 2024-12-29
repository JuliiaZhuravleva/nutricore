import datetime

from sqlalchemy import Column, Integer, Float, JSON, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from app.db.base_class import BaseClass
from app.db.base import Base


UTC = datetime.timezone.utc

class BodyMetric(Base, BaseClass):
    __tablename__ = "body_metrics"

    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    timestamp = Column(DateTime(timezone=True), default=lambda: datetime.datetime.now(UTC))
    weight = Column(Float)
    metrics = Column(JSON)  # Other metrics from Mi Scale

    user = relationship("User", backref="body_metrics")