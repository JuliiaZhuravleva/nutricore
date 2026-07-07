from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.meal import Meal
from app.schemas.meal import MealCreate, MealUpdate


class CRUDMeal:
    def get(self, db: Session, meal_id: int) -> Meal | None:
        stmt = select(Meal).where(Meal.id == meal_id)
        return db.execute(stmt).scalar_one_or_none()

    def get_multi(self, db: Session, skip: int = 0, limit: int = 100) -> list[Meal]:
        stmt = select(Meal).offset(skip).limit(limit)
        return db.scalars(stmt).all()

    def get_for_export(
        self, db: Session, *, since: datetime | None = None
    ) -> list[Meal]:
        """All meals in meal_time order (optionally only those at/after ``since``).

        Ordered so a downstream incremental pull can page by time; no ``limit`` —
        the consumer dedups idempotently on the stable meal id.
        """
        stmt = select(Meal).order_by(Meal.meal_time)
        if since is not None:
            stmt = stmt.where(Meal.meal_time >= since)
        return list(db.scalars(stmt).all())

    def get_user_meals(self, db: Session, user_id: int) -> list[Meal]:
        stmt = (
            select(Meal).where(Meal.user_id == user_id).order_by(Meal.meal_time.desc())
        )
        return list(db.scalars(stmt).all())

    def create(self, db: Session, obj_in: MealCreate, user_id: int) -> Meal:
        db_obj = Meal(
            user_id=user_id,
            description=obj_in.description,
            meal_time=obj_in.meal_time,
            calories=obj_in.calories,
            proteins=obj_in.proteins,
            fats=obj_in.fats,
            carbohydrates=obj_in.carbohydrates,
            nutrients=obj_in.nutrients,
            photos=obj_in.photos,
            ai_analysis=obj_in.ai_analysis,
            resolution_source=obj_in.resolution_source,
            resolution_signals=obj_in.resolution_signals,
        )
        db.add(db_obj)
        db.commit()
        db.refresh(db_obj)
        return db_obj

    def update(self, db: Session, db_obj: Meal, obj_in: MealUpdate) -> Meal:
        update_data = obj_in.dict(exclude_unset=True)
        for key, value in update_data.items():
            setattr(db_obj, key, value)
        db.commit()
        return db_obj

    def remove(self, db: Session, meal_id: int) -> Meal:
        stmt = select(Meal).where(Meal.id == meal_id)
        obj = db.execute(stmt).scalar_one_or_none()
        if obj:
            db.delete(obj)
            db.commit()
        return obj


crud_meal = CRUDMeal()
