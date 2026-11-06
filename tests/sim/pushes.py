"""What ``tick`` runs: every periodic push of the bot, in the order beat would.

A push is ``async (db, bot, now) -> dict`` and decides by itself who is due at ``now``
(the world's clock), exactly as its Celery task does. A new push is one line here.

Left out on purpose: beat's ``model-watch`` (celery_app.py), which checks the model
companies' APIs over the network and messages the admins; the world has neither.
"""

from __future__ import annotations


async def _week_card(db, bot, now) -> dict:
    import app.services.telegram as tg
    from app.services import week_push

    return await week_push.run(db, bot, tg._card_delivery, now)


async def _reminders(db, bot, now) -> dict:
    from app.services import reminder_push

    return await reminder_push.run(db, bot, now)


PUSHES = {
    "week card": _week_card,
    "reminders": _reminders,
}

BEAT_MINUTES = 15  # how often beat runs them (celery_app.py: crontab(minute="*/15"))
