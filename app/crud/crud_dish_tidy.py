import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.dish_tidy import DishTidyBatch, DishTidyItem

# A batch whose message may be in the chat with its buttons alive.
OPEN = ("sent", "maybe", "retry")
# A row the person turned down: not offered again for a while (``dish_tidy.Decline``).
DECLINED = ("declined", "undone")


class CRUDDishTidy:
    def get(self, db: Session, batch_id: int) -> DishTidyBatch | None:
        return db.get(DishTidyBatch, batch_id)

    def claim(
        self,
        db: Session,
        *,
        user_id: int,
        week: datetime.date | None,
        group_id: str,
        now: datetime.datetime,
    ) -> DishTidyBatch | None:
        """The week's batch, or None when another sweep already has it. Committed
        before anything is computed: a tidy-up lost to a killed worker beats one done
        twice. An on-demand batch (no week) is never refused here."""
        batch = DishTidyBatch(
            user_id=user_id,
            week=week,
            group_id=group_id,
            state="claimed",
            tries=0,
            claimed_at=now,
        )
        db.add(batch)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            return None
        return batch

    def for_week(
        self, db: Session, user_id: int, week: datetime.date
    ) -> DishTidyBatch | None:
        stmt = select(DishTidyBatch).where(
            DishTidyBatch.user_id == user_id, DishTidyBatch.week == week
        )
        return db.scalars(stmt).first()

    def items(self, db: Session, batch_id: int) -> list[DishTidyItem]:
        stmt = (
            select(DishTidyItem)
            .where(DishTidyItem.batch_id == batch_id)
            .order_by(DishTidyItem.idx)
        )
        return list(db.scalars(stmt).all())

    def open_batches(
        self, db: Session, user_id: int, but: int | None = None
    ) -> list[DishTidyBatch]:
        """The person's batches still open, newest first."""
        stmt = (
            select(DishTidyBatch)
            .where(DishTidyBatch.user_id == user_id, DishTidyBatch.state.in_(OPEN))
            .order_by(DishTidyBatch.id.desc())
        )
        if but is not None:
            stmt = stmt.where(DishTidyBatch.id != but)
        return list(db.scalars(stmt).all())

    def in_state(self, db: Session, *states: str) -> list[DishTidyBatch]:
        stmt = (
            select(DishTidyBatch)
            .where(DishTidyBatch.state.in_(states))
            .order_by(DishTidyBatch.id)
        )
        return list(db.scalars(stmt).all())

    def declines(self, db: Session, user_id: int, since: datetime.datetime) -> dict:
        """key → (fingerprint, when) of the rows the person turned down since
        ``since``, the latest per key."""
        stmt = (
            select(DishTidyItem.key, DishTidyItem.fingerprint, DishTidyItem.decided_at)
            .join(DishTidyBatch, DishTidyBatch.id == DishTidyItem.batch_id)
            .where(
                DishTidyBatch.user_id == user_id,
                DishTidyItem.state.in_(DECLINED),
                DishTidyItem.decided_at.is_not(None),
                DishTidyItem.decided_at >= since,
            )
            .order_by(DishTidyItem.decided_at)
        )
        return {key: (fp, at) for key, fp, at in db.execute(stmt)}


crud_dish_tidy = CRUDDishTidy()
