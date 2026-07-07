import datetime

from sqlalchemy import Column, DateTime, Float, Integer, JSON, String, Text, func

from app.db.base import Base
from app.db.base_class import BaseClass

UTC = datetime.timezone.utc


class ProductCache(Base, BaseClass):
    """Cached product lookups from Open Food Facts (and future structured DBs).

    Keyed by barcode (EAN/UPC): a second scan of the same product hits the cache
    instead of re-calling the OFF API. Stores the per-100g КБЖУ needed for portion
    scaling plus the raw OFF JSON for future extensibility.

    created_at is NOT NULL + server_default so a row can never land NULL-dated and
    escape the retention purge (TD-006 lesson). Pruning is not implemented in round 1
    (products don't change often; the table is tiny), but the column is ready for it.
    """

    __tablename__ = "product_caches"

    id = Column(Integer, primary_key=True, index=True)
    # The barcode string (EAN-13, UPC-A, etc.) — the primary lookup key.
    barcode = Column(String, nullable=False, unique=True, index=True)
    # OFF's own product code (may equal the barcode; stored for traceability).
    off_code = Column(String, nullable=True)
    product_name = Column(Text, nullable=True)
    brand = Column(Text, nullable=True)
    # Per-100g macros — the canonical unit returned by OFF.
    calories_per_100g = Column(Float, nullable=True)
    proteins_per_100g = Column(Float, nullable=True)
    fats_per_100g = Column(Float, nullable=True)
    carbohydrates_per_100g = Column(Float, nullable=True)
    # Full OFF API response for future extensibility (Nutri-Score, allergens, etc.).
    raw_data = Column(JSON, nullable=True)
    # NOT NULL + server_default — TD-006 lesson.
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        default=lambda: datetime.datetime.now(UTC),
        index=True,
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        default=lambda: datetime.datetime.now(UTC),
        onupdate=lambda: datetime.datetime.now(UTC),
    )
