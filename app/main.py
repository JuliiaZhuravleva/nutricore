from fastapi import FastAPI, Depends
from sqlalchemy.orm import Session

from app.api.v1.users import router as users_router
from app.api.v1.meals import router as meals_router
from app.api.v1.body_metrics import router as body_metrics_router
from app.api.v1.activities import router as activities_router
from app.api.v1.analysis_reports import router as analysis_reports_router

from app.core.config import settings
from app.db.session import get_db
from app.models.user import User

app = FastAPI(
    title=settings.PROJECT_NAME,
    openapi_url=f"{settings.API_V1_STR}/openapi.json"
)


# @app.get("/test-db")
# def test_db(db: Session = Depends(get_db)):
#     try:
#         # Попробуем создать тестового пользователя
#         test_user = User(
#             telegram_id="123456789",
#             username="test_user"
#         )
#         db.add(test_user)
#         db.commit()
#         db.refresh(test_user)
#
#         # Получим пользователя обратно из БД
#         user = db.query(User).first()
#
#         return {
#             "status": "success",
#             "user": {
#                 "id": user.id,
#                 "telegram_id": user.telegram_id,
#                 "username": user.username,
#                 "created_at": user.created_at
#             }
#         }
#     except Exception as e:
#         return {"status": "error", "detail": str(e)}

app.include_router(users_router, prefix="/api/v1/users", tags=["users"])
app.include_router(meals_router, prefix="/api/v1/meals", tags=["meals"])
app.include_router(body_metrics_router, prefix="/api/v1/body-metrics", tags=["body_metrics"])
app.include_router(activities_router, prefix="/api/v1/activities", tags=["activities"])
app.include_router(analysis_reports_router, prefix="/api/v1/analysis-reports", tags=["analysis_reports"])

@app.get("/")
def read_root():
    return {"Hello": "World"}