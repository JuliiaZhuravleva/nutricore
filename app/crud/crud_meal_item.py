from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.meal_item import MealItem

NAME_MAX = 120


class CRUDMealItem:
    """Reads and writes of a meal's dishes.

    A write only flushes. An item change and its meal's totals must land in one
    transaction, so the commit belongs to the caller.
    """

    def get(self, db: Session, item_id: int) -> MealItem | None:
        """The item, also when removed: a stale button needs to tell the two apart."""
        return db.get(MealItem, item_id)

    def live(self, db: Session, meal_id: int) -> list[MealItem]:
        """The meal's items that are not removed, in their order."""
        stmt = (
            select(MealItem)
            .where(MealItem.meal_id == meal_id, MealItem.removed_at.is_(None))
            .order_by(MealItem.position, MealItem.id)
        )
        return list(db.scalars(stmt).all())

    def live_names(self, db: Session, meal_ids: list[int]) -> dict[int, list[str]]:
        """Meal id → the names of its live items, in their order. One query."""
        stmt = (
            select(MealItem.meal_id, MealItem.name)
            .where(MealItem.meal_id.in_(meal_ids), MealItem.removed_at.is_(None))
            .order_by(MealItem.position, MealItem.id)
        )
        names: dict[int, list[str]] = {}
        for meal_id, name in db.execute(stmt):
            names.setdefault(meal_id, []).append(name)
        return names

    def add(
        self,
        db: Session,
        *,
        meal_id: int,
        name: str,
        personal_food_id: int | None = None,
        **fields,
    ) -> MealItem:
        """A new item, born in this meal and under this dish."""
        item = MealItem(
            meal_id=meal_id,
            origin_meal_id=meal_id,
            name=name.strip()[:NAME_MAX],
            personal_food_id=personal_food_id,
            original_food_id=personal_food_id,
            **fields,
        )
        db.add(item)
        db.flush()
        return item


crud_meal_item = CRUDMealItem()
