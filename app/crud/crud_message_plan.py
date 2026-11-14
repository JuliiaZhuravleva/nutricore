"""The plan of a message: one row per message read as several actions.

The unique key on (chat_id, message_id) is the claim, so ``open`` returning None means
the message is already planned (a replay or an edit) and nothing more may happen.
"""

from datetime import UTC, datetime
from typing import List, Optional

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.message_plan import MessagePlan

READING, DONE, FAILED, SINGLE = "reading", "done", "failed", "single"


class CRUDMessagePlan:
    READING, DONE, FAILED, SINGLE = READING, DONE, FAILED, SINGLE

    def open(self, db: Session, **fields) -> Optional[MessagePlan]:
        """A new plan in ``reading``, committed; None if the message has one."""
        plan = MessagePlan(status=READING, **fields)
        db.add(plan)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            return None
        db.refresh(plan)
        return plan

    def of_message(
        self, db: Session, chat_id: int, message_id: int
    ) -> Optional[MessagePlan]:
        return db.scalar(
            select(MessagePlan).where(
                MessagePlan.chat_id == chat_id, MessagePlan.message_id == message_id
            )
        )

    def reading_of(
        self, db: Session, user_id: int, since: datetime
    ) -> Optional[MessagePlan]:
        """The person's plan still being read, opened after ``since``."""
        return db.scalar(
            select(MessagePlan)
            .where(
                MessagePlan.user_id == user_id,
                MessagePlan.status == READING,
                MessagePlan.created_at >= since,
            )
            .order_by(MessagePlan.id.desc())
            .limit(1)
        )

    def stuck(self, db: Session, before: datetime) -> List[MessagePlan]:
        """Plans still reading, opened before ``before``: a restart lost them."""
        return list(
            db.scalars(
                select(MessagePlan)
                .where(MessagePlan.status == READING, MessagePlan.created_at < before)
                .order_by(MessagePlan.id)
            )
        )

    def owed(self, db: Session, before: datetime) -> List[MessagePlan]:
        """Done plans, opened before ``before``, whose messages never went out (a
        restart after the commit): ``line_ids`` is written, even as [], once they
        are sent."""
        return list(
            db.scalars(
                select(MessagePlan)
                .where(
                    MessagePlan.status == DONE,
                    MessagePlan.line_ids.is_(None),
                    MessagePlan.created_at < before,
                )
                .order_by(MessagePlan.id)
            )
        )

    # --- the row's state moves (pure row work; the caller holds any lock) ---------

    def ended(self, db: Session, plan_id: int, status: str) -> Optional[MessagePlan]:
        """Ends a reading plan, in one UPDATE: a sweep racing ``execute`` waits for
        its commit and then finds the row done, never turns done into failed."""
        moved = db.execute(
            update(MessagePlan)
            .where(MessagePlan.id == plan_id, MessagePlan.status == READING)
            .values(status=status)
        ).rowcount
        db.commit()
        return db.get(MessagePlan, plan_id) if moved else None

    def set_line(self, db: Session, plan_id: int, line_id: Optional[int]) -> None:
        plan = db.get(MessagePlan, plan_id)
        if plan is not None:
            plan.line_message_id = line_id
            db.commit()

    def shown(
        self, db: Session, plan_id: int, line_ids: list, result_id: Optional[int]
    ) -> None:
        plan = db.get(MessagePlan, plan_id)
        if plan is not None:
            plan.line_ids = list(line_ids)
            plan.result_message_id = result_id
            db.commit()

    def unanswered(self, db: Session, plan_id: int) -> Optional[tuple]:
        """(chat_id, message_id, line id) of a plan that is no longer reading and
        whose lines never went out (a crash after its commit), else None."""
        plan = db.get(MessagePlan, plan_id)
        if plan is None or plan.status == READING:
            return None
        if plan.status == DONE and plan.line_ids is not None:
            return None
        if plan.status == FAILED:
            return None
        return plan.chat_id, plan.message_id, plan.line_message_id

    def fall_back(self, db: Session, plan_id: int) -> bool:
        """The message goes the way it went before plans. False: ended already."""
        return self.ended(db, plan_id, SINGLE) is not None

    def fail(self, db: Session, plan_id: int) -> bool:
        """Nothing written: the row is failed, its failed line not yet shown
        (``fail_shown`` False; ``fail_shown_of`` sets it). False: ended meanwhile."""
        plan = self.ended(db, plan_id, FAILED)
        if plan is None:
            return False
        plan.commands = {**(plan.commands or {}), "fail_shown": False}
        db.commit()
        return True

    def fail_shown(self, db: Session, plan_id: int) -> None:
        """The failed line went out: the sweep leaves the plan alone. A new dict."""
        plan = db.get(MessagePlan, plan_id)
        if plan is not None:
            plan.commands = {**(plan.commands or {}), "fail_shown": True}
            db.commit()

    def failed_unshown(
        self, db: Session, before: datetime, since: datetime
    ) -> List[MessagePlan]:
        """Failed plans created in [since, before) whose failed line never went out.
        Only a plan that says so (``fail_shown`` False, not absent: a row from before
        the marker is left alone). Filtered here, not in SQL: no JSON operators."""
        rows = db.scalars(
            select(MessagePlan)
            .where(
                MessagePlan.status == FAILED,
                MessagePlan.created_at < before,
                MessagePlan.created_at >= since,
            )
            .order_by(MessagePlan.id)
        )
        return [p for p in rows if (p.commands or {}).get("fail_shown") is False]

    def done_between(
        self, db: Session, after: datetime, upto: datetime
    ) -> List[MessagePlan]:
        """Done plans created in (after, upto]: a bounded window, so a sweep that
        moves ``after`` forward never reads a plan twice."""
        return list(
            db.scalars(
                select(MessagePlan)
                .where(
                    MessagePlan.status == DONE,
                    MessagePlan.created_at > after,
                    MessagePlan.created_at <= upto,
                )
                .order_by(MessagePlan.id)
            )
        )

    def close_asks(self, plan: MessagePlan) -> list:
        """Every open question of the plan becomes ``closed``, in one new dict; the
        ask dicts that were open are returned (as they were). The caller commits."""
        asks = self.asks_of(plan)
        was = [dict(a) for a in asks if a.get("state") == "open"]
        if was:
            self.set_asks(
                plan,
                [
                    {**a, "state": "closed"} if a.get("state") == "open" else a
                    for a in asks
                ],
            )
        return was

    def nothing(self, db: Session, plan_id: int) -> bool:
        """Done with no lines to send. False: ended meanwhile."""
        plan = self.ended(db, plan_id, DONE)
        if plan is None:
            return False
        plan.line_ids = []
        db.commit()
        return True

    def reopen(self, db: Session, plan: MessagePlan, line_id: Optional[int]) -> bool:
        """A failed plan, already locked and refreshed, back to reading on the
        tapped line. False (rolled back): it is not failed any more."""
        if plan.status != FAILED:
            db.rollback()
            return False
        plan.status = READING
        plan.line_message_id = line_id
        plan.created_at = datetime.now(UTC)  # the sweep's clock starts again
        db.commit()
        return True

    def finish(
        self, plan: MessagePlan, meal_ids: list, group_id: str, commands: dict
    ) -> None:
        """The plan row as done: what ran and what it wrote. The caller commits."""
        plan.status = DONE
        plan.meal_ids = meal_ids
        plan.group_id = group_id
        plan.commands = commands

    # --- the plan's questions: ``commands["asks"]``, a JSON list (no migration) ------

    def asks_of(self, plan: MessagePlan) -> list:
        """The questions, as copies: the stored list is never changed in place."""
        return [dict(a) for a in (plan.commands or {}).get("asks") or []]

    def set_asks(self, plan: MessagePlan, asks: list) -> None:
        """Assigns a new dict: SQLAlchemy does not see a change made inside the old
        one. The caller commits."""
        plan.commands = {**(plan.commands or {}), "asks": [dict(a) for a in asks]}

    def ask_sent(self, db: Session, plan_id: int, q: int, message_id: int) -> None:
        """Keeps the message a question went out in."""
        plan = db.get(MessagePlan, plan_id, with_for_update=True)
        if plan is None:
            return
        asks = self.asks_of(plan)
        for ask in asks:
            if ask.get("q") == q:
                ask["message_id"] = message_id
        self.set_asks(plan, asks)
        db.commit()

    def undone(self, db: Session, plan: MessagePlan, commands: dict) -> None:
        """Marks the plan's dish changes as taken back, committed."""
        plan.commands = {**commands, "undone": True}
        db.commit()


crud_message_plan = CRUDMessagePlan()
