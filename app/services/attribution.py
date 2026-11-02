"""Whose model call this is: the person the current work is for.

A ContextVar, like the display-only meter in ``usage_meter``. Set at the very start of an
update (``action_journal.note_update``), and every task and thread started from there
holds a copy: ``block=False`` handlers, ``application.create_task``, ``asyncio.gather``,
``asyncio.to_thread``, ``asyncio.run``. Work that starts with no update, or that one
person starts for another (a sweep, a Celery worker, a maintenance script, /reprocess),
says whose it is with ``attribute_to``.

Nobody named: ``NO_PERSON``, which is a row of its own and not a lost call.
"""

import contextlib
from contextvars import ContextVar
from typing import Optional

from app.models.usage_ledger import NO_PERSON

_person: ContextVar[Optional[int]] = ContextVar("attributed_person", default=None)


def current_person() -> int:
    person = _person.get()
    return NO_PERSON if person is None else person


def begin_update(telegram_id: Optional[int]) -> None:
    """On every update, with or without a person: updates run one after another in one
    task, and the last one's person must not stay on for the next."""
    _person.set(telegram_id)


@contextlib.contextmanager
def attribute_to(telegram_id: Optional[int]):
    token = _person.set(telegram_id)
    try:
        yield
    finally:
        _person.reset(token)
