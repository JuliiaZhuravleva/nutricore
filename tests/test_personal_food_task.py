"""Unit tests for B4 learning-loop write-back (ADR-0003).

Covers:
  _schedule_personal_food_save (telegram.py helper):
    - dispatches task with correctly extracted args (image-path signals)
    - dispatches task without resolution_signals (text-input path)
    - extracts barcode from barcode_raw in signals
    - per-100g correctly reverse-scaled from total + portion_grams
    - canonical_name falls back through saved_food_name → product_name → foods[0]
    - empty/blank canonical_name suppresses dispatch
    - delay() failure is logged and never propagates

  embed_and_save_personal_food (Celery task, called directly):
    - creates PersonalFood row + PersonalFoodEmbedding on first call
    - idempotent: re-call increments times_used, does NOT re-embed same text
    - persists barcode on the personal_food row
    - skips embed_text when embedding already exists for canonical_name

SQLite is used for all DB tests (same as conftest.py).
embed_text is mocked as an AsyncMock — asyncio.run() executes the coroutine.
"""

from __future__ import annotations

import asyncio
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.crud.crud_personal_food import crud_personal_food
from app.crud.crud_user import crud_user
from app.db.base import Base
from app.schemas.user import UserCreate

# ---------------------------------------------------------------------------
# Helpers / fixtures for task-level DB
# ---------------------------------------------------------------------------


@pytest.fixture
def task_db():
    """Fresh in-memory SQLite DB scoped to the personal-food task tests.

    Separate from conftest.py's db_session so the task tests can control
    SessionLocal patching independently.
    """
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    yield Session
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


def _make_user(db, telegram_id: int = 99_001):
    return crud_user.create(db, obj_in=UserCreate(telegram_id=telegram_id))


# ---------------------------------------------------------------------------
# _schedule_personal_food_save tests
# ---------------------------------------------------------------------------


_BASE_NUTRITION = {
    "foods": ["Греческий йогурт FAGE 2%"],
    "calories": 194.0,  # for 200g portion
    "protein": 18.0,
    "fats": 10.0,
    "carbs": 7.6,
    "portion": "200г",
}

_BASE_SIGNALS = {
    "product_name": "Greek Yogurt FAGE 2%",
    "barcode_raw": "3017624010701",
    "portion_grams": 200.0,
    "saved_food_name": None,
}


def _call_schedule(
    nutrition=None,
    resolution_signals=None,
    resolution_source="barcode_off",
    user_id=42,
    meal_id=7,
):
    """Thin wrapper so tests don't repeat the import."""
    from app.services.telegram import _schedule_personal_food_save

    _schedule_personal_food_save(
        user_id=user_id,
        meal_id=meal_id,
        nutrition=nutrition or _BASE_NUTRITION,
        resolution_signals=resolution_signals,
        resolution_source=resolution_source,
    )


def test_schedule_dispatches_task_with_correct_user_and_meal(monkeypatch):
    """delay() is called with the correct user_id and meal_id."""
    delay_mock = MagicMock()
    with patch(
        "celery_app.tasks.personal_food.embed_and_save_personal_food.delay",
        delay_mock,
    ):
        _call_schedule(resolution_signals=_BASE_SIGNALS, user_id=42, meal_id=7)

    delay_mock.assert_called_once()
    kwargs = delay_mock.call_args.kwargs
    assert kwargs["user_id"] == 42
    assert kwargs["meal_id"] == 7


def test_schedule_extracts_product_name_as_canonical(monkeypatch):
    """product_name from signals is preferred as canonical_name."""
    delay_mock = MagicMock()
    with patch(
        "celery_app.tasks.personal_food.embed_and_save_personal_food.delay",
        delay_mock,
    ):
        signals = dict(_BASE_SIGNALS, product_name="FAGE Total 2%", saved_food_name=None)
        _call_schedule(resolution_signals=signals)

    kwargs = delay_mock.call_args.kwargs
    assert kwargs["canonical_name"] == "FAGE Total 2%"


