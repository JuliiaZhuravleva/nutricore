"""CRUDPersonalFood — data-access layer for the personal food DB (ADR-0003 / B1).

Public interface:
  upsert()                — idempotent insert-or-update by (user_id, lower(canonical_name))
  get_by_barcode()        — exact-barcode lookup (Phase A short-circuit for B3)
  find_similar()          — ANN query over personal_food_embeddings <=> cosine distance
                            THIS IS THE MOCKABLE B6 SEAM (see ADR-0003 §4d)
  add_embedding()         — insert one embedding row for a personal food
  get_embeddings_for_food() — all embedding rows for a food (for B4 dedup checks)

find_similar() is the ONLY method that uses Postgres-only pgvector <=> operator.
All other methods are SQLite-compatible and are covered by unit tests.
B6 mocks find_similar to test SavedFoodRAGStrategy branch logic on SQLite.
"""

from __future__ import annotations

import datetime
import logging
from typing import List, Optional

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.personal_food import PersonalFood, PersonalFoodEmbedding

# NOTE: upsert() deliberately takes explicit kwargs rather than a PersonalFoodCreate/
# PersonalFoodUpdate DTO — idempotent upsert semantics (insert-or-increment) don't map
# cleanly onto the project's Create/Update schema split, and the Celery write-back task
# already passes discrete fields. The Pydantic schemas exist for the REST/response layer.

logger = logging.getLogger(__name__)

UTC = datetime.timezone.utc


