import datetime

from sqlalchemy import Column, DateTime, String, Text, func

from app.db.base import Base
from app.db.base_class import BaseClass

UTC = datetime.timezone.utc


class AppSetting(Base, BaseClass):
    """Global, runtime-mutable key/value settings (one row per key).

    First use (TD-005): persist the effective OpenAI model when the owner picks a
    replacement after a deprecation, so the choice survives a restart without an
    env edit. Kept generic so future runtime toggles can reuse it.
    """

    __tablename__ = "app_settings"

    key = Column(String, primary_key=True)
    value = Column(Text, nullable=True)
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.datetime.now(UTC),
        onupdate=lambda: datetime.datetime.now(UTC),
        server_default=func.now(),
        nullable=False,
    )
