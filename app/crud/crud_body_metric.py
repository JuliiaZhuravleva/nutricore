from datetime import datetime
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.body_metric import BodyMetric
from app.schemas.body_metric import BodyMetricCreate, BodyMetricUpdate


class CRUDBodyMetric:
    def get(self, db: Session, metric_id: int) -> Optional[BodyMetric]:
        stmt = select(BodyMetric).where(BodyMetric.id == metric_id)
        return db.execute(stmt).scalar_one_or_none()

    def get_multi(
        self, db: Session, skip: int = 0, limit: int = 100
    ) -> List[BodyMetric]:
        stmt = select(BodyMetric).offset(skip).limit(limit)
        return list(db.execute(stmt).scalars().all())

    def get_user_metrics(self, db: Session, user_id: int) -> List[BodyMetric]:
        stmt = (
            select(BodyMetric)
            .where(BodyMetric.user_id == user_id)
            .order_by(BodyMetric.timestamp.desc())
        )
        return list(db.execute(stmt).scalars().all())

    def get_user_weights_since(
        self, db: Session, user_id: int, since: datetime
    ) -> List[BodyMetric]:
        """The user's weighings at/after ``since``, latest first."""
        stmt = (
            select(BodyMetric)
            .where(
                BodyMetric.user_id == user_id,
                BodyMetric.weight.is_not(None),
                BodyMetric.timestamp >= since,
            )
            .order_by(BodyMetric.timestamp.desc(), BodyMetric.id.desc())
        )
        return list(db.execute(stmt).scalars().all())

    def latest_weights(self, db: Session, user_id: int, limit: int) -> List[BodyMetric]:
        """The user's last ``limit`` weighings, latest first."""
        stmt = (
            select(BodyMetric)
            .where(BodyMetric.user_id == user_id, BodyMetric.weight.is_not(None))
            .order_by(BodyMetric.timestamp.desc(), BodyMetric.id.desc())
            .limit(limit)
        )
        return list(db.execute(stmt).scalars().all())

    def create(self, db: Session, obj_in: BodyMetricCreate, user_id: int) -> BodyMetric:
        db_obj = BodyMetric(
            user_id=user_id,
            timestamp=obj_in.timestamp,
            weight=obj_in.weight,
            metrics=obj_in.metrics,
        )
        db.add(db_obj)
        db.commit()
        db.refresh(db_obj)
        return db_obj

    def update(
        self, db: Session, db_obj: BodyMetric, obj_in: BodyMetricUpdate
    ) -> BodyMetric:
        if obj_in.timestamp is not None:
            db_obj.timestamp = obj_in.timestamp
        if obj_in.weight is not None:
            db_obj.weight = obj_in.weight
        if obj_in.metrics is not None:
            db_obj.metrics = obj_in.metrics
        db.commit()
        db.refresh(db_obj)
        return db_obj

    def remove(self, db: Session, metric_id: int) -> BodyMetric:
        stmt = select(BodyMetric).where(BodyMetric.id == metric_id)
        obj = db.execute(stmt).scalar_one_or_none()
        if obj:
            db.delete(obj)
            db.commit()
        return obj


crud_body_metric = CRUDBodyMetric()
