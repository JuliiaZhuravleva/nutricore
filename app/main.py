from fastapi import FastAPI, Depends
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from telegram import Update
import logging

from app.api.v1.users import router as users_router
from app.api.v1.meals import router as meals_router
from app.api.v1.body_metrics import router as body_metrics_router
from app.api.v1.activities import router as activities_router
from app.api.v1.analysis_reports import router as analysis_reports_router

from app.core.config import settings
from app.core.deps import require_api_token, require_webhook_secret
from app.services.telegram import create_bot_application

logger = logging.getLogger(__name__)

# Docs/schema disabled: this is an internal, token-gated API — don't expose the
# endpoint/field inventory unauthenticated via /docs, /redoc, /openapi.json.
app = FastAPI(
    title=settings.PROJECT_NAME,
    openapi_url=None,
    docs_url=None,
    redoc_url=None,
)

# Restrict Host headers to the configured hosts.
if settings.ALLOWED_HOSTS == ["*"]:
    logger.warning(
        "ALLOWED_HOSTS is ['*'] — TrustedHostMiddleware is a no-op; "
        "set specific hosts in production."
    )
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.ALLOWED_HOSTS)

# Initialize bot application
bot_app = create_bot_application()

@app.post("/webhook")
async def webhook(update: dict, _: None = Depends(require_webhook_secret)):
    """Handle incoming Telegram webhook updates (secret-token verified)."""
    await bot_app.update_queue.put(Update.de_json(data=update, bot=bot_app.bot))
    return {"ok": True}

# Set webhook on startup
@app.on_event("startup")
async def setup_webhook():
    """Set up webhook for Telegram bot on startup (only when secured)."""
    webhook_url = settings.TELEGRAM_WEBHOOK_URL
    if not webhook_url:
        logger.warning("TELEGRAM_WEBHOOK_URL not set, webhook not configured")
        return
    if not settings.TELEGRAM_WEBHOOK_SECRET:
        logger.error(
            "TELEGRAM_WEBHOOK_URL is set but TELEGRAM_WEBHOOK_SECRET is missing; "
            "refusing to register an unsecured webhook."
        )
        return
    await bot_app.bot.set_webhook(
        url=webhook_url, secret_token=settings.TELEGRAM_WEBHOOK_SECRET
    )
    logger.info(f"Webhook set to {webhook_url}")

# Every /api/v1 route requires a valid X-API-Token (fail-closed if unset).
_api_auth = [Depends(require_api_token)]
app.include_router(
    users_router, prefix="/api/v1/users", tags=["users"], dependencies=_api_auth
)
app.include_router(
    meals_router, prefix="/api/v1/meals", tags=["meals"], dependencies=_api_auth
)
app.include_router(
    body_metrics_router,
    prefix="/api/v1/body-metrics",
    tags=["body_metrics"],
    dependencies=_api_auth,
)
app.include_router(
    activities_router,
    prefix="/api/v1/activities",
    tags=["activities"],
    dependencies=_api_auth,
)
app.include_router(
    analysis_reports_router,
    prefix="/api/v1/analysis-reports",
    tags=["analysis_reports"],
    dependencies=_api_auth,
)

@app.get("/")
def read_root():
    return {"Hello": "World"}