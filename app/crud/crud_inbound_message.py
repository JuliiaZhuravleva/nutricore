from datetime import datetime
from typing import Any, List

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.inbound_message import InboundMessage
from app.schemas.inbound_message import InboundMessageCreate


class CRUDInboundMessage:
    def create(self, db: Session, obj_in: InboundMessageCreate) -> InboundMessage:
        db_obj = InboundMessage(**obj_in.model_dump())
        db.add(db_obj)
        db.commit()
        db.refresh(db_obj)
        return db_obj

    def mark_analyzed(self, db: Session, msg_id: int, ai_analysis: Any) -> bool:
        """Flip a message to analyzed with its parsed nutrition (clears error).

        Returns True if a row was updated, False if no such id exists.
        """
        obj = db.get(InboundMessage, msg_id)
        if obj is None:
            return False
        obj.status = "analyzed"
        obj.ai_analysis = ai_analysis
        obj.error = None
        db.commit()
        return True

    def mark_failed(self, db: Session, msg_id: int, error: str) -> bool:
        """Flip a message to failed with the error detail.

        Returns True if a row was updated, False if no such id exists.
        """
        obj = db.get(InboundMessage, msg_id)
        if obj is None:
            return False
        obj.status = "failed"
        obj.error = error
        db.commit()
        return True

    def get_reprocessable(self, db: Session, limit: int = 50) -> List[InboundMessage]:
        """Oldest-first pending/failed messages — the replay queue for /reprocess."""
        stmt = (
            select(InboundMessage)
            .where(InboundMessage.status.in_(("pending", "failed")))
            .order_by(InboundMessage.created_at)
            .limit(limit)
        )
        return list(db.scalars(stmt).all())

    def delete_older_than(self, db: Session, cutoff: datetime) -> int:
        """Delete rows created before `cutoff`; returns the number removed."""
        result = db.execute(
            delete(InboundMessage).where(InboundMessage.created_at < cutoff)
        )
        db.commit()
        return result.rowcount or 0


crud_inbound_message = CRUDInboundMessage()