def test_schedule_prefers_saved_food_name_over_product_name(monkeypatch):
    """saved_food_name (saved_rag path) beats product_name."""
    delay_mock = MagicMock()
    with patch(
        "celery_app.tasks.personal_food.embed_and_save_personal_food.delay",
        delay_mock,
    ):
        signals = dict(
            _BASE_SIGNALS,
            saved_food_name="Йогурт FAGE (сохранён)",
            product_name="FAGE Total",
        )
        _call_schedule(resolution_signals=signals)

    kwargs = delay_mock.call_args.kwargs
    assert kwargs["canonical_name"] == "Йогурт FAGE (сохранён)"


def test_schedule_falls_back_to_foods_list(monkeypatch):
    """Falls back to nutrition['foods'][0] when no product_name in signals."""
    delay_mock = MagicMock()
    with patch(
        "celery_app.tasks.personal_food.embed_and_save_personal_food.delay",
        delay_mock,
    ):
        signals = {"portion_grams": 200.0, "barcode_raw": None}
        nutrition = dict(_BASE_NUTRITION, foods=["Творог 5%"])
        _call_schedule(nutrition=nutrition, resolution_signals=signals)

    kwargs = delay_mock.call_args.kwargs
    assert kwargs["canonical_name"] == "Творог 5%"


def test_schedule_no_signals_uses_foods_list(monkeypatch):
    """Text-input path: no resolution_signals → canonical_name from foods[0]."""
    delay_mock = MagicMock()
    with patch(
        "celery_app.tasks.personal_food.embed_and_save_personal_food.delay",
        delay_mock,
    ):
        _call_schedule(resolution_signals=None)  # text input has no signals

    delay_mock.assert_called_once()
    kwargs = delay_mock.call_args.kwargs
    # Falls back to nutrition['foods'][0]
    assert kwargs["canonical_name"] == _BASE_NUTRITION["foods"][0]
    assert kwargs["barcode"] is None


def test_schedule_extracts_barcode(monkeypatch):
    """barcode_raw from signals is passed as barcode to the task."""
    delay_mock = MagicMock()
    with patch(
        "celery_app.tasks.personal_food.embed_and_save_personal_food.delay",
        delay_mock,
    ):
        signals = dict(_BASE_SIGNALS, barcode_raw="3017624010701")
        _call_schedule(resolution_signals=signals)

    kwargs = delay_mock.call_args.kwargs
    assert kwargs["barcode"] == "3017624010701"


def test_schedule_no_barcode_raw_passes_none(monkeypatch):
    """When barcode_raw is absent, barcode=None is passed to the task."""
    delay_mock = MagicMock()
    with patch(
        "celery_app.tasks.personal_food.embed_and_save_personal_food.delay",
        delay_mock,
    ):
        signals = dict(_BASE_SIGNALS, barcode_raw=None)
        _call_schedule(resolution_signals=signals)

    kwargs = delay_mock.call_args.kwargs
    assert kwargs["barcode"] is None


def test_schedule_computes_per100g_from_portion_grams(monkeypatch):
    """With portion_grams=200, per-100g = total * (100/200) = total / 2."""
    delay_mock = MagicMock()
    with patch(
        "celery_app.tasks.personal_food.embed_and_save_personal_food.delay",
        delay_mock,
    ):
        signals = dict(_BASE_SIGNALS, portion_grams=200.0)
        nutrition = dict(_BASE_NUTRITION, calories=200.0, protein=20.0)
        _call_schedule(nutrition=nutrition, resolution_signals=signals)

    kwargs = delay_mock.call_args.kwargs
    # 200g portion → factor = 100/200 = 0.5
    assert kwargs["per_100g_calories"] == pytest.approx(100.0)
    assert kwargs["per_100g_proteins"] == pytest.approx(10.0)


