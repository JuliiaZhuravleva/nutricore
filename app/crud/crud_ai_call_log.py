from datetime import datetime

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.models.ai_call_log import AiCallLog
from app.schemas.ai_call_log import AiCallLogCreate


class CRUDAiCallLog:
    def create(self, db: Session, obj_in: AiCallLogCreate) -> AiCallLog:
        db_obj = AiCallLog(**obj_in.model_dump())
        db.add(db_obj)
        db.commit()
        db.refresh(db_obj)
        return db_obj

    def delete_older_than(self, db: Session, cutoff: datetime) -> int:
        """Delete rows created before `cutoff`; returns the number removed."""
        result = db.execute(delete(AiCallLog).where(AiCallLog.created_at < cutoff))
        db.commit()
        return result.rowcount or 0


crud_ai_call_log = CRUDAiCallLog()
