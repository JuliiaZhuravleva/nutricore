from fastapi import FastAPI, Depends
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.models.user import User

app = FastAPI(
    title=settings.PROJECT_NAME,
    openapi_url=f"{settings.API_V1_STR}/openapi.json"
)


@app.get("/test-db")
def test_db(db: Session = Depends(get_db)):
    try:
        # Попробуем создать тестового пользователя
        test_user = User(
            telegram_id="123456789",
            username="test_user"
        )
        db.add(test_user)
        db.commit()
        db.refresh(test_user)

        # Получим пользователя обратно из БД
        user = db.query(User).first()

        return {
            "status": "success",
            "user": {
                "id": user.id,
                "telegram_id": user.telegram_id,
                "username": user.username,
                "created_at": user.created_at
            }
        }
    except Exception as e:
        return {"status": "error", "detail": str(e)}


@app.get("/")
def read_root():
    return {"Hello": "World"}