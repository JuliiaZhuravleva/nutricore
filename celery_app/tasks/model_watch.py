"""Weekly model lifecycle check (ADR-0004): see ``app.services.model_watch``."""

import asyncio
import logging

from app.core.config import settings
from app.services.model_watch import run_model_watch
from app.services.openai_service import OpenAIService
from celery_app.celery_app import app

logger = logging.getLogger(__name__)


@app.task
def check_models() -> dict:
    """Ask each company about the models in use; message the admins on news."""
    if not settings.MODEL_WATCH_ENABLED:
        logger.info("check_models: MODEL_WATCH_ENABLED is off; skipped")
        return {"skipped": True}
    # A fresh service per run: its clients bind to this run's event loop, and it
    # reads the admin's current picks from the DB.
    report = asyncio.run(run_model_watch(OpenAIService()))
    return {
        "states": {ref: f.state for ref, f in report.findings.items()},
        "news": report.news,
        "delivered": report.delivered,
    }
