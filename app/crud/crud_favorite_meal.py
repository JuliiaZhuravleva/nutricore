from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.crud.crud_personal_food import crud_personal_food
from app.models.favorite_meal import FavoriteMeal
from app.models.meal import Meal
from app.models.personal_food import PersonalFood


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
        """By its own words, else by any name its dish answers to: a rename or a merge
        moves a dish and its ⭐, while the row keeps the words it was saved under."""
        # Compared in Python: SQLite's lower() folds ASCII only, and the names
        # are Russian.
        wanted = name.casefold()
        for fav in self.list_active(db, user_id):
            if fav.name.casefold() == wanted:
                return fav
        dish = crud_personal_food.find_by_name(db, user_id, name)
        return self.active_of_dish(db, dish.id) if dish is not None else None

    def dish_of(self, db: Session, fav: FavoriteMeal) -> PersonalFood:
        """Its dish, past any merge. A row an old container wrote gets linked here.

        Only flushes. Reading a favourite asks for nothing, so a hidden dish stays so.
        """
        dish = db.get(PersonalFood, fav.personal_food_id or 0)
        if dish is not None:
            return crud_personal_food.live_of(db, dish)
        dish = crud_personal_food.find_by_name(
            db, fav.user_id, fav.name
        ) or crud_personal_food.for_favourite(db, fav.user_id, fav.name)
        fav.personal_food_id = dish.id
        db.flush()
        return dish

    def dish_if_any(self, db: Session, fav: FavoriteMeal) -> PersonalFood | None:
        """As ``dish_of``, with no row made and no link written: for a mere look."""
        dish = db.get(PersonalFood, fav.personal_food_id or 0)
        if dish is not None:
            return crud_personal_food.live_of(db, dish)
        return crud_personal_food.find_by_name(db, fav.user_id, fav.name)

    def active_of_dish(self, db: Session, dish_id: int) -> FavoriteMeal | None:
        """The not-removed favourite of this dish, the most recently used of them."""
        recency = func.coalesce(FavoriteMeal.last_used_at, FavoriteMeal.created_at)
        stmt = (
            select(FavoriteMeal)
            .where(
                FavoriteMeal.personal_food_id == dish_id,
                FavoriteMeal.removed_at.is_(None),
            )
            .order_by(recency.desc(), FavoriteMeal.id.desc())
        )
        return db.scalars(stmt).first()

    def create_from_meal(self, db: Session, meal: Meal, name: str) -> FavoriteMeal:
        """A whole meal: a portion with no grams of its own."""
        dish = crud_personal_food.for_favourite(db, meal.user_id, name)
        db_obj = FavoriteMeal(
            user_id=meal.user_id,
            name=name,
            personal_food_id=dish.id,
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
        usual_grams: float | None = None,
    ) -> FavoriteMeal:
        """One dish of a meal, at the portion that item holds."""
        dish = crud_personal_food.for_favourite(db, user_id, name)
        db_obj = FavoriteMeal(
            user_id=user_id,
            name=name,
            personal_food_id=dish.id,
            usual_grams=usual_grams,
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
