"""☀️ The morning question's sweep: see ``app.services.morning_push``."""

import asyncio
import logging
from datetime import UTC, datetime

from app.core.config import settings
from app.db.session import SessionLocal
from app.services import custom_emoji, morning_push
from celery_app.celery_app import app

# httpx logs the request URL, which holds the bot token, at INFO.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

NOTHING = {"sent": 0, "skipped": 0, "failed": 0, "blocked": 0}


async def _send(db, now: datetime) -> dict:
    bot = custom_emoji.build_bot(
        settings.TELEGRAM_BOT_TOKEN, custom_emoji.CustomEmoji()
    )
    async with bot:
        await bot.emoji.load(bot, settings.CUSTOM_EMOJI)
        if bot.emoji.problem:
            logger.warning("morning: plain emoji: %s", bot.emoji.problem)
        return await morning_push.run(db, bot, now)


@app.task
def send_morning_questions() -> dict:
    """Every 15 minutes: a question goes out in the first sweep inside 10:00 local."""
    now = datetime.now(UTC)
    with SessionLocal() as db:
        if not morning_push.users_due(db, now):
            return dict(NOTHING)
        return asyncio.run(_send(db, now))
