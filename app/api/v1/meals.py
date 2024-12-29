from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from app.db.session import get_db
from app.schemas.meal import MealCreate, MealUpdate, Meal
from app.crud.crud_meal import crud_meal

router = APIRouter()


@router.get("/", response_model=List[Meal])
def read_meals(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    return crud_meal.get_multi(db, skip=skip, limit=limit)


@router.get("/{meal_id}", response_model=Meal)
def read_meal(meal_id: int, db: Session = Depends(get_db)):
    db_meal = crud_meal.get(db, meal_id=meal_id)
    if not db_meal:
        raise HTTPException(status_code=404, detail="Meal not found.")
    return db_meal


@router.post("/", response_model=Meal)
def create_meal(meal_in: MealCreate, user_id: int, db: Session = Depends(get_db)):
    return crud_meal.create(db, meal_in, user_id)


@router.put("/{meal_id}", response_model=Meal)
def update_meal(meal_id: int, meal_in: MealUpdate, db: Session = Depends(get_db)):
    db_meal = crud_meal.get(db, meal_id=meal_id)
    if not db_meal:
        raise HTTPException(status_code=404, detail="Meal not found.")
    return crud_meal.update(db, db_meal, meal_in)


@router.delete("/{meal_id}", response_model=Meal)
def delete_meal(meal_id: int, db: Session = Depends(get_db)):
    db_meal = crud_meal.get(db, meal_id=meal_id)
    if not db_meal:
        raise HTTPException(status_code=404, detail="Meal not found.")
    return crud_meal.remove(db, meal_id)
