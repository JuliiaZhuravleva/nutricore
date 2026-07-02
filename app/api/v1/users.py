from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.user import UserCreate, UserUpdate, User
from app.crud.crud_user import crud_user

router = APIRouter()


@router.post("/", response_model=User)
def create_user(user_in: UserCreate, db: Session = Depends(get_db)):
    db_user = crud_user.get_by_telegram_id(db, telegram_id=user_in.telegram_id)
    if db_user:
        raise HTTPException(status_code=400, detail="User already exists.")
    return crud_user.create(db, obj_in=user_in)


@router.get("/{user_id}", response_model=User)
def read_user(user_id: int, db: Session = Depends(get_db)):
    db_user = crud_user.get(db, user_id=user_id)
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found.")
    return db_user


@router.put("/{user_id}", response_model=User)
def update_user(user_id: int, user_in: UserUpdate, db: Session = Depends(get_db)):
    db_user = crud_user.get(db, user_id=user_id)
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found.")
    return crud_user.update(db, db_user, user_in)


@router.delete("/{user_id}", response_model=User)
def delete_user(user_id: int, db: Session = Depends(get_db)):
    db_user = crud_user.get(db, user_id=user_id)
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found.")
    return crud_user.remove(db, user_id)