class CRUDPersonalFood:
    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def upsert(
        self,
        db: Session,
        *,
        user_id: int,
        canonical_name: str,
        meal_id: Optional[int] = None,
        resolution_source: Optional[str] = None,
        barcode: Optional[str] = None,
        brand: Optional[str] = None,
        per_100g_calories: Optional[float] = None,
        per_100g_proteins: Optional[float] = None,
        per_100g_fats: Optional[float] = None,
        per_100g_carbs: Optional[float] = None,
    ) -> PersonalFood:
        """Idempotent insert-or-update keyed on (user_id, lower(canonical_name)).

        Insert semantics (new food):
          Creates a new row with times_used=1.

        Update semantics (same food confirmed again):
          Increments times_used, refreshes last_used_at.
          Overwrites macros/barcode/resolution_source if provided (non-None).
          Does NOT overwrite with None — callers may omit unchanged fields.

        This method is idempotent: calling it twice for the same
        (user_id, canonical_name) pair is safe and retry-friendly (B4 Celery task).
        """
        name_key = canonical_name.strip().lower()
        # Use Python-side lower() for the case-insensitive match so we stay
        # Unicode-correct on both Postgres (where SQL lower() is also fine) and
        # SQLite (whose built-in lower() is ASCII-only, breaking Cyrillic/etc.).
        # A personal DB has <1000 rows per user (ADR-0003 §1), so the scan is cheap.
        all_foods_stmt = select(PersonalFood).where(PersonalFood.user_id == user_id)
        all_foods = list(db.execute(all_foods_stmt).scalars().all())
        existing = next(
            (f for f in all_foods if f.canonical_name.strip().lower() == name_key),
            None,
        )

        now = datetime.datetime.now(UTC)

        if existing is not None:
            # Count only a genuinely new confirmation. The Celery write-back task
            # (B4) can re-run with the SAME meal_id on retry (e.g. after an embedding
            # failure); gating times_used on a changed meal_id keeps it retry-safe (F7)
            # so the counter isn't inflated for a single confirmed meal. meal_id=None
            # (no provenance) falls back to counting each call.
            is_new_confirmation = meal_id is None or existing.meal_id != meal_id
            if is_new_confirmation:
                existing.times_used = (existing.times_used or 0) + 1
                existing.last_used_at = now
            existing.updated_at = now
            if meal_id is not None:
                existing.meal_id = meal_id
            if resolution_source is not None:
                existing.resolution_source = resolution_source
            if barcode is not None:
                existing.barcode = barcode
            if brand is not None:
                existing.brand = brand
            if per_100g_calories is not None:
                existing.per_100g_calories = per_100g_calories
            if per_100g_proteins is not None:
                existing.per_100g_proteins = per_100g_proteins
            if per_100g_fats is not None:
                existing.per_100g_fats = per_100g_fats
            if per_100g_carbs is not None:
                existing.per_100g_carbs = per_100g_carbs
            db.commit()
            db.refresh(existing)
            return existing

        # New food — insert
        db_obj = PersonalFood(
            user_id=user_id,
            canonical_name=canonical_name.strip(),
            meal_id=meal_id,
            resolution_source=resolution_source,
            barcode=barcode,
            brand=brand,
            per_100g_calories=per_100g_calories,
            per_100g_proteins=per_100g_proteins,
            per_100g_fats=per_100g_fats,
            per_100g_carbs=per_100g_carbs,
            times_used=1,
            last_used_at=now,
        )
        db.add(db_obj)
        db.commit()
        db.refresh(db_obj)
        return db_obj

    def add_embedding(
        self,
        db: Session,
        *,
        personal_food_id: int,
        text_embedded: str,
        embedding: List[float],
    ) -> PersonalFoodEmbedding:
        """Insert one embedding row for a personal food (canonical name or alias).

        Callers (B4 write-back task) do a best-effort app-level dedup check first,
        but the (personal_food_id, text_embedded) unique constraint is the
        authoritative backstop against duplicate vectors under concurrent confirms
        or a task retry racing a slow first attempt (F5): on a duplicate we roll
        back and return the row that won.
        """
        db_obj = PersonalFoodEmbedding(
            personal_food_id=personal_food_id,
            text_embedded=text_embedded,
            embedding=embedding,
        )
        db.add(db_obj)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            existing = db.execute(
                select(PersonalFoodEmbedding).where(
                    PersonalFoodEmbedding.personal_food_id == personal_food_id,
                    PersonalFoodEmbedding.text_embedded == text_embedded,
                )
            ).scalars().first()
            if existing is not None:
                return existing
            raise
        db.refresh(db_obj)
        return db_obj

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def get_by_barcode(
        self,
        db: Session,
        *,
        barcode: str,
        user_id: int,
    ) -> Optional[PersonalFood]:
        """Exact-barcode lookup scoped to one user.

        Phase A of SavedFoodRAGStrategy (ADR-0003 §4c): if the scanned barcode
        is already in personal_foods, serve it directly — no OFF call needed.

        Uses the partial index: personal_foods_user_barcode_idx
        (WHERE barcode IS NOT NULL) in Postgres.

        (user_id, barcode) is NOT unique — the dedup key is the canonical name, so
        the same barcode can legitimately land on two rows (same product confirmed
        under two different vision-read names). Return the most-used row rather than
        raising MultipleResultsFound (F6).
        """
        stmt = (
            select(PersonalFood)
            .where(
                PersonalFood.user_id == user_id,
                PersonalFood.barcode == barcode,
            )
            .order_by(PersonalFood.times_used.desc(), PersonalFood.id.desc())
        )
        return db.execute(stmt).scalars().first()

    def get_embeddings_for_food(
        self,
        db: Session,
        *,
        personal_food_id: int,
    ) -> List[PersonalFoodEmbedding]:
        """Return all embedding rows for a given personal food.

        Used by B4 write-back to check whether a text variant has already been
        embedded before calling embed_text() again (avoid duplicate work on retry).
        """
        stmt = select(PersonalFoodEmbedding).where(
            PersonalFoodEmbedding.personal_food_id == personal_food_id,
        )
        return list(db.execute(stmt).scalars().all())

    def find_similar(
        self,
        db: Session,
        *,
        embedding: List[float],
        threshold: float,
        user_id: int,
    ) -> Optional[tuple[PersonalFood, float]]:
        """ANN lookup with mandatory user_id filter.

        Returns (PersonalFood, cosine_distance) if the closest match is within
        threshold, else None.  threshold is cosine distance (0=identical,
        2=opposite); the default from config is 0.15 (≈ cosine similarity 0.85).

        *** THIS IS THE MOCKABLE B6 SEAM (ADR-0003 §4d) ***
        SQLite has no pgvector <=> operator.  Unit tests MUST patch this method:
            with patch.object(crud_personal_food, "find_similar", return_value=...):
                ...
        The real ANN / threshold correctness check is a manual post-deploy
        verification (see ADR-0003 §4d / B6 test plan).

        Implementation uses raw SQL with the pgvector <=> operator.  The
        embedding list is serialised to pgvector's "[x1,x2,...]" string format
        and cast to vector(1536) inside the query so psycopg2 passes it cleanly.
        """
        emb_str = "[" + ",".join(str(v) for v in embedding) + "]"
        stmt = text(
            """
            SELECT
                pfe.personal_food_id,
                (pfe.embedding <=> :embedding::vector) AS cosine_distance
            FROM personal_food_embeddings pfe
            JOIN personal_foods pf ON pf.id = pfe.personal_food_id
            WHERE pf.user_id = :user_id
            ORDER BY cosine_distance
            LIMIT 1
            """
        )
        try:
            row = db.execute(
                stmt, {"embedding": emb_str, "user_id": user_id}
            ).first()
        except Exception:
            # Non-blocking by contract (return None), but this is a SYSTEMIC failure
            # (pgvector missing, embedding-dimension mismatch, dropped connection,
            # a SQL bug) — NOT a routine no-match (that path has row=None, below).
            # Log at ERROR so it's distinguishable from a legitimate miss and doesn't
            # silently zero out the personal-DB hit rate (F4).
            logger.error(
                "find_similar: ANN query FAILED for user_id=%s — degrading to "
                "no-match; check pgvector availability and embedding dimensions",
                user_id,
                exc_info=True,
            )
            return None

        if row is None:
            return None

        distance = float(row.cosine_distance)
        if distance > threshold:
            return None

        pf = db.get(PersonalFood, row.personal_food_id)
        if pf is None:
            return None
        return pf, distance


crud_personal_food = CRUDPersonalFood()
