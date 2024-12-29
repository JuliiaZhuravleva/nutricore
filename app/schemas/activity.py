from pydantic import BaseModel, ConfigDict
from typing import Optional, Any, Dict
from datetime import datetime


class ActivityBase(BaseModel):
    timestamp: datetime
    activity_type: Optional[str] = None
    duration: Optional[float] = None
    calories_burned: Optional[float] = None
    metrics: Optional[Dict[str, Any]] = None


class ActivityCreate(ActivityBase):
    pass


class ActivityUpdate(BaseModel):
    timestamp: Optional[datetime] = None
    activity_type: Optional[str] = None
    duration: Optional[float] = None
    calories_burned: Optional[float] = None
    metrics: Optional[Dict[str, Any]] = None


class ActivityInDBBase(ActivityBase):
    id: int
    user_id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class Activity(ActivityInDBBase):
    pass


class ActivityInDB(ActivityInDBBase):
    pass
