"""The shared shape of a push sweep: who is due, read so that one person's failure
cannot end the sweep for everyone after them.

A failed check ends in ``db.rollback()``, which expires every row of the session.
Reading an attribute of an expired row reloads it, and a reload that fails outside
the per-person ``try`` would end the loop. So the loop walks plain ids and fetches
each row inside its own ``try`` (as ``dish_tidy_push.run`` does), and the callers'
``run`` snapshots plain ids again before sending.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from app.models.user import User

logger = logging.getLogger(__name__)


def users_due(
    db, check: Callable[[User], Any], log: logging.Logger, label: str
) -> list:
    """(user, what ``check`` found) for everyone whose check finds something. A person
    whose check raises is logged and skipped."""
    people = [
        (user_id, telegram_id)
        for user_id, telegram_id in db.query(User.id, User.telegram_id).order_by(
            User.id
        )
    ]
    found = []
    for user_id, telegram_id in people:
        try:
            user = db.get(User, user_id)
            if user is None:
                continue
            value = check(user)
        except Exception:
            log.exception("%s: could not check %s", label, telegram_id)
            db.rollback()
            continue
        if value is not None:
            found.append((user_id, value))
    # Fetched again after the last rollback: no row handed out is an expired one. A
    # reload that fails costs that person this sweep, not everyone after them.
    rows = []
    for user_id, value in found:
        try:
            user = db.get(User, user_id)
        except Exception:
            log.exception("%s: could not reload user %s", label, user_id)
            db.rollback()
            continue
        if user is not None:
            rows.append((user, value))
    return rows


def snapshot(db, rows: list) -> list:
    """(id, telegram_id, value) of ``users_due`` rows, read at once. A row that will
    not reload is left out (its person waits for the next sweep), never raised."""
    plain = []
    for user, value in rows:
        try:
            plain.append((user.id, user.telegram_id, value))
        except Exception:
            logger.exception("sweep: could not read a due person")
            db.rollback()
    return plain
