from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict


class MealBase(BaseModel):
    description: Optional[str] = None
    meal_time: datetime
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


class MealCreate(MealBase):
    pass


class MealUpdate(BaseModel):
    description: Optional[str] = None
    meal_time: Optional[datetime] = None
    calories: Optional[float] = None
    proteins: Optional[float] = None
    fats: Optional[float] = None
    carbohydrates: Optional[float] = None
    nutrients: Optional[Dict[str, Any]] = None
    photos: Optional[List[str]] = None
    ai_analysis: Optional[Dict[str, Any]] = None
    resolution_source: Optional[str] = None
    resolution_signals: Optional[Dict[str, Any]] = None


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


class MealsExport(BaseModel):
    """Wrapper for the read-only meals export consumed by the my-health vault."""

    meals: List[Meal]
