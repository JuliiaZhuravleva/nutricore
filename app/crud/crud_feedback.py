import datetime
from typing import Optional

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models.feedback import KINDS, Feedback

UTC = datetime.timezone.utc


def _for_owner(telegram_id: Optional[int]) -> list:
    conditions = [Feedback.was_food_at.is_(None)]
    if telegram_id is not None:
        conditions.append(Feedback.telegram_id == telegram_id)
    return conditions


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

    def mark_read(self, db: Session, feedback_id: int) -> bool:
        return self._stamp(db, feedback_id, Feedback.read_at)

    def _count(self, db: Session, *conditions) -> int:
        return db.scalar(select(func.count()).select_from(Feedback).where(*conditions))

    def count_for_owner(self, db: Session, telegram_id: Optional[int] = None) -> int:
        """What the owner's screens hold. A message that turned out to be food is a
        record now, and the owner's screens hold no records."""
        return self._count(db, *_for_owner(telegram_id))

    def count_was_food(self, db: Session, telegram_id: Optional[int] = None) -> int:
        conditions = [Feedback.was_food_at.is_not(None)]
        if telegram_id is not None:
            conditions.append(Feedback.telegram_id == telegram_id)
        return self._count(db, *conditions)

    def count_new(self, db: Session) -> int:
        return self._count(db, *_for_owner(None), Feedback.read_at.is_(None))

    def count_undelivered(self, db: Session, telegram_id: Optional[int] = None) -> int:
        return self._count(db, *_for_owner(telegram_id), Feedback.sent_at.is_(None))

    def nth_for_owner(
        self, db: Session, index: int, telegram_id: Optional[int] = None
    ) -> Optional[Feedback]:
        """One row, newest first: a page of the owner's screen."""
        return db.scalar(
            select(Feedback)
            .where(*_for_owner(telegram_id))
            .order_by(Feedback.created_at.desc(), Feedback.id.desc())
            .offset(index)
            .limit(1)
        )


crud_feedback = CRUDFeedback()
