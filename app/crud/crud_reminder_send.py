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

    def sent_after(
        self, db: Session, user_id: int, slot: str, after: datetime.datetime
    ) -> bool:
        """A send of this slot claimed after ``after`` (pass it in UTC: SQLite
        compares the stored text)."""
        stmt = select(ReminderSend.id).where(
            ReminderSend.user_id == user_id,
            ReminderSend.slot == slot,
            ReminderSend.sent_at > after,
        )
        return db.scalar(stmt.limit(1)) is not None

    def last_regular_day(
        self, db: Session, user_id: int, *, besides: str
    ) -> datetime.date | None:
        """The local day of the latest send whose kind is not ``besides``."""
        stmt = select(func.max(ReminderSend.local_day)).where(
            ReminderSend.user_id == user_id, ReminderSend.kind != besides
        )
        return db.scalar(stmt)

    def latest(
        self, db: Session, user_id: int, *, kind: str, statuses: tuple[str, ...]
    ) -> ReminderSend | None:
        """The newest send of ``kind`` whose status is one of ``statuses``."""
        stmt = (
            select(ReminderSend)
            .where(
                ReminderSend.user_id == user_id,
                ReminderSend.kind == kind,
                ReminderSend.status.in_(statuses),
            )
            .order_by(ReminderSend.sent_at.desc(), ReminderSend.id.desc())
            .limit(1)
        )
        return db.scalars(stmt).first()

    def any_received(
        self, db: Session, user_id: int, *, statuses: tuple[str, ...]
    ) -> bool:
        """Whether any send of any kind has one of ``statuses``."""
        stmt = select(ReminderSend.id).where(
            ReminderSend.user_id == user_id, ReminderSend.status.in_(statuses)
        )
        return db.scalar(stmt.limit(1)) is not None

    def previous(
        self, db: Session, user_id: int, before_id: int
    ) -> ReminderSend | None:
        """The latest earlier send that reached the chat: the one still standing
        there. Rows with no message (maybe, blocked) are walked past, so a reminder
        behind them still leaves (R4)."""
        stmt = (
            select(ReminderSend)
            .where(
                ReminderSend.user_id == user_id,
                ReminderSend.id < before_id,
                ReminderSend.message_id.is_not(None),
            )
            .order_by(ReminderSend.id.desc())
            .limit(1)
        )
        return db.scalars(stmt).first()

    def has_later(self, db: Session, user_id: int, after_id: int) -> bool:
        """Whether a send newer than ``after_id`` reached the chat (has a message)."""
        stmt = select(ReminderSend.id).where(
            ReminderSend.user_id == user_id,
            ReminderSend.id > after_id,
            ReminderSend.message_id.is_not(None),
        )
        return db.scalar(stmt.limit(1)) is not None

    def newest_first(
        self,
        db: Session,
        user_id: int,
        *,
        after: datetime.datetime | None,
        before_day: datetime.date,
    ) -> list[ReminderSend]:
        """Sends of days before ``before_day`` (a day still open can be answered),
        from ``after`` on when it is given, newest first. From, not after: a resume
        writes ``count_from = now`` and the same sweep sends at that very moment,
        and that send must count (R9: one unanswered after a return quiets)."""
        stmt = select(ReminderSend).where(
            ReminderSend.user_id == user_id, ReminderSend.local_day < before_day
        )
        if after is not None:
            stmt = stmt.where(ReminderSend.sent_at >= after)
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
