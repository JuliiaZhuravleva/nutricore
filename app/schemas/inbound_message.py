from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict


class InboundMessageCreate(BaseModel):
    telegram_id: int
    kind: str
    content: Optional[str] = None
    photo_file_id: Optional[str] = None


class InboundMessage(InboundMessageCreate):
    id: int
    status: str
    ai_analysis: Optional[Any] = None
    error: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
