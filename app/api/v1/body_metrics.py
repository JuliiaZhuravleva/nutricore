from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from app.db.session import get_db
from app.schemas.body_metric import BodyMetricCreate, BodyMetricUpdate, BodyMetric
from app.crud.crud_body_metric import crud_body_metric

router = APIRouter()


@router.get("/", response_model=List[BodyMetric])
def read_body_metrics(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    return crud_body_metric.get_multi(db, skip=skip, limit=limit)


@router.get("/{metric_id}", response_model=BodyMetric)
def read_body_metric(metric_id: int, db: Session = Depends(get_db)):
    db_metric = crud_body_metric.get(db, metric_id=metric_id)
    if not db_metric:
        raise HTTPException(status_code=404, detail="Body metric not found.")
    return db_metric


@router.post("/", response_model=BodyMetric)
def create_body_metric(metric_in: BodyMetricCreate, user_id: int, db: Session = Depends(get_db)):
    return crud_body_metric.create(db, metric_in, user_id)


@router.put("/{metric_id}", response_model=BodyMetric)
def update_body_metric(metric_id: int, metric_in: BodyMetricUpdate, db: Session = Depends(get_db)):
    db_metric = crud_body_metric.get(db, metric_id=metric_id)
    if not db_metric:
        raise HTTPException(status_code=404, detail="Body metric not found.")
    return crud_body_metric.update(db, db_metric, metric_in)


@router.delete("/{metric_id}", response_model=BodyMetric)
def delete_body_metric(metric_id: int, db: Session = Depends(get_db)):
    db_metric = crud_body_metric.get(db, metric_id=metric_id)
    if not db_metric:
        raise HTTPException(status_code=404, detail="Body metric not found.")
    return crud_body_metric.remove(db, metric_id)
