"""✨ Что нового after a big release: see ``app.services.bot_news``."""

import asyncio
import logging
from datetime import UTC, datetime

from telegram import Bot

from app.core.config import settings
from app.db.session import SessionLocal
from app.services import bot_news
from celery_app.celery_app import app

# httpx logs the request URL, which holds the bot token, at INFO.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


async def _send(db, now: datetime) -> dict:
    async with Bot(settings.TELEGRAM_BOT_TOKEN) as bot:
        return await bot_news.run(db, bot, now)


@app.task
def send_news() -> dict:
    """Every 15 minutes: each person gets the news inside their own daytime hours."""
    now = datetime.now(UTC)
    with SessionLocal() as db:
        if not bot_news.users_due(db, now):
            return {"sent": 0, "failed": 0, "blocked": 0}
        return asyncio.run(_send(db, now))
