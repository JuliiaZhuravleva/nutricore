"""Persisted OpenAI model override (part of the TD-005 self-heal flow).

Owns the ``app_settings``-backed runtime model override that lets the owner
switch the live OpenAI model in-chat (when one is deprecated) and have the choice
survive a restart. Extracted from ``telegram.py`` (TD-008) so the conversation
handler stays thin, and consumed by ``OpenAIService`` construction (TD-007) so
*every* service instance — not just the bot singleton — honours the override.

Mirrors the service-module shape of ``access_control.py`` / ``ai_call_log_service.py``:
a small, dependency-light module the rest of the code calls, with no Telegram or
handler coupling. All reads/writes are best-effort — a DB hiccup (or the
``app_settings`` table not existing yet, pre-migration) degrades to the configured
``settings.OPENAI_MODEL`` rather than breaking startup or a user action.
"""

import logging

from app.crud.crud_app_setting import crud_app_setting
from app.db.session import SessionLocal

logger = logging.getLogger(__name__)

# Persisted-setting key for the runtime model override (see TD-005 self-heal).
OPENAI_MODEL_SETTING_KEY = "openai_model"


def get_persisted_model() -> str | None:
    """Best-effort read of the persisted model override.

    Returns ``None`` when unset or unavailable (e.g. the ``app_settings`` table
    doesn't exist yet before the first migration, or the DB is unreachable) so
    callers fall back to ``settings.OPENAI_MODEL``.
    """
    try:
        with SessionLocal() as db:
            return crud_app_setting.get(db, OPENAI_MODEL_SETTING_KEY)
    except Exception as e:  # pragma: no cover - defensive best-effort
        # exc_info so a code bug (e.g. a crud signature change) is distinguishable
        # from a genuine DB-unavailable in the logs.
        logger.warning(
            "Could not load persisted OpenAI model override: %s", e, exc_info=True
        )
        return None


def persist_model(model: str) -> None:
    """Best-effort save of the chosen model.

    A failure just means it won't survive a restart — the live service is already
    switched by the caller, so the self-heal flow still completes.
    """
    try:
        with SessionLocal() as db:
            crud_app_setting.set(db, OPENAI_MODEL_SETTING_KEY, model)
    except Exception as e:
        logger.warning("Could not persist model choice %s: %s", model, e, exc_info=True)


def apply_persisted_model(service) -> None:
    """Apply the persisted override to a live ``OpenAIService`` instance.

    Called at startup (after migrations are guaranteed to have run) to re-assert
    the override on the shared bot singleton. Individual instances also load it in
    their constructor, so this is belt-and-suspenders for the singleton that was
    built at import time — possibly before the DB was ready.
    """
    model = get_persisted_model()
    if model:
        service.set_model(model)
        logger.info("Applied persisted OpenAI model override: %s", model)
