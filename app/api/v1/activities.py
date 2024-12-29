from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from app.db.session import get_db
from app.schemas.activity import ActivityCreate, ActivityUpdate, Activity
from app.crud.crud_activity import crud_activity

router = APIRouter()


@router.get("/", response_model=List[Activity])
def read_activities(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    return crud_activity.get_multi(db, skip=skip, limit=limit)


@router.get("/{activity_id}", response_model=Activity)
def read_activity(activity_id: int, db: Session = Depends(get_db)):
    db_act = crud_activity.get(db, activity_id=activity_id)
    if not db_act:
        raise HTTPException(status_code=404, detail="Activity not found.")
    return db_act


@router.post("/", response_model=Activity)
def create_activity(activity_in: ActivityCreate, user_id: int, db: Session = Depends(get_db)):
    return crud_activity.create(db, activity_in, user_id)


@router.put("/{activity_id}", response_model=Activity)
def update_activity(activity_id: int, activity_in: ActivityUpdate, db: Session = Depends(get_db)):
    db_act = crud_activity.get(db, activity_id=activity_id)
    if not db_act:
        raise HTTPException(status_code=404, detail="Activity not found.")
    return crud_activity.update(db, db_act, activity_in)


@router.delete("/{activity_id}", response_model=Activity)
def delete_activity(activity_id: int, db: Session = Depends(get_db)):
    db_act = crud_activity.get(db, activity_id=activity_id)
    if not db_act:
        raise HTTPException(status_code=404, detail="Activity not found.")
    return crud_activity.remove(db, activity_id)
