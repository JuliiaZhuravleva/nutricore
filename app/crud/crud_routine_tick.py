from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.routine_tick import RoutineTick


class CRUDRoutineTick:
    def between(
        self, db: Session, user_id: int, first: date, last: date
    ) -> list[RoutineTick]:
        """The user's ticks from ``first`` to ``last``, both included."""
        stmt = select(RoutineTick).where(
            RoutineTick.user_id == user_id,
            RoutineTick.day >= first,
            RoutineTick.day <= last,
        )
        return list(db.scalars(stmt).all())

    def _get(
        self, db: Session, user_id: int, routine_id: str, day: date
    ) -> RoutineTick | None:
        stmt = select(RoutineTick).where(
            RoutineTick.user_id == user_id,
            RoutineTick.routine_id == routine_id,
            RoutineTick.day == day,
        )
        return db.execute(stmt).scalar_one_or_none()

    def tick(self, db: Session, user_id: int, routine_id: str, day: date) -> None:
        if self._get(db, user_id, routine_id, day) is None:
            db.add(RoutineTick(user_id=user_id, routine_id=routine_id, day=day))
            db.commit()

    def tick_flush(self, db: Session, user_id: int, routine_id: str, day: date) -> bool:
        """Ticks inside the caller's transaction: flushed, never committed. False: it
        was ticked already and nothing is written (a second row would break the
        unique (user, routine, day) key and abort the whole commit)."""
        if self._get(db, user_id, routine_id, day) is not None:
            return False
        db.add(RoutineTick(user_id=user_id, routine_id=routine_id, day=day))
        db.flush()
        return True

    def untick_flush(
        self, db: Session, user_id: int, routine_id: str, day: date
    ) -> bool:
        """Takes the tick back as ``untick`` does, flushed, never committed. False:
        there was none."""
        existing = self._get(db, user_id, routine_id, day)
        if existing is None:
            return False
        db.delete(existing)
        db.flush()
        return True

    def untick(self, db: Session, user_id: int, routine_id: str, day: date) -> None:
        existing = self._get(db, user_id, routine_id, day)
        if existing is not None:
            db.delete(existing)
            db.commit()


crud_routine_tick = CRUDRoutineTick()
