import datetime

from sqlalchemy import (JSON, BigInteger, Column, DateTime, Integer, String,
                        Text)

from app.db.base import Base
from app.db.base_class import BaseClass

UTC = datetime.timezone.utc


class AiCallLog(Base, BaseClass):
    """Debug record of a single OpenAI food-analysis call.

    Intentionally not tied to the users table (telegram_id, no FK) so writing a
    log never depends on a user lookup, and BigInteger avoids the 32-bit overflow
    real Telegram ids hit. Pruned by a daily Celery-beat job (retention config).
    """

    __tablename__ = "ai_call_logs"

    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(BigInteger, nullable=True, index=True)
    kind = Column(String, nullable=False)  # "text" | "image"
    input_ref = Column(Text, nullable=True)  # the meal text or the photo file_id
    model = Column(String, nullable=True)
    raw_response = Column(Text, nullable=True)  # raw OpenAI content string
    parsed_result = Column(JSON, nullable=True)  # normalized dict on success
    status = Column(String, nullable=False)  # "ok" | "error"
    error = Column(Text, nullable=True)
    latency_ms = Column(Integer, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.datetime.now(UTC),
        index=True,
    )
