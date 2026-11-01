from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict


class MealBase(BaseModel):
    description: Optional[str] = None
    meal_time: datetime
    # "Завтрак" | "Обед" | "Ужин", or None when the user picked "Сейчас".
    meal_type: Optional[str] = None
    calories: Optional[float] = None
    proteins: Optional[float] = None
    fats: Optional[float] = None
    carbohydrates: Optional[float] = None
    nutrients: Optional[Dict[str, Any]] = None
    photos: Optional[List[str]] = None
    ai_analysis: Optional[Dict[str, Any]] = None
    # Pipeline resolution tracking (photo-product-lookup, A1).
    # "barcode_off" | "name_off" | "label_ocr" | "vision" | None (legacy/unknown)
    resolution_source: Optional[str] = None
    # Key intermediate signals for transparency + misprediction analysis.
    resolution_signals: Optional[Dict[str, Any]] = None


# A default of None with no Optional in the type: leaving one of these out is fine,
# and an explicit null is refused, because the three columns are NOT NULL.
TimePrecision = Literal["exact", "approx", "day_only"]
TimeSource = Literal["sent", "said", "forward", "edited"]
NumbersBasis = Literal["items", "plate", "none"]


class MealCreate(MealBase):
    # On create and update only: not in the export payload.
    sent_at: Optional[datetime] = None
    time_precision: TimePrecision = None
    time_source: TimeSource = None
    time_phrase: Optional[str] = None
    numbers_basis: NumbersBasis = None
    plate_totals: Optional[Dict[str, Any]] = None


class MealUpdate(BaseModel):
    description: Optional[str] = None
    meal_time: Optional[datetime] = None
    # Without this, PUT /api/v1/meals/{id} accepted a corrected label with 200 and
    # changed nothing (crud_meal.update copies only the fields declared here).
    meal_type: Optional[str] = None
    calories: Optional[float] = None
    proteins: Optional[float] = None
    fats: Optional[float] = None
    carbohydrates: Optional[float] = None
    nutrients: Optional[Dict[str, Any]] = None
    photos: Optional[List[str]] = None
    ai_analysis: Optional[Dict[str, Any]] = None
    resolution_source: Optional[str] = None
    resolution_signals: Optional[Dict[str, Any]] = None
    # Not sent_at, which never moves, and not rev, which crud_meal.update counts.
    time_precision: TimePrecision = None
    time_source: TimeSource = None
    time_phrase: Optional[str] = None
    numbers_basis: NumbersBasis = None
    plate_totals: Optional[Dict[str, Any]] = None
    deleted_at: Optional[datetime] = None


class MealInDBBase(MealBase):
    id: int
    user_id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class Meal(MealInDBBase):
    pass


class MealInDB(MealInDBBase):
    pass


class ExportedMeal(Meal):
    # The only way a consumer holding this row can learn it was deleted.
    deleted_at: Optional[datetime] = None


class MealsExport(BaseModel):
    """Wrapper for the read-only meals export consumed by the my-health vault."""

    meals: List[ExportedMeal]
