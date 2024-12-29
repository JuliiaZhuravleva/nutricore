from typing import Optional, List
from sqlalchemy.orm import Session

from app.models.activity import Activity
from app.schemas.activity import ActivityCreate, ActivityUpdate


class CRUDActivity:
    def get(self, db: Session, activity_id: int) -> Optional[Activity]:
        return db.query(Activity).filter(Activity.id == activity_id).first()

    def get_multi(self, db: Session, skip: int = 0, limit: int = 100) -> List[Activity]:
        return db.query(Activity).offset(skip).limit(limit).all()

    def create(self, db: Session, obj_in: ActivityCreate, user_id: int) -> Activity:
        db_obj = Activity(
            user_id=user_id,
            timestamp=obj_in.timestamp,
            activity_type=obj_in.activity_type,
            duration=obj_in.duration,
            calories_burned=obj_in.calories_burned,
            metrics=obj_in.metrics,
        )
        db.add(db_obj)
        db.commit()
        db.refresh(db_obj)
        return db_obj

    def update(self, db: Session, db_obj: Activity, obj_in: ActivityUpdate) -> Activity:
        if obj_in.timestamp is not None:
            db_obj.timestamp = obj_in.timestamp
        if obj_in.activity_type is not None:
            db_obj.activity_type = obj_in.activity_type
        if obj_in.duration is not None:
            db_obj.duration = obj_in.duration
        if obj_in.calories_burned is not None:
            db_obj.calories_burned = obj_in.calories_burned
        if obj_in.metrics is not None:
            db_obj.metrics = obj_in.metrics
        db.commit()
        db.refresh(db_obj)
        return db_obj

    def remove(self, db: Session, activity_id: int) -> Activity:
        obj = db.query(Activity).get(activity_id)
        db.delete(obj)
        db.commit()
        return obj


crud_activity = CRUDActivity()
