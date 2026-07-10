"""PersonalFood + PersonalFoodEmbedding models (ADR-0003 / B1).

Two-table design (see ADR-0003 §3):
  personal_foods       — one canonical food record per (user, lower(name))
  personal_food_embeddings — one vector row per embedded text pointing to personal_food_id

The embedding column uses pgvector's Vector(1536) type when the package is
installed (production/Postgres).  When pgvector is not importable (e.g. a dev
env without the package), a plain UserDefinedType fallback renders the same
DDL string "vector(1536)" that SQLite accepts with TEXT affinity — so
Base.metadata.create_all(sqlite_engine) works for unit tests without real
pgvector.  find_similar (the only method that uses the <=> operator) is mocked
in all unit tests (B6 seam — ADR-0003 §4d).

Migration-locked: VECTOR(1536) matches OPENAI_EMBEDDING_DIMS default.
Do NOT change after first deploy without a re-embed + ALTER COLUMN migration.
"""

from __future__ import annotations

import datetime

import sqlalchemy as sa
from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    func,
)
from sqlalchemy.orm import relationship

from app.db.base_class import Base, BaseClass

# ---------------------------------------------------------------------------
# Vector type — pgvector when available, plain UserDefinedType fallback otherwise
# ---------------------------------------------------------------------------
try:
    from pgvector.sqlalchemy import Vector as _PgVector  # type: ignore[import]

    _VECTOR_1536 = _PgVector(1536)
except ImportError:  # pragma: no cover — only hit without pgvector installed

    class _FallbackVector(sa.types.UserDefinedType):  # type: ignore[misc]
        """SQLite-compatible placeholder used when pgvector package is absent.

        Renders the same DDL as the real pgvector type so SQLite's create_all
        succeeds (SQLite uses TEXT affinity for unknown column types).
        """

        cache_ok = True

        def __init__(self, dims: int) -> None:
            self.dims = dims

        def get_col_spec(self, **kw: object) -> str:  # type: ignore[override]
            return f"vector({self.dims})"

        def bind_processor(self, dialect):  # type: ignore[override]
            def process(value):
                if value is None:
                    return None
                if isinstance(value, (list, tuple)):
                    return "[" + ",".join(str(v) for v in value) + "]"
                return str(value)

            return process

        def result_processor(self, dialect, coltype):  # type: ignore[override]
            def process(value):
                return value  # pass through (string in SQLite tests)

            return process

    _VECTOR_1536 = _FallbackVector(1536)  # type: ignore[assignment]


UTC = datetime.timezone.utc


class PersonalFood(Base, BaseClass):
    """Canonical per-user food record built from confirmed meals.

    Dedup key: (user_id, lower(canonical_name)) — enforced by a unique expression
    index in the Alembic migration.  CRUDPersonalFood.upsert() maintains this
    invariant in application code so it works on both Postgres and SQLite tests.

    created_at / updated_at are NOT NULL + server_default (TD-006 lesson).
    """

    __tablename__ = "personal_foods"

    id = Column(Integer, primary_key=True, index=True)

    # Owner — mandatory user scope (ADR-0003 §3: mandatory user_id filter)
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Display name shown at the confirm step; the dedup anchor
    canonical_name = Column(Text, nullable=False)
    brand = Column(Text, nullable=True)

    # Per-100g macros (same unit convention as the rest of the pipeline)
    per_100g_calories = Column(Numeric(8, 2), nullable=True)
    per_100g_proteins = Column(Numeric(8, 2), nullable=True)
    per_100g_fats = Column(Numeric(8, 2), nullable=True)
    per_100g_carbs = Column(Numeric(8, 2), nullable=True)

    # Provenance: which meal + pipeline strategy confirmed this food
    meal_id = Column(
        Integer,
        ForeignKey("meals.id", ondelete="SET NULL"),
        nullable=True,
    )
    resolution_source = Column(Text, nullable=True)

    # Nullable barcode — enables the exact-barcode short-circuit in
    # SavedFoodRAGStrategy Phase A (ADR-0003 §4c / B3).
    # Partial index: personal_foods_user_barcode_idx (migration, WHERE barcode IS NOT NULL)
    barcode = Column(Text, nullable=True)

    # Usage tracking (for future quick-pick UX, B5)
    times_used = Column(Integer, nullable=False, default=1, server_default="1")
    last_used_at = Column(DateTime(timezone=True), nullable=True)

    # NOT NULL + server_default (TD-006 lesson)
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

    # Relationships
    embeddings = relationship(
        "PersonalFoodEmbedding",
        back_populates="personal_food",
        cascade="all, delete-orphan",
    )

    # Note: the expression unique index (user_id, lower(canonical_name)) and the
    # partial barcode index are defined in the Alembic migration only — they use
    # Postgres-specific DDL that SQLAlchemy cannot express as portable __table_args__.


class PersonalFoodEmbedding(Base, BaseClass):
    """One embedding row per text variant (canonical name or alias).

    All rows for the same food point to the same personal_food_id.
    The HNSW ANN index (vector_cosine_ops) lives in the migration only.

    When the pgvector package is not installed, embedding is stored as a
    serialised string in SQLite — only for unit tests; never exercised in prod.
    """

    __tablename__ = "personal_food_embeddings"

    id = Column(Integer, primary_key=True, index=True)
    personal_food_id = Column(
        Integer,
        ForeignKey("personal_foods.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # The exact text that was embedded (for debugging / replay)
    text_embedded = Column(Text, nullable=False)
    # Vector(1536) — migration-locked; see ADR-0003 §2
    embedding = Column(_VECTOR_1536, nullable=False)  # type: ignore[arg-type]

    # NOT NULL + server_default (TD-006 lesson)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        default=lambda: datetime.datetime.now(UTC),
    )

    # Relationship back to the canonical food record
    personal_food = relationship("PersonalFood", back_populates="embeddings")
