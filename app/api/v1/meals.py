from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.deps import require_export_token
from app.crud.crud_meal import crud_meal
from app.crud.crud_meal_item import crud_meal_item
from app.db.session import get_db
from app.schemas.meal import Meal, MealCreate, MealsExport, MealUpdate
from app.services import meal_items

router = APIRouter()

# A meal with items holds these only as their sum (3.1): written here they would be
# overwritten by the next change and refuse every undo in between.
_OWNED_BY_ITEMS = frozenset(
    {
        "description",
        "calories",
        "proteins",
        "fats",
        "carbohydrates",
        "nutrients",
        "ai_analysis",
        "numbers_basis",
        "plate_totals",
    }
)


@router.get("/", response_model=List[Meal])
def read_meals(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    return crud_meal.get_multi(db, skip=skip, limit=limit)


# Declared before /{meal_id} so "export" isn't captured as a meal_id path param.
@router.get(
    "/export",
    response_model=MealsExport,
    dependencies=[Depends(require_export_token)],
)
def export_meals(
    since: Optional[datetime] = None,
    updated_since: Optional[datetime] = None,
    db: Session = Depends(get_db),
):
    """Read-only meals export for the my-health vault (token-guarded; single-user
    instance → all meals; ``since`` for incremental pulls on ``meal_time``;
    ``updated_since`` for what changed, deleted meals included, with ``deleted_at``)."""
    meals = crud_meal.get_for_export(db, since=since, updated_since=updated_since)
    return MealsExport(meals=meals)


@router.get("/{meal_id}", response_model=Meal)
def read_meal(meal_id: int, db: Session = Depends(get_db)):
    db_meal = crud_meal.get(db, meal_id=meal_id)
    if not db_meal:
        raise HTTPException(status_code=404, detail="Meal not found.")
    return db_meal


@router.post("/", response_model=Meal)
def create_meal(meal_in: MealCreate, user_id: int, db: Session = Depends(get_db)):
    return meal_items.save_meal(db, meal_in, user_id)


@router.put("/{meal_id}", response_model=Meal)
def update_meal(meal_id: int, meal_in: MealUpdate, db: Session = Depends(get_db)):
    db_meal = crud_meal.get(db, meal_id=meal_id)
    if not db_meal:
        raise HTTPException(status_code=404, detail="Meal not found.")
    sent = meal_in.model_fields_set
    if "deleted_at" in sent:
        raise HTTPException(status_code=409, detail="Delete a meal with DELETE.")
    if sent & _OWNED_BY_ITEMS and crud_meal_item.live(db, meal_id):
        raise HTTPException(
            status_code=409, detail="These fields follow the meal's items."
        )
    return crud_meal.update(db, db_meal, meal_in)


@router.delete("/{meal_id}", response_model=Meal)
def delete_meal(meal_id: int, db: Session = Depends(get_db)):
    db_meal = crud_meal.get(db, meal_id=meal_id)
    if not db_meal:
        raise HTTPException(status_code=404, detail="Meal not found.")
    return meal_items.delete_meal(db, db_meal.user_id, meal_id).meal
