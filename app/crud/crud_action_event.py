import datetime
import logging
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models.action_event import (
    ACTION_KINDS,
    ALLOWED_LABELS,
    PERSON_KINDS,
    ActionEvent,
)
from app.models.user import User

logger = logging.getLogger(__name__)
UTC = datetime.timezone.utc


def _utc(moment: datetime.datetime) -> datetime.datetime:
    # SQLite gives a stored moment back with no zone; it went in as UTC.
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


class CRUDActionEvent:
    def add(
        self,
        db: Session,
        *,
        telegram_id: int,
        kind: str,
        label: Optional[str] = None,
        at: Optional[datetime.datetime] = None,
    ) -> ActionEvent:
        """Flushes only. The last gate of the privacy rule, for every writer: a label
        that is not on the kind's closed list is dropped, and the kind stays."""
        if kind not in ACTION_KINDS:
            raise ValueError(f"unknown action kind: {kind}")
        if label is not None and label not in ALLOWED_LABELS.get(kind, ()):
            # The kind only: what was refused may be someone's food.
            logger.warning("Action journal refused a label on a %s row", kind)
            label = None
        event = ActionEvent(telegram_id=telegram_id, kind=kind, label=label)
        if at is not None:
            event.at = at
        db.add(event)
        db.flush()
        return event

    def add_for_user(self, db: Session, *, user_id: int, kind: str, label: str) -> None:
        """Beside a change in this session, and committed with it. In a savepoint, so
        that a journal that cannot write leaves the change standing. Never raises for the
        journal's sake; the change's own writes are flushed first, outside the guard, and
        fail as they always did."""
        db.flush()
        try:
            telegram_id = db.scalar(select(User.telegram_id).where(User.id == user_id))
            if not isinstance(telegram_id, int):
                return
            with db.begin_nested():
                self.add(db, telegram_id=telegram_id, kind=kind, label=label)
        except Exception as e:
            logger.warning(
                "Action journal: a %s not written: %s", kind, type(e).__name__
            )

    def last_actions(
        self, db: Session, telegram_id: int, limit: int = 20
    ) -> list[ActionEvent]:
        """A person's last ``limit`` rows, oldest first, as they are read."""
        rows = db.scalars(
            select(ActionEvent)
            .where(ActionEvent.telegram_id == telegram_id)
            .order_by(ActionEvent.at.desc(), ActionEvent.id.desc())
            .limit(limit)
        ).all()
        return list(reversed(rows))

    def counts_by_kind(
        self, db: Session, telegram_id: int, *, since: datetime.datetime
    ) -> dict[str, int]:
        """{kind: rows} from ``since`` (aware) on. A kind with none is not a key."""
        rows = db.execute(
            select(ActionEvent.kind, func.count())
            .where(
                ActionEvent.telegram_id == telegram_id,
                ActionEvent.at >= since.astimezone(UTC),
            )
            .group_by(ActionEvent.kind)
        )
        return {kind: count for kind, count in rows}

    def last_seen(self, db: Session, telegram_id: int) -> Optional[datetime.datetime]:
        """When the person last did something, in UTC: a read that ended later is not
        them. None once the rows are past their retention."""
        return self.last_seen_all(db, only=telegram_id).get(telegram_id)

    def last_seen_all(
        self, db: Session, *, only: Optional[int] = None
    ) -> dict[int, datetime.datetime]:
        statement = (
            select(ActionEvent.telegram_id, func.max(ActionEvent.at))
            .where(ActionEvent.kind.in_(PERSON_KINDS))
            .group_by(ActionEvent.telegram_id)
        )
        if only is not None:
            statement = statement.where(ActionEvent.telegram_id == only)
        return {person: _utc(at) for person, at in db.execute(statement)}

    def active_days(
        self,
        db: Session,
        telegram_id: int,
        *,
        zone: ZoneInfo,
        days: int = 7,
        now: Optional[datetime.datetime] = None,
    ) -> list[bool]:
        """One flag a day, oldest first, the last being today on the person's clock:
        ▰▰▱▰▰▰▱. In Python, because a day's edge is a zone's and the two databases
        share no way of saying that."""
        today = (now or datetime.datetime.now(UTC)).astimezone(zone).date()
        first = today - datetime.timedelta(days=days - 1)
        # A wide net in UTC; the exact day of each row is decided below.
        since = datetime.datetime.combine(first, datetime.time.min, tzinfo=UTC)
        moments = db.scalars(
            select(ActionEvent.at).where(
                ActionEvent.telegram_id == telegram_id,
                ActionEvent.kind.in_(PERSON_KINDS),
                ActionEvent.at >= since - datetime.timedelta(days=1),
            )
        )
        seen = {_utc(moment).astimezone(zone).date() for moment in moments}
        return [first + datetime.timedelta(days=i) in seen for i in range(days)]

    def delete_older_than(self, db: Session, cutoff: datetime.datetime) -> int:
        result = db.execute(
            delete(ActionEvent)
            .where(ActionEvent.at < cutoff)
            .execution_options(synchronize_session=False)
        )
        db.commit()
        return result.rowcount


crud_action_event = CRUDActionEvent()