def test_schedule_factor_one_when_no_portion_grams(monkeypatch):
    """When portion_grams is None and the portion string is unparseable, factor=1.0."""
    delay_mock = MagicMock()
    with patch(
        "celery_app.tasks.personal_food.embed_and_save_personal_food.delay",
        delay_mock,
    ):
        signals = {"portion_grams": None, "barcode_raw": None}
        # "половина тарелки" carries no gram value → _parse_portion_grams returns None
        nutrition = dict(
            _BASE_NUTRITION, calories=97.0, protein=9.0, portion="половина тарелки"
        )
        _call_schedule(nutrition=nutrition, resolution_signals=signals)

    kwargs = delay_mock.call_args.kwargs
    assert kwargs["per_100g_calories"] == pytest.approx(97.0)
    assert kwargs["per_100g_proteins"] == pytest.approx(9.0)


def test_schedule_parses_portion_from_nutrition_string(monkeypatch):
    """Text-input path: portion_grams parsed from nutrition['portion'] string."""
    delay_mock = MagicMock()
    with patch(
        "celery_app.tasks.personal_food.embed_and_save_personal_food.delay",
        delay_mock,
    ):
        # No signals → no portion_grams → _parse_portion_grams(nutrition) runs
        nutrition = {
            "foods": ["Рис"],
            "calories": 350.0,
            "protein": 6.0,
            "fats": 1.0,
            "carbs": 77.0,
            "portion": "100г",  # exactly 100g → factor=1.0
        }
        _call_schedule(nutrition=nutrition, resolution_signals=None)

    kwargs = delay_mock.call_args.kwargs
    # portion_grams=100 → factor = 100/100 = 1.0 → unchanged
    assert kwargs["per_100g_calories"] == pytest.approx(350.0)


def test_schedule_empty_foods_suppresses_dispatch(monkeypatch):
    """No canonical_name (empty foods, no product_name) → delay() NOT called."""
    delay_mock = MagicMock()
    with patch(
        "celery_app.tasks.personal_food.embed_and_save_personal_food.delay",
        delay_mock,
    ):
        nutrition = dict(_BASE_NUTRITION, foods=[], portion="100г")
        signals = {"portion_grams": 100.0, "barcode_raw": None}
        _call_schedule(nutrition=nutrition, resolution_signals=signals)

    delay_mock.assert_not_called()


def test_schedule_delay_failure_does_not_propagate(monkeypatch):
    """If delay() raises, _schedule_personal_food_save swallows it."""
    delay_mock = MagicMock(side_effect=RuntimeError("broker down"))
    with patch(
        "celery_app.tasks.personal_food.embed_and_save_personal_food.delay",
        delay_mock,
    ):
        # Must not raise
        _call_schedule(resolution_signals=_BASE_SIGNALS)


def test_schedule_passes_resolution_source(monkeypatch):
    """resolution_source is forwarded verbatim to the task."""
    delay_mock = MagicMock()
    with patch(
        "celery_app.tasks.personal_food.embed_and_save_personal_food.delay",
        delay_mock,
    ):
        _call_schedule(resolution_signals=_BASE_SIGNALS, resolution_source="name_off")

    kwargs = delay_mock.call_args.kwargs
    assert kwargs["resolution_source"] == "name_off"


# ---------------------------------------------------------------------------
# embed_and_save_personal_food task (called directly / eagerly)
# ---------------------------------------------------------------------------


def _make_embed_mock(vector=None) -> AsyncMock:
    """AsyncMock for OpenAIService.embed_text that returns a tiny fake vector."""
    return AsyncMock(return_value=vector or [0.1, 0.2, 0.3])


