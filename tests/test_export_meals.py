"""Read-only meals export endpoint for the my-health vault.

Double-gated on current main: the router-level X-API-Token (fail-closed) AND the
endpoint's own X-Export-Token. API-level tests via TestClient with get_db pointed
at the in-memory session. base_url is localhost so TrustedHostMiddleware (which
rejects the default "testserver" host) lets the request through. stdlib datetime
only (no pytz), independent of the other test modules.
"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.db.session import get_db
from app.main import app
from app.models.meal import Meal

UTC = timezone.utc

_API_TOKEN = "test-api-token"
_EXPORT_TOKEN = "test-export-token"
_HEADERS = {"X-API-Token": _API_TOKEN, "X-Export-Token": _EXPORT_TOKEN}


@pytest.fixture
def client(db_session, monkeypatch):
    monkeypatch.setattr(settings, "API_TOKEN", _API_TOKEN)
    monkeypatch.setattr(settings, "EXPORT_API_TOKEN", _EXPORT_TOKEN)
    # Route every get_db dependency at the in-memory test session.
    app.dependency_overrides[get_db] = lambda: db_session
    # localhost host header passes TrustedHostMiddleware; not a context manager →
    # Telegram/webhook startup events don't fire.
    yield TestClient(app, base_url="http://localhost")
    app.dependency_overrides.clear()


@pytest.fixture
def seeded(db_session):
    # Insert the ORM model directly (SQLite doesn't enforce the users FK, so no
    # user row is needed).
    times = [
        datetime(2026, 6, 1, 9, 0, tzinfo=UTC),
        datetime(2026, 6, 1, 14, 0, tzinfo=UTC),
        datetime(2026, 6, 2, 9, 0, tzinfo=UTC),
    ]
    for t in times:
        db_session.add(
            Meal(
                user_id=1,
                meal_time=t,
                calories=300,
                proteins=10,
                fats=5,
                carbohydrates=50,
            )
        )
    db_session.commit()
    return times


def test_export_requires_api_token(client, seeded):
    # The router-level X-API-Token gate fires before the export token.
    assert (
        client.get(
            "/api/v1/meals/export", headers={"X-Export-Token": _EXPORT_TOKEN}
        ).status_code
        == 401
    )


def test_export_requires_export_token(client, seeded):
    # Valid API token, but missing/wrong export token → 403.
    assert (
        client.get(
            "/api/v1/meals/export", headers={"X-API-Token": _API_TOKEN}
        ).status_code
        == 403
    )
    assert (
        client.get(
            "/api/v1/meals/export",
            headers={"X-API-Token": _API_TOKEN, "X-Export-Token": "wrong"},
        ).status_code
        == 403
    )


def test_export_returns_all_meals_in_time_order(client, seeded):
    resp = client.get("/api/v1/meals/export", headers=_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"meals"}
    meals = body["meals"]
    assert len(meals) == 3
    # contract fields the my-health parser reads
    m = meals[0]
    for field in ("id", "meal_time", "calories", "proteins", "fats", "carbohydrates"):
        assert field in m
    stamps = [x["meal_time"] for x in meals]
    assert stamps == sorted(stamps)  # ordered by meal_time


def test_export_since_filters(client, seeded):
    since = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    resp = client.get(
        "/api/v1/meals/export",
        params={"since": since.isoformat()},
        headers=_HEADERS,
    )
    assert resp.status_code == 200
    meals = resp.json()["meals"]
    assert len(meals) == 2  # drops the 09:00 June-1 meal before `since`
    # SQLite doesn't round-trip tzinfo, so compare naive-to-naive.
    returned = [
        datetime.fromisoformat(m["meal_time"]).replace(tzinfo=None) for m in meals
    ]
    assert min(returned) >= since.replace(tzinfo=None) - timedelta(seconds=1)


def test_export_disabled_when_token_unset(client, seeded, monkeypatch):
    # Opt-in: unset EXPORT_API_TOKEN → always 403 even with a matching header.
    monkeypatch.setattr(settings, "EXPORT_API_TOKEN", None)
    assert client.get("/api/v1/meals/export", headers=_HEADERS).status_code == 403
