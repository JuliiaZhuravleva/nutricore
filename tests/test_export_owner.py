"""The export holds the owner's food only (a two-person audit found it held everyone's).

The consumer drops ``user_id``, so another person's meal in the feed would count as the
owner's. Every test goes through the real route on real rows, and shows the owner's
meals coming first: a filter that returned nothing would otherwise pass. No model is
reached: the rows are written straight through ``save_meal``.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update

from app.core.config import settings
from app.db.session import get_db
from app.main import app
from app.models import Meal, User
from app.schemas.meal import MealCreate
from app.services import meal_items

_API_TOKEN, _EXPORT_TOKEN = "test-api-token", "test-export-token"
_HEADERS = {"X-API-Token": _API_TOKEN, "X-Export-Token": _EXPORT_TOKEN}
OWNER, SECOND_OWNER, GUEST = 42, 43, 77
EATEN = datetime(2026, 10, 26, 9, 0, tzinfo=UTC)
LONG_AGO = datetime(2026, 1, 1, tzinfo=UTC)
CURSOR = datetime(2026, 6, 1, tzinfo=UTC)


@pytest.fixture
def client(db_session, monkeypatch):
    monkeypatch.setattr(settings, "API_TOKEN", _API_TOKEN)
    monkeypatch.setattr(settings, "EXPORT_API_TOKEN", _EXPORT_TOKEN)
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_IDS", str(OWNER))
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app, base_url="http://localhost")
    app.dependency_overrides.clear()


def _person(db, telegram_id: int) -> User:
    user = User(telegram_id=telegram_id, username=f"u{telegram_id}", settings={})
    db.add(user)
    db.commit()
    return user


@pytest.fixture
def owner(db_session):
    return _person(db_session, OWNER)


@pytest.fixture
def guest(db_session):
    return _person(db_session, GUEST)


def _meal(db, user, name="суп") -> Meal:
    return meal_items.save_meal(
        db,
        MealCreate(
            description=name,
            meal_time=EATEN,
            calories=300.0,
            nutrients={"foods": [name], "calories": 300.0},
        ),
        user.id,
    )


def _rewind(db, *meals) -> None:
    """Their last change is long before the cursor."""
    ids = [meal.id for meal in meals]
    db.execute(update(Meal).where(Meal.id.in_(ids)).values(updated_at=LONG_AGO))
    db.commit()
    db.expire_all()


def _pull(client, **params) -> set[int]:
    params = {key: value.isoformat() for key, value in params.items()}
    answer = client.get("/api/v1/meals/export", params=params, headers=_HEADERS)
    assert answer.status_code == 200, answer.text
    return {row["id"] for row in answer.json()["meals"]}


def test_a_plain_pull_holds_the_owners_meals_and_not_a_second_persons(
    client, db_session, owner, guest
):
    mine, theirs = _meal(db_session, owner), _meal(db_session, guest, "хлеб")
    assert _pull(client) == {mine.id}
    assert theirs.id not in _pull(client)


def test_the_since_pull_is_scoped_too(client, db_session, owner, guest):
    mine, theirs = _meal(db_session, owner), _meal(db_session, guest, "хлеб")
    assert _pull(client, since=LONG_AGO) == {mine.id}
    assert theirs.id not in _pull(client, since=LONG_AGO)


def test_the_updated_since_pull_is_scoped_too(client, db_session, owner, guest):
    mine, theirs = _meal(db_session, owner), _meal(db_session, guest, "хлеб")
    assert _pull(client, updated_since=CURSOR) == {mine.id}
    assert theirs.id not in _pull(client, updated_since=CURSOR)


def test_a_second_persons_deletion_does_not_reach_the_consumer(
    client, db_session, owner, guest
):
    mine, theirs = _meal(db_session, owner), _meal(db_session, guest, "хлеб")
    _rewind(db_session, mine, theirs)
    assert _pull(client, updated_since=CURSOR) == set()  # positive: the cursor filters

    assert meal_items.delete_meal(db_session, owner.id, mine.id).status == "deleted"
    assert meal_items.delete_meal(db_session, guest.id, theirs.id).status == "deleted"

    assert _pull(client, updated_since=CURSOR) == {mine.id}


def test_every_configured_owner_counts(client, db_session, owner, guest, monkeypatch):
    other_owner = _person(db_session, SECOND_OWNER)
    mine = _meal(db_session, owner)
    hers = _meal(db_session, other_owner, "каша")
    theirs = _meal(db_session, guest, "хлеб")
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_IDS", f"{OWNER},{SECOND_OWNER}")

    assert _pull(client) == {mine.id, hers.id}
    assert theirs.id not in _pull(client)


@pytest.mark.parametrize(
    "pull",
    [{}, {"since": LONG_AGO}, {"updated_since": CURSOR}],
    ids=["plain", "since", "updated_since"],
)
@pytest.mark.parametrize("configured", ["", "not-a-number"], ids=["unset", "malformed"])
def test_no_configured_owner_returns_nothing_and_says_so(
    client, db_session, owner, guest, monkeypatch, caplog, pull, configured
):
    mine = _meal(db_session, owner)
    _meal(db_session, guest, "хлеб")
    with caplog.at_level(logging.WARNING, logger="app.api.v1.meals"):
        assert _pull(client, **pull) == {mine.id}  # known positive: the owner is set
    assert "no owner configured" not in caplog.text
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_IDS", configured)

    with caplog.at_level(logging.WARNING, logger="app.api.v1.meals"):
        assert _pull(client, **pull) == set()

    assert "no owner configured" in caplog.text


def test_an_owner_who_never_wrote_to_the_bot_gets_nothing_of_anyone_elses(
    client, db_session, guest, monkeypatch
):
    theirs = _meal(db_session, guest, "хлеб")
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_IDS", str(GUEST))
    assert _pull(client) == {theirs.id}  # known positive: that row can be exported

    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_IDS", str(OWNER))

    assert _pull(client) == set()


def test_the_tokens_still_guard_the_scoped_route(client, db_session, owner):
    _meal(db_session, owner)
    assert client.get("/api/v1/meals/export", headers=_HEADERS).status_code == 200
    answer = client.get(
        "/api/v1/meals/export",
        headers={"X-API-Token": _API_TOKEN, "X-Export-Token": "wrong"},
    )
    assert answer.status_code == 403