def _run_task(
    task_db,
    *,
    user_id: int,
    canonical_name: str,
    meal_id: Optional[int] = None,
    resolution_source: Optional[str] = None,
    barcode: Optional[str] = None,
    per_100g_calories: Optional[float] = None,
    per_100g_proteins: Optional[float] = None,
    per_100g_fats: Optional[float] = None,
    per_100g_carbs: Optional[float] = None,
    embed_mock: Optional[AsyncMock] = None,
) -> int:
    """Run the Celery task eagerly (in-process, no broker needed).

    Uses task.apply() — Celery's built-in eager runner that runs the task body
    synchronously in the current process.  ``task_db`` is the sessionmaker
    fixture patched onto the task module's SessionLocal.
    Returns personal_food_id.
    """
    from celery_app.tasks.personal_food import embed_and_save_personal_food

    embed_m = embed_mock or _make_embed_mock()
    mock_svc = MagicMock()
    mock_svc.embed_text = embed_m

    with (
        patch(
            "celery_app.tasks.personal_food.SessionLocal",
            task_db,
        ),
        patch(
            "celery_app.tasks.personal_food.get_openai_service",
            return_value=mock_svc,
        ),
    ):
        result = embed_and_save_personal_food.apply(
            kwargs={
                "user_id": user_id,
                "canonical_name": canonical_name,
                "meal_id": meal_id,
                "resolution_source": resolution_source,
                "barcode": barcode,
                "per_100g_calories": per_100g_calories,
                "per_100g_proteins": per_100g_proteins,
                "per_100g_fats": per_100g_fats,
                "per_100g_carbs": per_100g_carbs,
            }
        )
    # .get() returns the return value or re-raises the task exception
    return result.get(propagate=True)


def test_task_creates_personal_food_row(task_db):
    """Task inserts a PersonalFood row on first call."""
    Session = task_db
    with Session() as db:
        user = _make_user(db, telegram_id=88_001)
        user_id = user.id

    personal_food_id = _run_task(
        task_db,
        user_id=user_id,
        canonical_name="Куриная грудка",
        per_100g_calories=165.0,
        per_100g_proteins=31.0,
        per_100g_fats=3.6,
        per_100g_carbs=0.0,
    )

    with Session() as db:
        pf = crud_personal_food.get_by_barcode(db, barcode="nope", user_id=user_id)
        # Row should exist by ID
        from sqlalchemy import select
        from app.models.personal_food import PersonalFood

        row = db.execute(
            select(PersonalFood).where(PersonalFood.id == personal_food_id)
        ).scalar_one_or_none()

    assert row is not None
    assert row.canonical_name == "Куриная грудка"
    assert float(row.per_100g_calories) == pytest.approx(165.0)
    assert row.times_used == 1


def test_task_creates_embedding_row(task_db):
    """Task inserts a PersonalFoodEmbedding row for the canonical name."""
    Session = task_db
    with Session() as db:
        user = _make_user(db, telegram_id=88_002)
        user_id = user.id

    personal_food_id = _run_task(
        task_db,
        user_id=user_id,
        canonical_name="Яблоко",
        embed_mock=_make_embed_mock([0.5, 0.6, 0.7]),
    )

    with Session() as db:
        embeddings = crud_personal_food.get_embeddings_for_food(
            db, personal_food_id=personal_food_id
        )

    assert len(embeddings) == 1
    assert embeddings[0].text_embedded == "Яблоко"


def test_task_embed_text_called_once_on_first_run(task_db):
    """embed_text is called exactly once for a new food."""
    Session = task_db
    with Session() as db:
        user = _make_user(db, telegram_id=88_003)
        user_id = user.id

    embed_mock = _make_embed_mock()
    _run_task(task_db, user_id=user_id, canonical_name="Банан", embed_mock=embed_mock)

    embed_mock.assert_awaited_once_with("Банан")


def test_task_idempotent_skips_embed_on_second_call(task_db):
    """On re-run with same canonical_name, embed_text is NOT called again."""
    Session = task_db
    with Session() as db:
        user = _make_user(db, telegram_id=88_004)
        user_id = user.id

    embed_mock = _make_embed_mock()

    # First call: creates food + embedding
    _run_task(task_db, user_id=user_id, canonical_name="Гречка", embed_mock=embed_mock)
    # Second call: upserts food (increments times_used) but skips embed
    _run_task(task_db, user_id=user_id, canonical_name="Гречка", embed_mock=embed_mock)

    # embed_text called only on the first run
    assert embed_mock.await_count == 1


