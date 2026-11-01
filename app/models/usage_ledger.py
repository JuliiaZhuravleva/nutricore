import datetime

from sqlalchemy import (
    BigInteger,
    Column,
    Date,
    DateTime,
    Integer,
    Numeric,
    UniqueConstraint,
    func,
)

from app.db.base import Base
from app.db.base_class import BaseClass

UTC = datetime.timezone.utc

# A model call nobody can be named for. A number, not NULL: NULLs are distinct in a
# Postgres unique constraint, and the one row a day would become a row a call.
NO_PERSON = 0


class UsageLedger(Base, BaseClass):
    """What the models cost, per person per day (``day`` in DEFAULT_TIMEZONE).

    Numbers only, no text, so it is kept for good. Keyed by telegram_id with no foreign
    key: a row outlives anything that happens to the person's other rows. ``calls``
    includes ``unpriced_calls``, the calls with no known price, which add nothing to
    ``cost_usd``: a price is never guessed.
    """

    __tablename__ = "usage_ledger"
    __table_args__ = (
        UniqueConstraint("telegram_id", "day", name="uq_usage_ledger_person_day"),
    )

    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(BigInteger, nullable=False)
    day = Column(Date, nullable=False, index=True)
    calls = Column(Integer, nullable=False, default=0, server_default="0")
    # Scale 10: one short embedding costs a few millionths of a cent.
    cost_usd = Column(Numeric(16, 10), nullable=False, default=0, server_default="0")
    unpriced_calls = Column(Integer, nullable=False, default=0, server_default="0")
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
