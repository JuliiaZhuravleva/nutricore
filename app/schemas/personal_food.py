"""Pydantic schemas for PersonalFood + PersonalFoodEmbedding (ADR-0003 / B1)."""

from __future__ import annotations

import datetime
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, ConfigDict


# ---------------------------------------------------------------------------
# PersonalFood schemas
# ---------------------------------------------------------------------------


class PersonalFoodBase(BaseModel):
    """Fields shared across create / update / response."""

    canonical_name: str
    brand: Optional[str] = None
    per_100g_calories: Optional[Decimal] = None
    per_100g_proteins: Optional[Decimal] = None
    per_100g_fats: Optional[Decimal] = None
    per_100g_carbs: Optional[Decimal] = None
    resolution_source: Optional[str] = None
    barcode: Optional[str] = None


class PersonalFoodCreate(PersonalFoodBase):
    """Fields required to create a new personal food record."""

    user_id: int
    meal_id: Optional[int] = None


class PersonalFoodUpdate(BaseModel):
    """All fields optional — patch semantics.

    Used by upsert() when a canonical-name collision is detected (update macros,
    provenance, and bump usage counters).
    """

    brand: Optional[str] = None
    per_100g_calories: Optional[Decimal] = None
    per_100g_proteins: Optional[Decimal] = None
    per_100g_fats: Optional[Decimal] = None
    per_100g_carbs: Optional[Decimal] = None
    meal_id: Optional[int] = None
    resolution_source: Optional[str] = None
    barcode: Optional[str] = None


class PersonalFoodInDBBase(PersonalFoodBase):
    id: int
    user_id: int
    meal_id: Optional[int] = None
    times_used: int
    last_used_at: Optional[datetime.datetime] = None
    created_at: datetime.datetime
    updated_at: datetime.datetime

    model_config = ConfigDict(from_attributes=True)


class PersonalFood(PersonalFoodInDBBase):
    """API / response schema for a personal food record."""

    pass


# ---------------------------------------------------------------------------
# PersonalFoodEmbedding schemas
# ---------------------------------------------------------------------------


class PersonalFoodEmbeddingBase(BaseModel):
    personal_food_id: int
    text_embedded: str


class PersonalFoodEmbeddingCreate(PersonalFoodEmbeddingBase):
    """Embedding values as a flat list of floats."""

    embedding: List[float]


class PersonalFoodEmbeddingInDBBase(PersonalFoodEmbeddingBase):
    id: int
    created_at: datetime.datetime

    model_config = ConfigDict(from_attributes=True)


class PersonalFoodEmbedding(PersonalFoodEmbeddingInDBBase):
    """API / response schema for an embedding row."""

    pass
