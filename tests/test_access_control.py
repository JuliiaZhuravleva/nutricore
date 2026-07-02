"""Access-control (mode + whitelist) tests."""

import asyncio
from types import SimpleNamespace

import pytest
from telegram.ext import ApplicationHandlerStop

from app.core.config import settings
from app.services import telegram as tg


@pytest.fixture
def access(monkeypatch):
    """Configure admins=[1], whitelist=[2]; caller sets the mode."""
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_IDS", "1")
    monkeypatch.setattr(settings, "ALLOWED_TELEGRAM_IDS", "2")

    def set_mode(mode):
        monkeypatch.setattr(settings, "BOT_ACCESS_MODE", mode)

    return set_mode


def test_open_mode_allows_everyone(access):
    access("open")
    assert tg.is_user_allowed(1) is True  # admin
    assert tg.is_user_allowed(2) is True  # whitelisted
    assert tg.is_user_allowed(999) is True  # stranger
    assert tg.is_user_allowed(None) is True  # no-user update


def test_whitelist_mode(access):
    access("whitelist")
    assert tg.is_user_allowed(1) is True  # admin always allowed
    assert tg.is_user_allowed(2) is True  # whitelisted
    assert tg.is_user_allowed(999) is False  # stranger dropped
    assert tg.is_user_allowed(None) is False  # no-user update dropped


def test_closed_mode_admins_only(access):
    access("closed")
    assert tg.is_user_allowed(1) is True  # admin
    assert tg.is_user_allowed(2) is False  # even whitelisted is blocked
    assert tg.is_user_allowed(999) is False


def test_unknown_mode_falls_back_to_whitelist(access):
    access("banana")
    assert tg.is_user_allowed(2) is True  # whitelisted passes
    assert tg.is_user_allowed(999) is False  # stranger dropped


def test_allowed_ids_tolerates_malformed(monkeypatch):
    monkeypatch.setattr(settings, "ALLOWED_TELEGRAM_IDS", "2, nope, 5")
    assert settings.allowed_ids == [2, 5]


def _make_update(user_id):
    return SimpleNamespace(effective_user=SimpleNamespace(id=user_id))


def test_gate_drops_non_allowed(access):
    access("whitelist")
    with pytest.raises(ApplicationHandlerStop):
        asyncio.run(tg.access_gate(_make_update(999), None))


def test_gate_passes_allowed(access):
    access("whitelist")
    # Returns normally (no exception) for an allowed user.
    assert asyncio.run(tg.access_gate(_make_update(2), None)) is None
