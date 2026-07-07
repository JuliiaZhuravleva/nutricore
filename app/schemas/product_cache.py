from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict


class ProductCacheBase(BaseModel):
    barcode: str
    off_code: Optional[str] = None
    product_name: Optional[str] = None
    brand: Optional[str] = None
    calories_per_100g: Optional[float] = None
    proteins_per_100g: Optional[float] = None
    fats_per_100g: Optional[float] = None
    carbohydrates_per_100g: Optional[float] = None
    raw_data: Optional[Dict[str, Any]] = None


class ProductCacheCreate(ProductCacheBase):
    pass


class ProductCacheUpdate(BaseModel):
    """Fields that may be refreshed when the OFF entry is re-fetched."""

    product_name: Optional[str] = None
    brand: Optional[str] = None
    calories_per_100g: Optional[float] = None
    proteins_per_100g: Optional[float] = None
    fats_per_100g: Optional[float] = None
    carbohydrates_per_100g: Optional[float] = None
    raw_data: Optional[Dict[str, Any]] = None


class ProductCacheInDBBase(ProductCacheBase):
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ProductCache(ProductCacheInDBBase):
    pass
