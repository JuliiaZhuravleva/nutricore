import datetime
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.models.access_grant import AccessGrant

UTC = datetime.timezone.utc
_INSERT = {"postgresql": postgresql_insert, "sqlite": sqlite_insert}


class CRUDAccessGrant:
    def get(self, db: Session, telegram_id: int) -> Optional[AccessGrant]:
        return db.scalar(
            select(AccessGrant).where(AccessGrant.telegram_id == telegram_id)
        )

    def is_active(self, db: Session, telegram_id: int) -> bool:
        found = db.scalar(
            select(AccessGrant.id).where(
                AccessGrant.telegram_id == telegram_id,
                AccessGrant.revoked_at.is_(None),
            )
        )
        return found is not None

    def everyone(self, db: Session) -> list:
        """Oldest first: a row keeps its place on the screen when its state changes."""
        return list(db.scalars(select(AccessGrant).order_by(AccessGrant.id)))

    def grant(
        self,
        db: Session,
        telegram_id: int,
        display_name: str,
        username: Optional[str],
        granted_by: int,
    ) -> AccessGrant:
        """Opens the door, or opens it again: the same row, so ``first_opened_at`` stays
        and two taps at once cannot make two."""
        now = datetime.datetime.now(UTC)
        fresh = {
            "display_name": display_name,
            "username": username,
            "granted_by": granted_by,
            "granted_at": now,
            "revoked_at": None,
            "closed_told_at": None,
            "updated_at": now,
        }
        insert = _INSERT[db.get_bind().dialect.name]
        db.execute(
            insert(AccessGrant)
            .values(telegram_id=telegram_id, **fresh)
            .on_conflict_do_update(index_elements=["telegram_id"], set_=fresh)
        )
        db.commit()
        db.expire_all()
        return self.get(db, telegram_id)

    def set_revoked(self, db: Session, telegram_id: int, revoked: bool) -> None:
        now = datetime.datetime.now(UTC)
        db.execute(
            update(AccessGrant)
            .where(AccessGrant.telegram_id == telegram_id)
            .values(
                revoked_at=now if revoked else None,
                closed_told_at=None,
                updated_at=now,
            )
        )
        db.commit()
        db.expire_all()

    def take_closed_notice(self, db: Session, telegram_id: int) -> bool:
        """Whether this call is the one that may tell them the door is closed. One
        UPDATE, so of two messages at once only one hears it."""
        now = datetime.datetime.now(UTC)
        result = db.execute(
            update(AccessGrant)
            .where(
                AccessGrant.telegram_id == telegram_id,
                AccessGrant.revoked_at.is_not(None),
                AccessGrant.closed_told_at.is_(None),
            )
            .values(closed_told_at=now, updated_at=now)
        )
        db.commit()
        return result.rowcount == 1

    def mark_opened(self, db: Session, telegram_id: int) -> Optional[AccessGrant]:
        """The grant, if this call is the one that set ``first_opened_at``. One UPDATE,
        so of two /start at once only one tells the owner."""
        now = datetime.datetime.now(UTC)
        result = db.execute(
            update(AccessGrant)
            .where(
                AccessGrant.telegram_id == telegram_id,
                AccessGrant.first_opened_at.is_(None),
                AccessGrant.revoked_at.is_(None),
            )
            .values(first_opened_at=now, updated_at=now)
        )
        db.commit()
        if result.rowcount != 1:
            return None
        db.expire_all()
        return self.get(db, telegram_id)


crud_access_grant = CRUDAccessGrant()
