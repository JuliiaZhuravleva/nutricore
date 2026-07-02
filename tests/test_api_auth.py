"""Auth-dependency tests for the REST API and the Telegram webhook.

The dependencies are called directly (no TestClient / DB / network) — auth is
rejected before any endpoint or DB access runs.
"""

import pytest
from fastapi import HTTPException

from app.core import deps
from app.core.config import settings

# --- require_api_token -------------------------------------------------------


def test_api_disabled_when_token_unset(monkeypatch):
    monkeypatch.setattr(settings, "API_TOKEN", None)
    with pytest.raises(HTTPException) as exc:
        deps.require_api_token(x_api_token="anything")
    assert exc.value.status_code == 503


def test_api_missing_token_rejected(monkeypatch):
    monkeypatch.setattr(settings, "API_TOKEN", "s3cret")
    with pytest.raises(HTTPException) as exc:
        deps.require_api_token(x_api_token=None)
    assert exc.value.status_code == 401


def test_api_wrong_token_rejected(monkeypatch):
    monkeypatch.setattr(settings, "API_TOKEN", "s3cret")
    with pytest.raises(HTTPException) as exc:
        deps.require_api_token(x_api_token="nope")
    assert exc.value.status_code == 401


def test_api_correct_token_passes(monkeypatch):
    monkeypatch.setattr(settings, "API_TOKEN", "s3cret")
    assert deps.require_api_token(x_api_token="s3cret") is None


# --- require_webhook_secret --------------------------------------------------


def test_webhook_disabled_when_secret_unset(monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_WEBHOOK_SECRET", None)
    with pytest.raises(HTTPException) as exc:
        deps.require_webhook_secret(x_telegram_bot_api_secret_token="anything")
    assert exc.value.status_code == 403


def test_webhook_wrong_secret_rejected(monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_WEBHOOK_SECRET", "hooksecret")
    with pytest.raises(HTTPException) as exc:
        deps.require_webhook_secret(x_telegram_bot_api_secret_token="nope")
    assert exc.value.status_code == 403


def test_webhook_correct_secret_passes(monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_WEBHOOK_SECRET", "hooksecret")
    assert (
        deps.require_webhook_secret(x_telegram_bot_api_secret_token="hooksecret")
        is None
    )
