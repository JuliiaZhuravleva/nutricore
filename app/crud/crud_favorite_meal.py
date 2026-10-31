from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.favorite_meal import FavoriteMeal
from app.models.meal import Meal


class CRUDFavoriteMeal:
    def get_for_user(
        self, db: Session, favorite_id: int, user_id: int
    ) -> FavoriteMeal | None:
        """The favorite if it belongs to this user (removed ones included)."""
        stmt = select(FavoriteMeal).where(
            FavoriteMeal.id == favorite_id, FavoriteMeal.user_id == user_id
        )
        return db.execute(stmt).scalar_one_or_none()

    def list_active(self, db: Session, user_id: int) -> list[FavoriteMeal]:
        """Not-removed favorites, most recently used (or added) first."""
        recency = func.coalesce(FavoriteMeal.last_used_at, FavoriteMeal.created_at)
        stmt = (
            select(FavoriteMeal)
            .where(FavoriteMeal.user_id == user_id, FavoriteMeal.removed_at.is_(None))
            .order_by(recency.desc(), FavoriteMeal.id.desc())
        )
        return list(db.scalars(stmt).all())

    def find_active_by_name(
        self, db: Session, user_id: int, name: str
    ) -> FavoriteMeal | None:
        # Compared in Python: SQLite's lower() folds ASCII only, and the names
        # are Russian.
        wanted = name.casefold()
        for fav in self.list_active(db, user_id):
            if fav.name.casefold() == wanted:
                return fav
        return None

    def create_from_meal(self, db: Session, meal: Meal, name: str) -> FavoriteMeal:
        db_obj = FavoriteMeal(
            user_id=meal.user_id,
            name=name,
            calories=meal.calories,
            proteins=meal.proteins,
            fats=meal.fats,
            carbohydrates=meal.carbohydrates,
            nutrients=meal.nutrients,
            source_meal_id=meal.id,
        )
        db.add(db_obj)
        db.commit()
        db.refresh(db_obj)
        return db_obj

    def create_from_numbers(
        self,
        db: Session,
        *,
        user_id: int,
        name: str,
        numbers: dict,
        nutrients: dict,
        source_meal_id: int,
    ) -> FavoriteMeal:
        """One dish of a meal, at the portion that item holds."""
        db_obj = FavoriteMeal(
            user_id=user_id,
            name=name,
            calories=numbers["calories"],
            proteins=numbers["proteins"],
            fats=numbers["fats"],
            carbohydrates=numbers["carbohydrates"],
            nutrients=nutrients,
            source_meal_id=source_meal_id,
        )
        db.add(db_obj)
        db.commit()
        db.refresh(db_obj)
        return db_obj

    def mark_used(self, db: Session, fav: FavoriteMeal) -> None:
        fav.use_count = (fav.use_count or 0) + 1
        fav.last_used_at = datetime.now(UTC)
        db.commit()

    def set_removed(self, db: Session, fav: FavoriteMeal, removed: bool) -> None:
        fav.removed_at = datetime.now(UTC) if removed else None
        db.commit()


crud_favorite_meal = CRUDFavoriteMeal()