def test_task_idempotent_increments_times_used(task_db):
    """Second call for same food increments times_used from 1 → 2."""
    Session = task_db
    with Session() as db:
        user = _make_user(db, telegram_id=88_005)
        user_id = user.id

    personal_food_id = _run_task(
        task_db, user_id=user_id, canonical_name="Творог 5%"
    )
    _run_task(task_db, user_id=user_id, canonical_name="Творог 5%")

    with Session() as db:
        from sqlalchemy import select
        from app.models.personal_food import PersonalFood

        row = db.execute(
            select(PersonalFood).where(PersonalFood.id == personal_food_id)
        ).scalar_one()

    assert row.times_used == 2


def test_task_persists_barcode(task_db):
    """barcode argument is stored on the PersonalFood row."""
    Session = task_db
    with Session() as db:
        user = _make_user(db, telegram_id=88_006)
        user_id = user.id

    personal_food_id = _run_task(
        task_db,
        user_id=user_id,
        canonical_name="Молоко 3.2%",
        barcode="4600000111222",
    )

    with Session() as db:
        from sqlalchemy import select
        from app.models.personal_food import PersonalFood

        row = db.execute(
            select(PersonalFood).where(PersonalFood.id == personal_food_id)
        ).scalar_one()

    assert row.barcode == "4600000111222"


def test_task_existing_embedding_not_duplicated(task_db):
    """If an embedding for canonical_name already exists, a second embed is NOT added."""
    Session = task_db
    with Session() as db:
        user = _make_user(db, telegram_id=88_007)
        user_id = user.id

    # First run creates the embedding
    personal_food_id = _run_task(
        task_db, user_id=user_id, canonical_name="Кефир 1%"
    )
    # Second run should NOT add a second embedding row
    _run_task(task_db, user_id=user_id, canonical_name="Кефир 1%")

    with Session() as db:
        embeddings = crud_personal_food.get_embeddings_for_food(
            db, personal_food_id=personal_food_id
        )

    assert len(embeddings) == 1


def test_task_persists_resolution_source(task_db):
    """resolution_source provenance is stored on the PersonalFood row."""
    Session = task_db
    with Session() as db:
        user = _make_user(db, telegram_id=88_008)
        user_id = user.id

    personal_food_id = _run_task(
        task_db,
        user_id=user_id,
        canonical_name="Рис",
        resolution_source="barcode_off",
    )

    with Session() as db:
        from sqlalchemy import select
        from app.models.personal_food import PersonalFood

        row = db.execute(
            select(PersonalFood).where(PersonalFood.id == personal_food_id)
        ).scalar_one()

    assert row.resolution_source == "barcode_off"


def test_task_returns_personal_food_id(task_db):
    """Task returns the integer personal_food_id on success."""
    Session = task_db
    with Session() as db:
        user = _make_user(db, telegram_id=88_009)
        user_id = user.id

    result = _run_task(task_db, user_id=user_id, canonical_name="Овсянка")
    assert isinstance(result, int)
    assert result > 0


# ---------------------------------------------------------------------------
# B6: pipeline-level confirm → personal_food DB write round-trip
# ---------------------------------------------------------------------------


