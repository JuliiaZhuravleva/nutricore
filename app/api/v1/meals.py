from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.core.config import settings
from app.crud.crud_meal import crud_meal
from app.db.session import get_db
from app.schemas.meal import Meal, MealCreate, MealsExport, MealUpdate

router = APIRouter()


def _require_export_token(token: Optional[str]) -> None:
    """Gate the read-only export on a dedicated token (on top of the router-level
    X-API-Token). Disabled — always 403 — when EXPORT_API_TOKEN is unset, so
    exposure is opt-in and fail-closed."""
    expected = settings.EXPORT_API_TOKEN
    if not expected or token != expected:
        raise HTTPException(status_code=403, detail="Invalid or missing export token.")


@router.get("/", response_model=List[Meal])
def read_meals(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    return crud_meal.get_multi(db, skip=skip, limit=limit)


# Declared before /{meal_id} so "export" isn't captured as a meal_id path param.
@router.get("/export", response_model=MealsExport)
def export_meals(
    since: Optional[datetime] = None,
    x_export_token: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    """Read-only meals export for the my-health vault (token-guarded; single-user
    instance → all meals; ``since`` for incremental pulls)."""
    _require_export_token(x_export_token)
    return MealsExport(meals=crud_meal.get_for_export(db, since=since))


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
