import datetime
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.models.usage_ledger import UsageLedger

UTC = datetime.timezone.utc
_INSERT = {"postgresql": postgresql_insert, "sqlite": sqlite_insert}


@dataclass(frozen=True)
class Totals:
    calls: int = 0
    cost_usd: Decimal = Decimal(0)
    unpriced_calls: int = 0


_SUMS = (
    func.coalesce(func.sum(UsageLedger.calls), 0),
    func.coalesce(func.sum(UsageLedger.cost_usd), 0),
    func.coalesce(func.sum(UsageLedger.unpriced_calls), 0),
)


def _totals(calls, cost, unpriced) -> Totals:
    return Totals(int(calls), Decimal(str(cost)), int(unpriced))


class CRUDUsageLedger:
    """One row a person a day, added to in one statement: two calls at once both count."""

    def add(
        self,
        db: Session,
        *,
        telegram_id: int,
        day: datetime.date,
        cost_usd: Optional[float],
    ) -> None:
        """One model call. ``cost_usd`` None: its price is not known. Commits."""
        table = UsageLedger.__table__
        cost = Decimal(format(cost_usd or 0, ".10f"))
        now = datetime.datetime.now(UTC)
        insert = _INSERT[db.get_bind().dialect.name](table).values(
            telegram_id=telegram_id,
            day=day,
            calls=1,
            cost_usd=cost,
            unpriced_calls=0 if cost_usd is not None else 1,
            created_at=now,
            updated_at=now,
        )
        db.execute(
            insert.on_conflict_do_update(
                index_elements=[table.c.telegram_id, table.c.day],
                set_={
                    "calls": table.c.calls + insert.excluded.calls,
                    "cost_usd": table.c.cost_usd + insert.excluded.cost_usd,
                    "unpriced_calls": (
                        table.c.unpriced_calls + insert.excluded.unpriced_calls
                    ),
                    "updated_at": now,
                },
            )
        )
        db.commit()

    def total(self, db: Session, *, since: Optional[datetime.date] = None) -> Totals:
        """Everyone, the calls with no person included. ``since``: that day and after."""
        statement = select(*_SUMS)
        if since is not None:
            statement = statement.where(UsageLedger.day >= since)
        return _totals(*db.execute(statement).one())

    def of_person(
        self, db: Session, telegram_id: int, *, since: Optional[datetime.date] = None
    ) -> Totals:
        statement = select(*_SUMS).where(UsageLedger.telegram_id == telegram_id)
        if since is not None:
            statement = statement.where(UsageLedger.day >= since)
        return _totals(*db.execute(statement).one())

    def by_person(
        self, db: Session, *, since: Optional[datetime.date] = None
    ) -> dict[int, Totals]:
        """{telegram_id: totals}, with ``NO_PERSON`` as a key when it has any."""
        statement = select(UsageLedger.telegram_id, *_SUMS).group_by(
            UsageLedger.telegram_id
        )
        if since is not None:
            statement = statement.where(UsageLedger.day >= since)
        return {row[0]: _totals(*row[1:]) for row in db.execute(statement)}


crud_usage_ledger = CRUDUsageLedger()
