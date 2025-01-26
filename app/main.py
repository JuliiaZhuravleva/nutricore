from fastapi import FastAPI, Depends
from sqlalchemy.orm import Session
from telegram import Update
from telegram.ext import Application
import logging

from app.api.v1.users import router as users_router
from app.api.v1.meals import router as meals_router
from app.api.v1.body_metrics import router as body_metrics_router
from app.api.v1.activities import router as activities_router
from app.api.v1.analysis_reports import router as analysis_reports_router

from app.core.config import settings
from app.db.session import get_db
from app.models.user import User
from app.services.telegram import create_bot_application

logger = logging.getLogger(__name__)

app = FastAPI(
    title=settings.PROJECT_NAME,
    openapi_url=f"{settings.API_V1_STR}/openapi.json"
)

# Initialize bot application
bot_app = create_bot_application()

@app.post("/webhook")
async def webhook(update: dict):
    """Handle incoming Telegram webhook updates."""
    await bot_app.update_queue.put(Update.de_json(data=update, bot=bot_app.bot))
    return {"ok": True}

# Set webhook on startup
@app.on_event("startup")
async def setup_webhook():
    """Set up webhook for Telegram bot on startup."""
    webhook_url = settings.TELEGRAM_WEBHOOK_URL
    if webhook_url:
        await bot_app.bot.set_webhook(url=webhook_url)
        logger.info(f"Webhook set to {webhook_url}")
    else:
        logger.warning("TELEGRAM_WEBHOOK_URL not set, webhook not configured")

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