def test_confirm_to_db_roundtrip(task_db):
    """End-to-end: _schedule_personal_food_save dispatches → task runs → row in DB.

    B4 QA note: "add pipeline-level integration test for confirm->personal_food
    DB write round-trip."  This combines the dispatch (telegram.py) and the
    task execution (Celery) in a single test so we confirm the kwargs wiring
    between the two layers is correct.
    """
    from app.services.telegram import _schedule_personal_food_save
    from celery_app.tasks.personal_food import embed_and_save_personal_food
    from app.models.personal_food import PersonalFood
    from sqlalchemy import select

    Session = task_db
    with Session() as db:
        user = _make_user(db, telegram_id=77_001)
        user_id = user.id

    # Step 1: call _schedule_personal_food_save and capture kwargs that would
    # be forwarded to the Celery task via .delay().
    captured_kwargs: dict = {}

    def _capture_delay(**kwargs):
        captured_kwargs.update(kwargs)

    embed_mock = _make_embed_mock([0.3, 0.4, 0.5])
    mock_svc = MagicMock()
    mock_svc.embed_text = embed_mock

    with patch(
        "celery_app.tasks.personal_food.embed_and_save_personal_food.delay",
        side_effect=_capture_delay,
    ):
        _schedule_personal_food_save(
            user_id=user_id,
            meal_id=None,
            nutrition={
                "foods": ["Куриная грудка"],
                "calories": 165.0,
                "protein": 31.0,
                "fats": 3.6,
                "carbs": 0.0,
                "portion": "100г",
            },
            resolution_signals={"portion_grams": 100.0, "barcode_raw": "4600000999001"},
            resolution_source="barcode_off",
        )

    assert captured_kwargs.get("canonical_name"), "dispatch must have been called"
    assert captured_kwargs["user_id"] == user_id
    assert captured_kwargs["barcode"] == "4600000999001"

    # Step 2: actually execute the task with the captured kwargs (eager mode).
    with (
        patch("celery_app.tasks.personal_food.SessionLocal", task_db),
        patch("celery_app.tasks.personal_food.get_openai_service", return_value=mock_svc),
    ):
        result = embed_and_save_personal_food.apply(kwargs=captured_kwargs)
        food_id = result.get(propagate=True)

    # Step 3: verify the DB row is there with the correct data.
    with Session() as db:
        row = db.execute(
            select(PersonalFood).where(PersonalFood.id == food_id)
        ).scalar_one()

    assert row.canonical_name == "Куриная грудка"
    assert row.barcode == "4600000999001"
    assert float(row.per_100g_calories) == pytest.approx(165.0)
    assert row.times_used == 1
    # Embedding was created
    embeddings = crud_personal_food.get_embeddings_for_food(
        db, personal_food_id=food_id
    )
    assert len(embeddings) == 1
    assert embeddings[0].text_embedded == "Куриная грудка"


def test_multiple_confirms_idempotent_roundtrip(task_db):
    """Multiple confirms of the same food: times_used grows; embedding stays unique.

    B4 QA note: "idempotency over multiple confirms."  Simulates a user
    photographing and confirming the same food three times — the Celery task
    must be idempotent on (user_id, lower(canonical_name)).
    """
    from app.models.personal_food import PersonalFood
    from sqlalchemy import select

    Session = task_db
    with Session() as db:
        user = _make_user(db, telegram_id=77_002)
        user_id = user.id

    embed_mock = _make_embed_mock()

    # Run the task three times (simulating three separate confirms).
    food_id: Optional[int] = None
    for _ in range(3):
        fid = _run_task(
            task_db,
            user_id=user_id,
            canonical_name="Творог 5%",
            per_100g_calories=121.0,
            per_100g_proteins=17.0,
            per_100g_fats=5.0,
            per_100g_carbs=2.8,
            embed_mock=embed_mock,
        )
        if food_id is None:
            food_id = fid
        # All runs must return the same food row id (idempotent dedup key)
        assert fid == food_id

    with Session() as db:
        row = db.execute(
            select(PersonalFood).where(PersonalFood.id == food_id)
        ).scalar_one()

    # times_used reflects every confirm
    assert row.times_used == 3

    # Embedding must NOT be duplicated — only created once
    with Session() as db:
        embeddings = crud_personal_food.get_embeddings_for_food(
            db, personal_food_id=food_id
        )
    assert len(embeddings) == 1

    # embed_text must have been called only once (on the first run)
    assert embed_mock.await_count == 1
