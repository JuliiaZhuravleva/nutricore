import datetime

from sqlalchemy import Column, Integer, String, JSON, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from app.db.base_class import BaseClass
from app.db.base import Base


UTC = datetime.timezone.utc


class AnalysisReport(Base, BaseClass):
    __tablename__ = "analysis_reports"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    report_type = Column(String)  # 'weekly' or 'monthly'
    period_start = Column(DateTime(timezone=True), default=lambda: datetime.datetime.now(UTC))
    period_end = Column(DateTime(timezone=True), default=lambda: datetime.datetime.now(UTC))
    analysis = Column(JSON, nullable=True)  # AI-generated analysis
    metrics = Column(JSON, nullable=True)  # Aggregated metrics for the period
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.datetime.now(UTC))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.datetime.now(UTC), onupdate=lambda: datetime.datetime.now(UTC))

    user = relationship("User", back_populates="analysis_reports")
