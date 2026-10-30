from celery import Celery
from celery.schedules import crontab

from app.core.config import settings

app = Celery(
    "worker",
    broker=f"redis://{settings.REDIS_HOST}:{settings.REDIS_PORT}/0",
    backend=f"redis://{settings.REDIS_HOST}:{settings.REDIS_PORT}/0",
    include=[
        "celery_app.tasks.periodic",
        "celery_app.tasks.personal_food",
        "celery_app.tasks.model_watch",
        "celery_app.tasks.week_push",
    ],
)

app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
)

# Daily cleanup of the debug/history tables (retention via config).
app.conf.beat_schedule = {
    "purge-ai-call-logs": {
        "task": "celery_app.tasks.periodic.purge_ai_call_logs",
        "schedule": crontab(hour=3, minute=0),
    },
    "purge-inbound-messages": {
        "task": "celery_app.tasks.periodic.purge_inbound_messages",
        "schedule": crontab(hour=3, minute=30),
    },
    # Sunday 22:00 on each user's clock; the task itself checks who is due.
    "week-summaries": {
        "task": "celery_app.tasks.week_push.send_week_summaries",
        "schedule": crontab(minute="*/15"),
        "options": {"expires": 840},  # after a restart, no pile of old runs
    },
    # Weekly, Monday 06:00 UTC (10:00 Tbilisi): model shutdown dates (ADR-0004).
    "model-watch": {
        "task": "celery_app.tasks.model_watch.check_models",
        "schedule": crontab(day_of_week="mon", hour=6, minute=0),
    },
}
