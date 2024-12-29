from sqlalchemy import Column, Integer, String, JSON, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from app.db.base_class import BaseClass
from app.db.base import Base


class AnalysisReport(Base, BaseClass):
    __tablename__ = "analysis_reports"

    user_id = Column(Integer, ForeignKey("user.id"), nullable=False)
    report_type = Column(String)  # 'weekly' or 'monthly'
    period_start = Column(DateTime, nullable=False)
    period_end = Column(DateTime, nullable=False)
    analysis = Column(JSON)  # AI-generated analysis
    metrics = Column(JSON)  # Aggregated metrics for the period

    user = relationship("User", backref="analysis_reports")
