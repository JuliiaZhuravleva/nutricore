from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict


class AiCallLogCreate(BaseModel):
    telegram_id: Optional[int] = None
    kind: str
    input_ref: Optional[str] = None
    model: Optional[str] = None
    raw_response: Optional[str] = None
    parsed_result: Optional[Any] = None
    status: str
    error: Optional[str] = None
    latency_ms: Optional[int] = None

    # `model` is a normal field here, not a pydantic namespace.
    model_config = ConfigDict(protected_namespaces=())


class AiCallLog(AiCallLogCreate):
    id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True, protected_namespaces=())
