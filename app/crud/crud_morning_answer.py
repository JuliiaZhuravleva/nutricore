import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.morning_answer import MorningAnswer

LITTLE = "little"


class CRUDMorningAnswer:
    def get(self, db: Session, row_id: int) -> MorningAnswer | None:
        return db.get(MorningAnswer, row_id)

    def for_day(
        self, db: Session, user_id: int, day: datetime.date
    ) -> MorningAnswer | None:
        stmt = select(MorningAnswer).where(
            MorningAnswer.user_id == user_id, MorningAnswer.day == day
        )
        return db.scalars(stmt).first()

    def claim(
        self,
        db: Session,
        *,
        user_id: int,
        day: datetime.date,
        chat_id: int,
        now: datetime.datetime,
    ) -> MorningAnswer | None:
        """The day's row, or None when another sweep already has it. Marked before the
        send: a question lost to a killed worker beats one sent twice."""
        row = MorningAnswer(
            user_id=user_id, day=day, sent_at=now, chat_id=chat_id, status="claimed"
        )
        db.add(row)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            return None
        return row

    def previous(
        self, db: Session, user_id: int, before_id: int
    ) -> MorningAnswer | None:
        """The latest earlier question that reached the chat (has a message)."""
        stmt = (
            select(MorningAnswer)
            .where(
                MorningAnswer.user_id == user_id,
                MorningAnswer.id < before_id,
                MorningAnswer.message_id.is_not(None),
            )
            .order_by(MorningAnswer.id.desc())
            .limit(1)
        )
        return db.scalars(stmt).first()

    def little_days(
        self,
        db: Session,
        user_id: int,
        first: datetime.date,
        last: datetime.date,
    ) -> set[datetime.date]:
        """The days in ``first``..``last`` answered «почти нет»."""
        stmt = select(MorningAnswer.day).where(
            MorningAnswer.user_id == user_id,
            MorningAnswer.answer == LITTLE,
            MorningAnswer.day >= first,
            MorningAnswer.day <= last,
        )
        return set(db.scalars(stmt).all())


crud_morning_answer = CRUDMorningAnswer()
