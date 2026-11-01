from sqlalchemy.orm import Session

from app.crud.crud_action_event import crud_action_event
from app.models.action_event import CORRECTED, CORRECTION_KINDS
from app.models.record_event import EVENT_KINDS, RecordEvent


class CRUDRecordEvent:
    """The change log. A write only flushes: it joins the change it describes."""

    def get(self, db: Session, event_id: int) -> RecordEvent | None:
        return db.get(RecordEvent, event_id)

    def add(
        self,
        db: Session,
        *,
        user_id: int,
        kind: str,
        meal_id: int | None = None,
        food_id: int | None = None,
        before: dict | None = None,
        after: dict | None = None,
        source_id: int | None = None,
        undoes_event_id: int | None = None,
    ) -> RecordEvent:
        if kind not in EVENT_KINDS:
            raise ValueError(f"unknown event kind: {kind}")
        event = RecordEvent(
            user_id=user_id,
            kind=kind,
            meal_id=meal_id,
            food_id=food_id,
            before=before,
            after=after,
            source_id=source_id,
            undoes_event_id=undoes_event_id,
        )
        db.add(event)
        db.flush()
        # Every change comes through here. An undo takes a correction back: not another.
        if undoes_event_id is None and kind in CORRECTION_KINDS:
            crud_action_event.add_for_user(
                db, user_id=user_id, kind=CORRECTED, label=kind
            )
        return event


crud_record_event = CRUDRecordEvent()
