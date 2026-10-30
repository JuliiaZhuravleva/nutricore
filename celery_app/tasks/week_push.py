"""The week's summary on Sunday evening: see ``app.services.week_push``."""

import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path

from app.core.config import settings
from app.db.session import SessionLocal
from app.services import custom_emoji, week_push
from app.services.card_delivery import CardDelivery
from app.services.picture_renderer import PictureRenderer
from celery_app.celery_app import app

# httpx logs the request URL, which holds the bot token, at INFO.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


async def _send(db, now: datetime) -> dict:
    bot = custom_emoji.build_bot(
        settings.TELEGRAM_BOT_TOKEN, custom_emoji.CustomEmoji()
    )
    delivery = CardDelivery(PictureRenderer(Path(settings.RENDER_DIR)))
    async with bot:
        await bot.emoji.load(bot, settings.CUSTOM_EMOJI)
        if bot.emoji.problem:
            logger.warning("week push: plain emoji: %s", bot.emoji.problem)
        return await week_push.run(db, bot, delivery, now)


@app.task
def send_week_summaries() -> dict:
    """Every 15 minutes: time zones with :30 and :45 reach 22:00 between the hours."""
    now = datetime.now(UTC)
    with SessionLocal() as db:
        if not week_push.users_due(db, now):
            return {"sent": 0, "failed": 0, "blocked": 0}
        return asyncio.run(_send(db, now))
