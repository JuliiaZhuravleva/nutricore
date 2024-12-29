import datetime

from sqlalchemy import Column, Integer, String, JSON, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from app.db.base_class import BaseClass
from app.db.base import Base


UTC = datetime.timezone.utc


class AnalysisReport(Base, BaseClass):
    __tablename__ = "analysis_reports"

    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    report_type = Column(String)  # 'weekly' or 'monthly'
    period_start = Column(DateTime(timezone=True), default=lambda: datetime.datetime.now(UTC))
    period_end = Column(DateTime(timezone=True), default=lambda: datetime.datetime.now(UTC))
    analysis = Column(JSON)  # AI-generated analysis
    metrics = Column(JSON)  # Aggregated metrics for the period

    user = relationship("User", backref="analysis_reports")
