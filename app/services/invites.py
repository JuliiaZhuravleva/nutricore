"""Who the owner lets in by hand: the grant, its subscription, closing and reopening.

No Telegram here. The subscription is why the bot's screens open for an invited person,
and it runs for the term the owner chose. Closing the door keeps every record of the
person.
"""

import enum
import logging
from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session

from app.crud.crud_access_grant import crud_access_grant
from app.crud.crud_subscription import crud_subscription
from app.services.access_control import in_the_settings

logger = logging.getLogger(__name__)

DEFAULT_MONTHS = 12
TERMS = (3, 6, 12)  # months, as the owner's buttons offer them


@dataclass(frozen=True)
class Person:
    """Who was picked: what Telegram told about them, and nothing else."""

    telegram_id: int
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    username: Optional[str] = None

    @property
    def name(self) -> str:
        """Never empty: an id is ugly, and still tells the owner who it was."""
        full = " ".join(part for part in (self.first_name, self.last_name) if part)
        return (full or self.username or str(self.telegram_id))[:255]


class GrantFailed(Exception):
    """The door stayed as it was."""


class Closed(enum.Enum):
    CLOSED = "closed"
    ALREADY = "already"
    IN_SETTINGS = "in_settings"  # let in by the server's settings: no button closes it
    UNKNOWN = "unknown"


class Reopened(enum.Enum):
    REOPENED = "reopened"
    ALREADY = "already"
    UNKNOWN = "unknown"


def invite(db: Session, person: Person, by: int, months: int = DEFAULT_MONTHS) -> None:
    """Lets the person in, or back in. The subscription first: a grant without one would
    pass the gate and meet a padlock. Raises ``GrantFailed``."""
    made = crud_subscription.create_subscription(
        db, person.telegram_id, by, months=months
    )
    if made is None:
        raise GrantFailed
    crud_access_grant.grant(
        db, person.telegram_id, person.name, person.username, granted_by=by
    )


def by_id(db: Session, telegram_id: int, by: int, months: int) -> None:
    """/grant_sub: a subscription alone would leave the person outside the gate and off
    the owner's list. Someone already known keeps their name. Raises ``GrantFailed``."""
    known = crud_access_grant.get(db, telegram_id)
    person = (
        Person(telegram_id, known.display_name, username=known.username)
        if known is not None
        else Person(telegram_id)
    )
    invite(db, person, by, months)


def set_term(db: Session, telegram_id: int, by: int, months: int) -> bool:
    """The term from today, for someone let in here whose door is open. False for anyone
    else; a failed write raises ``GrantFailed``."""
    if not crud_access_grant.is_active(db, telegram_id):
        return False
    if (
        crud_subscription.create_subscription(db, telegram_id, by, months=months)
        is None
    ):
        raise GrantFailed
    return True


def close(db: Session, telegram_id: int) -> Closed:
    """The grant first: the door shuts even if the subscription cannot be written."""
    if in_the_settings(telegram_id):
        return Closed.IN_SETTINGS
    grant = crud_access_grant.get(db, telegram_id)
    if grant is None:
        return Closed.UNKNOWN
    if grant.revoked_at is not None:
        return Closed.ALREADY
    crud_access_grant.set_revoked(db, telegram_id, True)
    crud_subscription.deactivate_subscription(db, telegram_id)
    return Closed.CLOSED


def reopen(db: Session, telegram_id: int, by: int) -> Reopened:
    grant = crud_access_grant.get(db, telegram_id)
    if grant is None:
        return Reopened.UNKNOWN
    if grant.revoked_at is None:
        return Reopened.ALREADY
    made = crud_subscription.create_subscription(
        db, telegram_id, by, months=DEFAULT_MONTHS
    )
    if made is None:
        raise GrantFailed
    crud_access_grant.set_revoked(db, telegram_id, False)
    return Reopened.REOPENED
