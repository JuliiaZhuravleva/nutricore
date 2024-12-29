from typing import Optional, List
from sqlalchemy.orm import Session

from app.models.meal import Meal
from app.schemas.meal import MealCreate, MealUpdate


class CRUDMeal:
    def get(self, db: Session, meal_id: int) -> Optional[Meal]:
        return db.query(Meal).filter(Meal.id == meal_id).first()

    def get_multi(self, db: Session, skip: int = 0, limit: int = 100) -> List[Meal]:
        return db.query(Meal).offset(skip).limit(limit).all()

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
        )
        db.add(db_obj)
        db.commit()
        db.refresh(db_obj)
        return db_obj

    def update(self, db: Session, db_obj: Meal, obj_in: MealUpdate) -> Meal:
        if obj_in.description is not None:
            db_obj.description = obj_in.description
        if obj_in.meal_time is not None:
            db_obj.meal_time = obj_in.meal_time
        if obj_in.calories is not None:
            db_obj.calories = obj_in.calories
        if obj_in.proteins is not None:
            db_obj.proteins = obj_in.proteins
        if obj_in.fats is not None:
            db_obj.fats = obj_in.fats
        if obj_in.carbohydrates is not None:
            db_obj.carbohydrates = obj_in.carbohydrates
        if obj_in.nutrients is not None:
            db_obj.nutrients = obj_in.nutrients
        if obj_in.photos is not None:
            db_obj.photos = obj_in.photos
        if obj_in.ai_analysis is not None:
            db_obj.ai_analysis = obj_in.ai_analysis
        db.commit()
        db.refresh(db_obj)
        return db_obj

    def remove(self, db: Session, meal_id: int) -> Meal:
        obj = db.query(Meal).get(meal_id)
        db.delete(obj)
        db.commit()
        return obj


crud_meal = CRUDMeal()
