from pydantic import BaseModel, ConfigDict
from typing import Optional, Any, Dict
from datetime import datetime


class BodyMetricBase(BaseModel):
    timestamp: datetime
    weight: Optional[float] = None
    metrics: Optional[Dict[str, Any]] = None


class BodyMetricCreate(BodyMetricBase):
    pass


class BodyMetricUpdate(BaseModel):
    timestamp: Optional[datetime] = None
    weight: Optional[float] = None
    metrics: Optional[Dict[str, Any]] = None


class BodyMetricInDBBase(BodyMetricBase):
    id: int
    user_id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class BodyMetric(BodyMetricInDBBase):
    pass


class BodyMetricInDB(BodyMetricInDBBase):
    pass
