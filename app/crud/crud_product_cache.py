from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.product_cache import ProductCache
from app.schemas.product_cache import ProductCacheCreate, ProductCacheUpdate


class CRUDProductCache:
    def get_by_barcode(self, db: Session, barcode: str) -> ProductCache | None:
        """Look up a cached product by its barcode (EAN/UPC).

        Returns None on a cache miss — the caller should then hit the OFF API.
        """
        stmt = select(ProductCache).where(ProductCache.barcode == barcode)
        return db.execute(stmt).scalar_one_or_none()

    def create(self, db: Session, obj_in: ProductCacheCreate) -> ProductCache:
        db_obj = ProductCache(**obj_in.model_dump())
        db.add(db_obj)
        db.commit()
        db.refresh(db_obj)
        return db_obj

    def update(
        self, db: Session, db_obj: ProductCache, obj_in: ProductCacheUpdate
    ) -> ProductCache:
        """Refresh an existing cache entry (e.g. after an OFF re-fetch)."""
        update_data = obj_in.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(db_obj, key, value)
        db.commit()
        db.refresh(db_obj)
        return db_obj

    def get_or_create(
        self, db: Session, obj_in: ProductCacheCreate
    ) -> tuple[ProductCache, bool]:
        """Return (entry, created).

        If a row with the same barcode already exists, return it without
        creating a duplicate. ``created`` is True only when a new row was
        inserted.
        """
        existing = self.get_by_barcode(db, obj_in.barcode)
        if existing is not None:
            return existing, False
        return self.create(db, obj_in), True


crud_product_cache = CRUDProductCache()
