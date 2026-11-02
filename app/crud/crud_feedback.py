import datetime
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.feedback import KINDS, Feedback

UTC = datetime.timezone.utc


class CRUDFeedback:
    def create(
        self,
        db: Session,
        *,
        telegram_id: int,
        kind: str,
        original: dict,
        text: Optional[str] = None,
        file_id: Optional[str] = None,
        journal: Optional[str] = None,
    ) -> Feedback:
        """Committed before this returns: the promise is that the row comes first."""
        if kind not in KINDS:
            raise ValueError(f"unknown feedback kind: {kind}")
        row = Feedback(
            telegram_id=telegram_id,
            kind=kind,
            text=text,
            file_id=file_id,
            journal=journal,
            original=original,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row

    def get(self, db: Session, feedback_id: int) -> Optional[Feedback]:
        return db.get(Feedback, feedback_id)

    def get_own(
        self, db: Session, feedback_id: int, telegram_id: int
    ) -> Optional[Feedback]:
        """None for a row that is not there and for someone else's alike."""
        return db.scalar(
            select(Feedback).where(
                Feedback.id == feedback_id, Feedback.telegram_id == telegram_id
            )
        )

    def set_transcript(self, db: Session, feedback_id: int, words: str) -> None:
        db.execute(
            update(Feedback)
            .where(Feedback.id == feedback_id)
            .values(transcript=words)
            .execution_options(synchronize_session=False)
        )
        db.commit()

    def _stamp(self, db: Session, feedback_id: int, column) -> bool:
        """True for the one who set it: a second tap or a retry finds it taken."""
        result = db.execute(
            update(Feedback)
            .where(Feedback.id == feedback_id, column.is_(None))
            .values({column.key: datetime.datetime.now(UTC)})
            .execution_options(synchronize_session=False)
        )
        db.commit()
        return result.rowcount == 1

    def mark_sent(self, db: Session, feedback_id: int) -> bool:
        return self._stamp(db, feedback_id, Feedback.sent_at)

    def mark_food(self, db: Session, feedback_id: int) -> bool:
        return self._stamp(db, feedback_id, Feedback.was_food_at)


crud_feedback = CRUDFeedback()
