from sqlalchemy.orm import Session
from sqlalchemy import select
from typing import Optional, List, Type, Any

from app.models.user import User
from app.schemas.user import UserCreate, UserUpdate


class CRUDUser:
    def get(self, db: Session, user_id: int) -> Optional[User]:
        stmt = select(User).where(User.id == user_id)
        return db.execute(stmt).scalar_one_or_none()

    def get_by_telegram_id(self, db: Session, telegram_id: int) -> Optional[User]:
        """Get user by telegram_id."""
        stmt = select(User).where(User.telegram_id == telegram_id)
        return db.execute(stmt).scalar_one_or_none()

    def create(self, db: Session, *, obj_in: UserCreate) -> User:
        """Create new user."""
        db_obj = User(
            telegram_id=obj_in.telegram_id,
            username=obj_in.username,
            diet_preferences=obj_in.diet_preferences,
            target_metrics=obj_in.target_metrics,
            settings=obj_in.settings
        )
        db.add(db_obj)
        db.commit()
        db.refresh(db_obj)
        return db_obj

    def update(self, db: Session, db_obj: User, obj_in: UserUpdate) -> User:
        if obj_in.username is not None:
            db_obj.username = obj_in.username
        if obj_in.diet_preferences is not None:
            db_obj.diet_preferences = obj_in.diet_preferences
        if obj_in.target_metrics is not None:
            db_obj.target_metrics = obj_in.target_metrics
        db.commit()
        db.refresh(db_obj)
        return db_obj

    def remove(self, db: Session, user_id: int) -> User:
        stmt = select(User).where(User.id == user_id)
        obj = db.execute(stmt).scalar_one_or_none()
        if not obj:
            raise ValueError("User not found")
        db.delete(obj)
        db.commit()
        return obj


crud_user = CRUDUser()