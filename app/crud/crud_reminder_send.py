import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.reminder_send import ReminderSend


class CRUDReminderSend:
    def get(self, db: Session, send_id: int) -> ReminderSend | None:
        return db.get(ReminderSend, send_id)

    def claim(
        self,
        db: Session,
        *,
        user_id: int,
        local_day: datetime.date,
        slot: str,
        kind: str,
        chat_id: int,
        now: datetime.datetime,
        centre: str | None = None,
    ) -> ReminderSend | None:
        """The slot's row, or None when another sweep already has it. Marked before the
        send: a reminder lost to a killed worker beats one sent twice."""
        row = ReminderSend(
            user_id=user_id,
            local_day=local_day,
            slot=slot,
            kind=kind,
            centre=centre,
            sent_at=now,
            chat_id=chat_id,
            status="claimed",
        )
        db.add(row)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            return None
        return row

    def exists(
        self, db: Session, user_id: int, local_day: datetime.date, slot: str
    ) -> bool:
        stmt = select(ReminderSend.id).where(
            ReminderSend.user_id == user_id,
            ReminderSend.local_day == local_day,
            ReminderSend.slot == slot,
        )
        return db.scalar(stmt) is not None

    def count_on(
        self,
        db: Session,
        user_id: int,
        local_day: datetime.date,
        kind: str | None = None,
    ) -> int:
        stmt = select(func.count(ReminderSend.id)).where(
            ReminderSend.user_id == user_id, ReminderSend.local_day == local_day
        )
        if kind is not None:
            stmt = stmt.where(ReminderSend.kind == kind)
        return db.scalar(stmt) or 0

    def previous(
        self, db: Session, user_id: int, before_id: int
    ) -> ReminderSend | None:
        """The send right before this one: the one still standing in the chat."""
        stmt = (
            select(ReminderSend)
            .where(ReminderSend.user_id == user_id, ReminderSend.id < before_id)
            .order_by(ReminderSend.id.desc())
            .limit(1)
        )
        return db.scalars(stmt).first()

    def newest_first(
        self,
        db: Session,
        user_id: int,
        *,
        after: datetime.datetime | None,
        before_day: datetime.date,
    ) -> list[ReminderSend]:
        """Sends of days before ``before_day`` (a day still open can be answered),
        later than ``after`` when it is given, newest first."""
        stmt = select(ReminderSend).where(
            ReminderSend.user_id == user_id, ReminderSend.local_day < before_day
        )
        if after is not None:
            stmt = stmt.where(ReminderSend.sent_at > after)
        stmt = stmt.order_by(ReminderSend.sent_at.desc(), ReminderSend.id.desc())
        return list(db.scalars(stmt).all())

    def delete_older_than(self, db: Session, cutoff: datetime.datetime) -> int:
        result = db.execute(
            delete(ReminderSend)
            .where(ReminderSend.sent_at < cutoff)
            .execution_options(synchronize_session=False)
        )
        db.commit()
        return result.rowcount


crud_reminder_send = CRUDReminderSend()
