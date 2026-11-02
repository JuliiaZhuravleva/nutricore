"""/grant_sub on a day the next month does not have.

It raised ValueError on the 31st, and the handler took that for a badly typed command.
Through the real application, at the wire; the day is frozen where the subscription
reads it. The ``reader`` fixture is the fake of the model: a command never reaches it.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import select

import app.crud.crud_subscription as crud_module
from app.models import Subscription, User
from tests.test_capture_router import (  # noqa: F401 (fixtures)
    OWNER,
    Session,
    _message,
    db,
    reader,
    send,
)

GRANTEE = 77
FORMAT = "❌ Формат команды: /grant_sub user_id months"


def _freeze(monkeypatch, moment: datetime) -> None:
    class Frozen(datetime):
        @classmethod
        def utcnow(cls):
            return moment

    monkeypatch.setattr(crud_module, "datetime", Frozen)


def _grant(arguments: str):
    command = f"/grant_sub {arguments}".strip()
    return send(
        _message(
            text=command,
            entities=[{"type": "bot_command", "offset": 0, "length": 10}],
        )
    )


def _subscription(db) -> Subscription | None:
    db.expire_all()
    return db.scalars(
        select(Subscription)
        .join(User, Subscription.user_id == User.id)
        .where(User.telegram_id == GRANTEE)
    ).one_or_none()


@pytest.mark.parametrize(
    ("today", "months", "ends"),
    [
        (datetime(2026, 10, 31, 8, 5, 9), 1, datetime(2026, 11, 30, 8, 5, 9)),
        (datetime(2028, 1, 31, 8, 5, 9), 1, datetime(2028, 2, 29, 8, 5, 9)),
        (datetime(2027, 1, 31, 8, 5, 9), 1, datetime(2027, 2, 28, 8, 5, 9)),
        (datetime(2026, 12, 31, 8, 5, 9), 1, datetime(2027, 1, 31, 8, 5, 9)),
        (datetime(2026, 12, 15, 8, 5, 9), 1, datetime(2027, 1, 15, 8, 5, 9)),
        (datetime(2026, 10, 15, 8, 5, 9), 1, datetime(2026, 11, 15, 8, 5, 9)),
        (datetime(2026, 12, 31, 8, 5, 9), 14, datetime(2028, 2, 29, 8, 5, 9)),
    ],
    ids=[
        "oct31_to_nov30",
        "jan31_leap_feb",
        "jan31_plain_feb",
        "dec31_rolls_over",
        "dec15_keeps_day",
        "oct15_keeps_day",
        "many_months_cross_years",
    ],
)
def test_the_grant_ends_in_the_target_month(
    db, reader, monkeypatch, today, months, ends
):
    _freeze(monkeypatch, today)

    wire = _grant(f"{GRANTEE} {months}")

    (reply,) = wire.seen()
    assert reply.startswith(f"✅ Доступ открыт: {GRANTEE}, срок {months} месяц")
    assert "/users" in reply
    granted = _subscription(db)
    assert granted.is_active and granted.start_date == today
    assert granted.end_date == ends
    assert reader.text == [] and reader.image == []


def test_a_grant_on_the_31st_replaces_an_earlier_one(db, reader, monkeypatch):
    _freeze(monkeypatch, datetime(2026, 10, 15, 8, 5, 9))
    _grant(f"{GRANTEE} 1")
    assert _subscription(db).end_date == datetime(2026, 11, 15, 8, 5, 9)  # positive

    _freeze(monkeypatch, datetime(2026, 10, 31, 8, 5, 9))
    wire = _grant(f"{GRANTEE} 1")

    assert FORMAT not in wire.seen()
    assert _subscription(db).end_date == datetime(2026, 11, 30, 8, 5, 9)


@pytest.mark.parametrize(
    "arguments",
    [f"{GRANTEE} abc", "abc 1", f"{GRANTEE}", "", f"{GRANTEE} 1 2"],
    ids=["months_word", "id_word", "no_months", "nothing", "too_many"],
)
def test_a_bad_argument_still_gets_the_format_message(
    db, reader, monkeypatch, arguments
):
    _freeze(monkeypatch, datetime(2026, 10, 31, 8, 5, 9))

    wire = _grant(arguments)

    assert wire.seen() == [FORMAT]
    assert _subscription(db) is None


def test_a_grant_by_id_keeps_the_name_of_someone_known(db, reader):
    from app.crud.crud_access_grant import crud_access_grant

    crud_access_grant.grant(db, GRANTEE, "Саша", "sasha_k", granted_by=1)
    _grant(f"{GRANTEE} 3")
    db.expire_all()
    grant = crud_access_grant.get(db, GRANTEE)
    assert (grant.display_name, grant.username) == ("Саша", "sasha_k")
