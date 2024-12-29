from pydantic import BaseModel, ConfigDict
from typing import Optional, Any, Dict, List
from datetime import datetime


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
