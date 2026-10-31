from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.meal import Meal
from app.models.meal_item import MealItem
from app.schemas.meal import MealCreate, MealUpdate


class CRUDMeal:
    def get(self, db: Session, meal_id: int) -> Meal | None:
        """The meal, unless it is deleted."""
        stmt = select(Meal).where(Meal.id == meal_id, Meal.deleted_at.is_(None))
        return db.execute(stmt).scalar_one_or_none()

    def get_any(self, db: Session, meal_id: int) -> Meal | None:
        """The meal, also when deleted: for undo and for a stale button's answer."""
        stmt = select(Meal).where(Meal.id == meal_id)
        return db.execute(stmt).scalar_one_or_none()

    def get_multi(self, db: Session, skip: int = 0, limit: int = 100) -> list[Meal]:
        stmt = select(Meal).where(Meal.deleted_at.is_(None)).offset(skip).limit(limit)
        return db.scalars(stmt).all()

    def get_for_export(
        self, db: Session, *, since: datetime | None = None
    ) -> list[Meal]:
        """All meals in meal_time order (optionally only those at/after ``since``).

        Ordered so a downstream incremental pull can page by time; no ``limit`` —
        the consumer dedups idempotently on the stable meal id.
        """
        stmt = select(Meal).where(Meal.deleted_at.is_(None)).order_by(Meal.meal_time)
        if since is not None:
            stmt = stmt.where(Meal.meal_time >= since)
        return list(db.scalars(stmt).all())

    def get_user_meals(self, db: Session, user_id: int) -> list[Meal]:
        stmt = (
            select(Meal)
            .where(Meal.user_id == user_id, Meal.deleted_at.is_(None))
            .order_by(Meal.meal_time.desc())
        )
        return list(db.scalars(stmt).all())

    def get_user_meals_between(
        self, db: Session, user_id: int, start: datetime, end: datetime
    ) -> list[Meal]:
        """The user's meals with ``start <= meal_time < end``, earliest first."""
        stmt = (
            select(Meal)
            .where(
                Meal.user_id == user_id,
                Meal.meal_time >= start,
                Meal.meal_time < end,
                Meal.deleted_at.is_(None),
            )
            .order_by(Meal.meal_time)
        )
        return list(db.scalars(stmt).all())

    def build(self, obj_in: MealCreate, user_id: int) -> Meal:
        """The row, not added and not committed: for a transaction that holds more."""
        return Meal(
            user_id=user_id,
            description=obj_in.description,
            meal_time=obj_in.meal_time,
            # NOTE: this constructor lists every column by hand, so a field added
            # to MealCreate is silently dropped until it is added here too — that
            # is how meal_type reached the schema and still never hit the DB.
            meal_type=obj_in.meal_type,
            calories=obj_in.calories,
            proteins=obj_in.proteins,
            fats=obj_in.fats,
            carbohydrates=obj_in.carbohydrates,
            nutrients=obj_in.nutrients,
            photos=obj_in.photos,
            ai_analysis=obj_in.ai_analysis,
            resolution_source=obj_in.resolution_source,
            resolution_signals=obj_in.resolution_signals,
            # None leaves these to the column defaults.
            sent_at=obj_in.sent_at,
            time_precision=obj_in.time_precision,
            time_source=obj_in.time_source,
            time_phrase=obj_in.time_phrase,
            numbers_basis=obj_in.numbers_basis,
            plate_totals=obj_in.plate_totals,
        )

    def create(self, db: Session, obj_in: MealCreate, user_id: int) -> Meal:
        db_obj = self.build(obj_in, user_id)
        db.add(db_obj)
        db.commit()
        db.refresh(db_obj)
        return db_obj

    def update(self, db: Session, db_obj: Meal, obj_in: MealUpdate) -> Meal:
        update_data = obj_in.dict(exclude_unset=True)
        changed = any(getattr(db_obj, k) != v for k, v in update_data.items())
        for key, value in update_data.items():
            setattr(db_obj, key, value)
        if changed:
            db_obj.rev = (db_obj.rev or 0) + 1
        db.commit()
        return db_obj

    def remove(self, db: Session, meal_id: int) -> Meal:
        stmt = select(Meal).where(Meal.id == meal_id)
        obj = db.execute(stmt).scalar_one_or_none()
        if obj:
            # Not left to the FK: SQLite ignores it and reuses the id, so another
            # meal would inherit these items.
            db.execute(delete(MealItem).where(MealItem.meal_id == meal_id))
            db.delete(obj)
            db.commit()
        return obj


crud_meal = CRUDMeal()
