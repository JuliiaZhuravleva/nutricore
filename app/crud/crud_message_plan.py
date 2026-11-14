"""The plan of a message: one row per message read as several actions.

The unique key on (chat_id, message_id) is the claim, so ``open`` returning None means
the message is already planned (a replay or an edit) and nothing more may happen.
"""

from datetime import datetime
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.message_plan import MessagePlan

READING, DONE, FAILED, SINGLE = "reading", "done", "failed", "single"


class CRUDMessagePlan:
    READING, DONE, FAILED, SINGLE = READING, DONE, FAILED, SINGLE

    def open(self, db: Session, **fields) -> Optional[MessagePlan]:
        """A new plan in ``reading``, committed; None if the message has one."""
        plan = MessagePlan(status=READING, **fields)
        db.add(plan)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            return None
        db.refresh(plan)
        return plan

    def of_message(
        self, db: Session, chat_id: int, message_id: int
    ) -> Optional[MessagePlan]:
        return db.scalar(
            select(MessagePlan).where(
                MessagePlan.chat_id == chat_id, MessagePlan.message_id == message_id
            )
        )

    def reading_of(
        self, db: Session, user_id: int, since: datetime
    ) -> Optional[MessagePlan]:
        """The person's plan still being read, opened after ``since``."""
        return db.scalar(
            select(MessagePlan)
            .where(
                MessagePlan.user_id == user_id,
                MessagePlan.status == READING,
                MessagePlan.created_at >= since,
            )
            .order_by(MessagePlan.id.desc())
            .limit(1)
        )

    def stuck(self, db: Session, before: datetime) -> List[MessagePlan]:
        """Plans still reading, opened before ``before``: a restart lost them."""
        return list(
            db.scalars(
                select(MessagePlan)
                .where(MessagePlan.status == READING, MessagePlan.created_at < before)
                .order_by(MessagePlan.id)
            )
        )

    def owed(self, db: Session) -> List[MessagePlan]:
        """Done plans whose messages never went out: ``line_ids`` is written, even as
        [], once they are sent."""
        return list(
            db.scalars(
                select(MessagePlan)
                .where(MessagePlan.status == DONE, MessagePlan.line_ids.is_(None))
                .order_by(MessagePlan.id)
            )
        )


crud_message_plan = CRUDMessagePlan()
