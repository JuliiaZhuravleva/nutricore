"""🍲 The weekly tidy-up's sweep: see ``app.services.dish_tidy_push``."""

import asyncio
import logging
from datetime import UTC, datetime

from app.core.config import settings
from app.db.session import SessionLocal
from app.services import custom_emoji, dish_tidy_push
from celery_app.celery_app import app

# httpx logs the request URL, which holds the bot token, at INFO.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


async def _send(db, now: datetime) -> dict:
    bot = custom_emoji.build_bot(
        settings.TELEGRAM_BOT_TOKEN, custom_emoji.CustomEmoji()
    )
    async with bot:
        await bot.emoji.load(bot, settings.CUSTOM_EMOJI)
        if bot.emoji.problem:
            logger.warning("dish tidy: plain emoji: %s", bot.emoji.problem)
        return await dish_tidy_push.run(db, bot, now)


@app.task
def tidy_dishes() -> dict:
    """Every 15 minutes: Sunday 12:00–20:59 local, the week's batch goes out."""
    now = datetime.now(UTC)
    with SessionLocal() as db:
        if not dish_tidy_push.has_work(db, now):
            return dict.fromkeys(dish_tidy_push.COUNTS, 0)
        return asyncio.run(_send(db, now))
