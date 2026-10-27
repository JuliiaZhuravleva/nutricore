"""What the admins were already told about each model (ADR-0004).

One ``app_settings`` row per model ref, key ``model_watch:<ref>``, value JSON
``{"state", "date", "detail", "notified_at"}``. The weekly check
(``celery_app.tasks.model_watch``) and a live failure (``report_model_failure``)
both write here, so the same news is never sent twice. A row is written only
after the alert was delivered: an alert that did not go out is retried next time.

Best-effort like ``model_selection``: a DB problem is logged, never raised into
the user's meal flow.
"""

import datetime
import json
import logging

from app.crud.crud_app_setting import crud_app_setting
from app.db.session import SessionLocal
from app.services.admin_notify import notify_admins
from app.services.model_registry import parse_model_ref

logger = logging.getLogger(__name__)

STATE_PREFIX = "model_watch:"

TASK_NAMES_RU = {
    "vision": "распознавание фото",
    "text": "разбор текста",
    "barcode": "чтение штрихкода",
    "label_ocr": "чтение этикетки",
    "web_search": "поиск КБЖУ в интернете",
    "embeddings": "поиск по прошлым блюдам",
}

# Refs with an alert in flight: vision and barcode run concurrently on the same
# photo, so both can fail on one dead model within milliseconds.
_reporting: set[str] = set()


def normalize_ref(ref: str) -> str:
    return str(parse_model_ref(ref))


def load_states() -> dict[str, dict]:
    """{ref: record} for every model with a stored alert state."""
    try:
        with SessionLocal() as db:
            rows = crud_app_setting.get_prefix(db, STATE_PREFIX)
    except Exception as exc:
        logger.warning("Could not load model watch state: %s", exc, exc_info=True)
        return {}
    states = {}
    for key, value in rows.items():
        try:
            states[key[len(STATE_PREFIX) :]] = json.loads(value or "{}")
        except ValueError:
            logger.warning("Unreadable model watch state for %s: %r", key, value)
    return states


def load_state(ref: str) -> dict | None:
    return load_states().get(normalize_ref(ref))


def save_state(ref: str, record: dict) -> bool:
    record = {
        **record,
        "notified_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    try:
        with SessionLocal() as db:
            crud_app_setting.set(
                db, STATE_PREFIX + normalize_ref(ref), json.dumps(record)
            )
        return True
    except Exception as exc:
        logger.warning("Could not save model watch state for %s: %s", ref, exc)
        return False


def clear_state(ref: str) -> None:
    try:
        with SessionLocal() as db:
            crud_app_setting.delete(db, STATE_PREFIX + normalize_ref(ref))
    except Exception as exc:
        logger.warning("Could not clear model watch state for %s: %s", ref, exc)


def failure_message(task: str, ref: str, err: Exception) -> str:
    return (
        "⚠️ Модель перестала отвечать\n\n"
        f"Модель: {normalize_ref(ref)}\n"
        f"Где сломалось: {TASK_NAMES_RU.get(task, task)}\n"
        f"Ответ сервиса: {str(err)[:300]}\n\n"
        "Что сделать: открой /models и посмотри, на чём что работает. Замену "
        "можно выбрать прямо в чате: отправь фото еды — бот предложит список "
        "моделей."
    )


async def report_model_failure(task: str, ref: str, err: Exception) -> bool:
    """Tell the admins a model just failed as unavailable — once per breakage.

    Called at the place the failure is detected (``OpenAIService._create`` and the
    web search), before any caller can swallow it. Returns True if an alert went
    out now. Never raises.
    """
    key = normalize_ref(ref)
    if key in _reporting:
        return False
    _reporting.add(key)
    try:
        current = load_state(key)
        if current and current.get("state") == "not_found":
            return False
        if not await notify_admins(failure_message(task, ref, err)):
            return False
        save_state(key, {"state": "not_found", "date": None, "detail": str(err)[:300]})
        return True
    except Exception as exc:  # pragma: no cover - defensive, never break a meal
        logger.warning(
            "report_model_failure failed for %s: %s", ref, exc, exc_info=True
        )
        return False
    finally:
        _reporting.discard(key)
