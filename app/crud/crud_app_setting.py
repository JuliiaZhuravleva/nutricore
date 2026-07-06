from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.app_setting import AppSetting


class CRUDAppSetting:
    def get(self, db: Session, key: str) -> Optional[str]:
        row = db.execute(
            select(AppSetting).where(AppSetting.key == key)
        ).scalar_one_or_none()
        return row.value if row else None

    def set(self, db: Session, key: str, value: str) -> AppSetting:
        """Upsert a setting by key; returns the stored row."""
        row = db.execute(
            select(AppSetting).where(AppSetting.key == key)
        ).scalar_one_or_none()
        if row is None:
            row = AppSetting(key=key, value=value)
            db.add(row)
        else:
            row.value = value
        try:
            db.commit()
        except Exception:
            # Roll back so the session isn't left mid-transaction (matches
            # crud_subscription); the best-effort caller handles the re-raise.
            db.rollback()
            raise
        db.refresh(row)
        return row


crud_app_setting = CRUDAppSetting()
