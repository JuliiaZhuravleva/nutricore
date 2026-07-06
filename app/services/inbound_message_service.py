"""Best-effort persistence of inbound meal messages to inbound_messages (TD-009).

Kept out of the telegram handler (which already owns too much — see the tech
debt): recording a message and flipping its lifecycle status is a generic
capture concern. ``record_inbound``/``mark_analyzed``/``mark_failed`` never raise
— losing a debug/history row must not break the user's meal flow — so the caller
can fire-and-forget. ``get_reprocessable`` is the one that surfaces errors: it
backs the owner-triggered ``/reprocess`` command, which reports failures itself.
"""

import logging
from datetime import datetime
from typing import Any, List, Optional

from app.crud.crud_inbound_message import crud_inbound_message
from app.db.session import SessionLocal
from app.schemas.inbound_message import InboundMessage as InboundMessageRead
from app.schemas.inbound_message import InboundMessageCreate

logger = logging.getLogger(__name__)


def record_inbound(
    *,
    telegram_id: int,
    kind: str,
    content: Optional[str],
    photo_file_id: Optional[str],
) -> Optional[int]:
    """Persist a ``pending`` inbound message; return its id (None on failure)."""
    try:
        with SessionLocal() as db:
            obj = crud_inbound_message.create(
                db,
                InboundMessageCreate(
                    telegram_id=telegram_id,
                    kind=kind,
                    content=content,
                    photo_file_id=photo_file_id,
                ),
            )
            return obj.id
    except Exception as exc:  # pragma: no cover - best effort
        # exc_info so a real bug (bad field, encoding) leaves a traceback and
        # isn't indistinguishable from a transient DB blip in the logs.
        logger.warning("Could not record inbound message: %s", exc, exc_info=True)
        return None


def mark_analyzed(msg_id: Optional[int], ai_analysis: Any) -> bool:
    """Best-effort flip to analyzed. Returns True iff the status persisted; a
    None id (never recorded) or any DB failure returns False."""
    if msg_id is None:
        return False
    try:
        with SessionLocal() as db:
            return crud_inbound_message.mark_analyzed(db, msg_id, ai_analysis)
    except Exception as exc:  # pragma: no cover - best effort
        logger.warning(
            "Could not mark inbound message %s analyzed: %s", msg_id, exc, exc_info=True
        )
        return False


def mark_failed(msg_id: Optional[int], error: str) -> bool:
    """Best-effort flip to failed. Returns True iff the status persisted; a None
    id (never recorded) or any DB failure returns False."""
    if msg_id is None:
        return False
    try:
        with SessionLocal() as db:
            return crud_inbound_message.mark_failed(db, msg_id, error)
    except Exception as exc:  # pragma: no cover - best effort
        logger.warning(
            "Could not mark inbound message %s failed: %s", msg_id, exc, exc_info=True
        )
        return False


def get_reprocessable(limit: int = 50) -> List[InboundMessageRead]:
    """Detached read models for the replay queue (pending/failed, oldest first).

    Validated inside the session so callers can use them after it closes. Unlike
    the record/mark helpers this may raise (DB down) — /reprocess reports that.
    """
    with SessionLocal() as db:
        rows = crud_inbound_message.get_reprocessable(db, limit)
        return [InboundMessageRead.model_validate(r) for r in rows]


def delete_older_than(cutoff: datetime) -> int:
    """Prune messages older than `cutoff`; returns the count removed."""
    with SessionLocal() as db:
        return crud_inbound_message.delete_older_than(db, cutoff)
