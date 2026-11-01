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

    def untick(self, db: Session, user_id: int, routine_id: str, day: date) -> None:
        existing = self._get(db, user_id, routine_id, day)
        if existing is not None:
            db.delete(existing)
            db.commit()


crud_routine_tick = CRUDRoutineTick()
