from celery import Celery
from celery.schedules import crontab

from app.core.config import settings

app = Celery(
    "worker",
    broker=f"redis://{settings.REDIS_HOST}:{settings.REDIS_PORT}/0",
    backend=f"redis://{settings.REDIS_HOST}:{settings.REDIS_PORT}/0",
    include=["celery_app.tasks.periodic"],
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
}
