"""Best-effort debug logging of OpenAI analysis calls to ai_call_logs.

Kept out of the telegram handler layer: it's a generic instrumentation concern
(time a coroutine, record the call), not bot-specific. The caller passes the
model name and a parse callable so this stays decoupled from nutrition specifics.
"""

import logging
import time
from typing import Any, Awaitable, Callable, Optional

from app.crud.crud_ai_call_log import crud_ai_call_log
from app.db.session import SessionLocal
from app.schemas.ai_call_log import AiCallLogCreate

logger = logging.getLogger(__name__)


def record_ai_call(**fields: Any) -> None:
    """Write one ai_call_logs row. Never raises — a logging failure must not
    break the caller's flow."""
    try:
        with SessionLocal() as db:
            crud_ai_call_log.create(db, AiCallLogCreate(**fields))
    except Exception as exc:  # pragma: no cover - best effort
        logger.warning("Failed to record ai_call_log: %s", exc)


async def analyze_and_log(
    coro: Awaitable[Any],
    *,
    kind: str,
    input_ref: Optional[str],
    telegram_id: Optional[int],
    model: Optional[str],
    parse: Callable[[Any], dict],
) -> dict:
    """Await an analysis coroutine, persist a debug row, return the parsed dict.

    Records ``status="ok"`` with the raw + parsed result on success, or
    ``status="error"`` (and re-raises) on any analysis / parse failure.
    """
    started = time.perf_counter()
    raw = None
    try:
        raw = await coro
        parsed = parse(raw)
    except Exception as exc:
        record_ai_call(
            telegram_id=telegram_id,
            kind=kind,
            input_ref=input_ref,
            model=model,
            raw_response=raw if isinstance(raw, str) else None,
            parsed_result=None,
            status="error",
            error=str(exc),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
        raise
    record_ai_call(
        telegram_id=telegram_id,
        kind=kind,
        input_ref=input_ref,
        model=model,
        raw_response=raw if isinstance(raw, str) else None,
        parsed_result=parsed,
        status="ok",
        error=None,
        latency_ms=int((time.perf_counter() - started) * 1000),
    )
    return parsed
