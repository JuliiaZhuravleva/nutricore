"""Subscription gate tests — the owner (admin) must never be gated."""

import asyncio

from app.core.config import settings
from app.services import telegram as tg


def test_admin_bypasses_subscription(monkeypatch):
    """An admin id short-circuits to True without hitting the database."""
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_IDS", "424242")

    # If this touched the DB it would fail (no DB in the unit test); returning
    # True proves the admin_ids short-circuit runs before any DB access.
    assert asyncio.run(tg.check_subscription(424242)) is True


def test_admin_ids_tolerates_malformed_config(monkeypatch):
    """A malformed entry is skipped, not raised — it must not break the hot path.

    admin_ids is evaluated on every check_subscription call, so a config typo must
    never propagate a ValueError and break access for the whole bot.
    """
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_IDS", "42, oops, 99")

    assert settings.admin_ids == [42, 99]
    # And the gate still works for the valid ids without crashing.
    assert asyncio.run(tg.check_subscription(42)) is True
