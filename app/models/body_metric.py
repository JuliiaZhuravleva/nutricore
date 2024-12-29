from sqlalchemy import Column, Integer, Float, JSON, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from app.db.base_class import BaseClass
from app.db.base import Base


class BodyMetric(Base, BaseClass):
    __tablename__ = "body_metrics"

    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    timestamp = Column(DateTime, nullable=False)
    weight = Column(Float)
    metrics = Column(JSON)  # Other metrics from Mi Scale

    user = relationship("User", backref="body_metrics")