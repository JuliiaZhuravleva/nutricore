"""Personal food DB learning-loop write-back task (B4 / ADR-0003).

Fire-and-forget: called from confirm_meal in telegram.py after the meal is
saved.  Embeds the canonical food name and upserts it into personal_foods so
the personal RAG DB grows with each confirmed meal.

Idempotent / retry-safe:
  - upsert() dedup key is (user_id, lower(canonical_name)) — calling twice for
    the same food only increments times_used, never creates duplicate rows.
  - embedding is skipped when text_embedded already exists in
    personal_food_embeddings for this food, so a Celery retry after a transient
    failure does not double-embed or double-pay the OpenAI embedding API.
"""

import asyncio
import logging
from typing import Optional

from app.crud.crud_personal_food import crud_personal_food
from app.db.session import SessionLocal
from app.services.openai_service import OpenAIService
from celery_app.celery_app import app

logger = logging.getLogger(__name__)


@app.task(bind=True, max_retries=3, default_retry_delay=30)
def embed_and_save_personal_food(
    self,
    user_id: int,
    canonical_name: str,
    meal_id: Optional[int] = None,
    resolution_source: Optional[str] = None,
    barcode: Optional[str] = None,
    per_100g_calories: Optional[float] = None,
    per_100g_proteins: Optional[float] = None,
    per_100g_fats: Optional[float] = None,
    per_100g_carbs: Optional[float] = None,
) -> int:
    """Upsert confirmed food into personal_foods and embed canonical name.

    Returns personal_food_id on success.

    Retry contract: up to 3 attempts, 30 s apart (transient DB / network).
    Idempotency contract:
      - upsert() increments times_used on repeat call; never inserts duplicate.
      - add_embedding() is called only when canonical_name is not yet embedded.
    """
    try:
        with SessionLocal() as db:
            # Step 1 — idempotent upsert (dedup on lower(canonical_name))
            pf = crud_personal_food.upsert(
                db,
                user_id=user_id,
                canonical_name=canonical_name,
                meal_id=meal_id,
                resolution_source=resolution_source,
                barcode=barcode,
                per_100g_calories=per_100g_calories,
                per_100g_proteins=per_100g_proteins,
                per_100g_fats=per_100g_fats,
                per_100g_carbs=per_100g_carbs,
            )
            personal_food_id = pf.id

            # Step 2 — embed canonical name (skip if already embedded for this food)
            existing = crud_personal_food.get_embeddings_for_food(
                db, personal_food_id=personal_food_id
            )
            already_embedded = any(
                e.text_embedded == canonical_name for e in existing
            )

            if not already_embedded:
                # Do NOT reuse the process-wide async OpenAIService singleton here:
                # its httpx AsyncClient binds to the event loop of the FIRST task and
                # breaks on the next asyncio.run() in a long-lived prefork worker
                # ("Event loop is closed") — the write-back would fail for every food
                # after the first, per worker (F3). Create a fresh service bound to
                # THIS run's loop and close its client when done.
                async def _embed() -> list:
                    svc = OpenAIService()
                    try:
                        return await svc.embed_text(canonical_name)
                    finally:
                        await svc.client.close()

                embedding: list = asyncio.run(_embed())
                crud_personal_food.add_embedding(
                    db,
                    personal_food_id=personal_food_id,
                    text_embedded=canonical_name,
                    embedding=embedding,
                )
                logger.info(
                    "embed_and_save_personal_food: saved personal_food_id=%d"
                    " canonical=%r user_id=%d (new embedding)",
                    personal_food_id,
                    canonical_name,
                    user_id,
                )
            else:
                logger.debug(
                    "embed_and_save_personal_food: embedding already exists"
                    " for personal_food_id=%d canonical=%r — skipping embed call",
                    personal_food_id,
                    canonical_name,
                )

        return personal_food_id

    except Exception as exc:
        if self.request.retries >= self.max_retries:
            # Retries exhausted — the confirmed food is PERMANENTLY not saved to the
            # personal DB (the whole point of this fire-and-forget task). Log at ERROR
            # with a distinct signature so this is operationally visible and greppable,
            # not lost among the per-attempt WARNINGs (F8).
            logger.error(
                "embed_and_save_personal_food: PERMANENTLY FAILED after %d retries "
                "for user_id=%d canonical=%r — food NOT saved to personal DB: %s",
                self.request.retries,
                user_id,
                canonical_name,
                exc,
                exc_info=True,
            )
        else:
            logger.warning(
                "embed_and_save_personal_food: attempt %d failed for user_id=%d "
                "canonical=%r (will retry): %s",
                self.request.retries,
                user_id,
                canonical_name,
                exc,
                exc_info=True,
            )
        raise self.retry(exc=exc)
