import datetime

from sqlalchemy import BigInteger, Column, DateTime, Integer, String, func

from app.db.base import Base
from app.db.base_class import BaseClass

UTC = datetime.timezone.utc


class AccessGrant(Base, BaseClass):
    """A person the owner let in by hand, next to the list in the settings.

    Keyed by telegram_id with no foreign key: the gate reads it before any user row
    exists. ``revoked_at`` closes the door and keeps the row, so «↩️ Вернуть» and a
    second invitation find it. ``first_opened_at`` is set once, by their first /start;
    ``closed_told_at`` once per closing, when they hear the door is closed.
    """

    __tablename__ = "access_grants"

    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(BigInteger, unique=True, index=True, nullable=False)
    display_name = Column(String(255), nullable=False)
    username = Column(String(64), nullable=True)
    granted_by = Column(BigInteger, nullable=False)
    granted_at = Column(DateTime(timezone=True), nullable=False)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    first_opened_at = Column(DateTime(timezone=True), nullable=True)
    closed_told_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        default=lambda: datetime.datetime.now(UTC),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        default=lambda: datetime.datetime.now(UTC),
        onupdate=lambda: datetime.datetime.now(UTC),
    )
