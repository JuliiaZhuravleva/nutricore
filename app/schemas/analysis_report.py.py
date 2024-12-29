from pydantic import BaseModel, ConfigDict
from typing import Optional, Any, Dict
from datetime import datetime


class AnalysisReportBase(BaseModel):
    report_type: Optional[str] = None
    period_start: datetime
    period_end: datetime
    analysis: Optional[Dict[str, Any]] = None
    metrics: Optional[Dict[str, Any]] = None


class AnalysisReportCreate(AnalysisReportBase):
    pass


class AnalysisReportUpdate(BaseModel):
    report_type: Optional[str] = None
    period_start: Optional[datetime] = None
    period_end: Optional[datetime] = None
    analysis: Optional[Dict[str, Any]] = None
    metrics: Optional[Dict[str, Any]] = None


class AnalysisReportInDBBase(AnalysisReportBase):
    id: int
    user_id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AnalysisReport(AnalysisReportInDBBase):
    pass


class AnalysisReportInDB(AnalysisReportInDBBase):
    pass
