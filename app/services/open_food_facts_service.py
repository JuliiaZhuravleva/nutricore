"""Open Food Facts HTTP client service (A2).

Responsibilities
----------------
* Look up a product by barcode (EAN/UPC) via the free OFF API
  (no API key, no sign-up required).
* Normalize the raw response into per-100g КБЖУ (calories, protein, fat,
  carbohydrates), with a kJ → kcal fallback when only kJ data is present.
* Read the ``product_cache`` table before making any network call (cache-first).
* Write any successful API result back to the cache for future reuse.
* Return a typed ``OFFLookupResult`` on success, or ``None`` when the product
  is not found or any network/HTTP error occurs — callers fall back to the
  vision pipeline instead of receiving an exception.

Usage (from A4 pipeline)::

    from app.services.open_food_facts_service import OpenFoodFactsService

    svc = OpenFoodFactsService(db)
    result = await svc.lookup("4607195501226")
    if result is not None:
        print(result.calories_per_100g, result.from_cache)
"""

import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

import httpx
from sqlalchemy.orm import Session

from app.crud.crud_product_cache import crud_product_cache
from app.schemas.product_cache import ProductCacheCreate

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# OFF API constants
# ---------------------------------------------------------------------------

OFF_BASE_URL = "https://world.openfoodfacts.org"
OFF_PRODUCT_PATH = "/api/v2/product/{barcode}.json"

# Slim the payload: we only need these fields.
_OFF_FIELDS = "product_name,product_name_en,brands,code,nutriments,nutriscore_grade"

# Timeouts (seconds). OFF is generally fast but this is an external call.
_TIMEOUT_CONNECT = 5.0
_TIMEOUT_READ = 10.0

# Cap the OFF response we parse/cache. A slimmed product (fields=… below) is a
# few KB; 1 MB is a generous ceiling that stops a malformed/oversized body from
# bloating memory or the cached raw_data JSON column.
_MAX_RESPONSE_BYTES = 1_000_000

# Open Food Facts fair-use policy requires a descriptive User-Agent.
OFF_USER_AGENT = (
    "NutricoreBot/1.0 " "(meal-tracking personal tool; imnicecat@gmail.com)"
)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OFFLookupResult:
    """Structured result from a successful OFF product lookup.

    All macro values are **per 100g** as returned by OFF.  The pipeline layer
    (A4) is responsible for scaling to the eaten portion.

    ``from_cache=True`` means the result came from ``product_cache``; no HTTP
    call was made for this request.
    """

    barcode: str
    off_code: str
    product_name: Optional[str]
    brand: Optional[str]
    calories_per_100g: Optional[float]
    proteins_per_100g: Optional[float]
    fats_per_100g: Optional[float]
    carbohydrates_per_100g: Optional[float]
    raw_data: Optional[Dict[str, Any]]
    from_cache: bool

    @property
    def has_macros(self) -> bool:
        """True if OFF actually returned at least one usable nutrition value.

        An OFF product can exist with an *empty* ``nutriments`` block (common
        for regional / store-brand items). Such a result must NOT be surfaced
        as a high-confidence КБЖУ — coercing the missing values to 0 would show
        ``0 ккал / 0 БЖУ`` badged "по штрих-коду (точно)", i.e. a bogus zero
        indistinguishable from a genuinely verified one. Callers treat
        ``has_macros=False`` as a miss and fall through to the vision estimate.
        """
        return any(
            v is not None
            for v in (
                self.calories_per_100g,
                self.proteins_per_100g,
                self.fats_per_100g,
                self.carbohydrates_per_100g,
            )
        )


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class OpenFoodFactsService:
    """Cache-first client for the Open Food Facts product API.

    Parameters
    ----------
    db:
        SQLAlchemy session for cache reads/writes.  The **caller** owns the
        session lifetime; this service does not open or close it.
    """

    def __init__(self, db: Session) -> None:
        self._db = db

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def lookup(self, barcode: str) -> Optional[OFFLookupResult]:
        """Return КБЖУ for *barcode*, or ``None`` if not found / API error.

        Strategy
        --------
        1. Check ``product_cache`` (cache hit → immediate return, no HTTP).
        2. Call the OFF API on a cache miss.
        3. Normalise the OFF response into a typed result.
        4. Persist the result to the cache so the next scan hits step 1.
        5. Return ``None`` on "product not found" or any network/parse error
           so the caller can fall back to the vision pipeline seamlessly.
        """
        barcode = barcode.strip()

        # 0. Defense-in-depth: only ASCII digits of a plausible length ever reach
        # the OFF URL path / cache key. Callers validate upstream, but this guard
        # keeps a future caller (or a malformed cache key) from injecting into the
        # request path — the value is interpolated into OFF_PRODUCT_PATH below.
        if not (barcode.isascii() and barcode.isdigit() and 6 <= len(barcode) <= 18):
            logger.warning("OFF lookup rejected implausible barcode %r", barcode)
            return None

        # 1. Cache read. A DB error here is an INFRA failure, not a "product not
        # found" — log it distinctly (with a stack trace) and continue to the
        # API rather than silently degrading the scan to a vision estimate.
        try:
            cached = crud_product_cache.get_by_barcode(self._db, barcode)
        except Exception as exc:
            logger.warning(
                "product_cache read failed for barcode %s (treating as cache "
                "miss, continuing to OFF API): %s",
                barcode,
                exc,
                exc_info=True,
            )
            cached = None
        if cached is not None:
            logger.debug("OFF cache hit for barcode %s", barcode)
            cached_result = OFFLookupResult(
                barcode=cached.barcode,
                off_code=cached.off_code or cached.barcode,
                product_name=cached.product_name,
                brand=cached.brand,
                calories_per_100g=cached.calories_per_100g,
                proteins_per_100g=cached.proteins_per_100g,
                fats_per_100g=cached.fats_per_100g,
                carbohydrates_per_100g=cached.carbohydrates_per_100g,
                raw_data=cached.raw_data,
                from_cache=True,
            )
            # A cached row with no usable macros is a "found but no nutrition"
            # entry — treat as a miss so the caller falls back to vision.
            return cached_result if cached_result.has_macros else None

        # 2. OFF API call
        product_data = await self._fetch_from_off(barcode)
        if product_data is None:
            return None

        # 3. Normalise
        result = self._normalise(barcode, product_data)
        if result is None:
            return None

        # 4. OFF product exists but carries no usable nutrition → do NOT cache a
        # zero-nutrition row and do NOT surface it as КБЖУ; report a miss so the
        # pipeline falls through to the vision estimate (honest low confidence).
        if not result.has_macros:
            logger.info(
                "OFF product %s has no usable nutriments — treating as miss "
                "(fall through to vision)",
                barcode,
            )
            return None

        # 5. Cache write (best-effort)
        self._write_cache(result)

        return result

    # ------------------------------------------------------------------
    # Private — HTTP
    # ------------------------------------------------------------------

    async def _fetch_from_off(self, barcode: str) -> Optional[Dict[str, Any]]:
        """Call the OFF API and return the raw ``product`` dict, or ``None``."""
        url = OFF_BASE_URL + OFF_PRODUCT_PATH.format(barcode=barcode)
        headers = {"User-Agent": OFF_USER_AGENT}
        timeout = httpx.Timeout(
            connect=_TIMEOUT_CONNECT,
            read=_TIMEOUT_READ,
            write=5.0,
            pool=5.0,
        )

        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get(
                    url, headers=headers, params={"fields": _OFF_FIELDS}
                )
        except httpx.TimeoutException as exc:
            logger.warning("OFF API timeout for barcode %s: %s", barcode, exc)
            return None
        except httpx.RequestError as exc:
            logger.warning("OFF API request error for barcode %s: %s", barcode, exc)
            return None

        latency_ms = int((time.perf_counter() - started) * 1000)

        if response.status_code != 200:
            logger.warning(
                "OFF API returned HTTP %s for barcode %s (%.0f ms)",
                response.status_code,
                barcode,
                latency_ms,
            )
            return None

        if len(response.content) > _MAX_RESPONSE_BYTES:
            logger.warning(
                "OFF API response for barcode %s is %d bytes (> %d cap) — ignoring",
                barcode,
                len(response.content),
                _MAX_RESPONSE_BYTES,
            )
            return None

        try:
            data: Dict[str, Any] = response.json()
        except Exception as exc:
            logger.warning("OFF API returned non-JSON for barcode %s: %s", barcode, exc)
            return None

        # OFF status field: 1 = found, 0 = not found.
        status = data.get("status")
        if status != 1:
            logger.debug(
                "OFF: product not found for barcode %s "
                "(status=%s, verbose=%r, %.0f ms)",
                barcode,
                status,
                data.get("status_verbose", ""),
                latency_ms,
            )
            return None

        product = data.get("product")
        if not product:
            logger.warning(
                "OFF returned status=1 but no 'product' field for barcode %s",
                barcode,
            )
            return None

        logger.debug(
            "OFF: found product for barcode %s in %.0f ms", barcode, latency_ms
        )
        return product

    # ------------------------------------------------------------------
    # Private — normalization
    # ------------------------------------------------------------------

    def _normalise(
        self, barcode: str, product: Dict[str, Any]
    ) -> Optional[OFFLookupResult]:
        """Extract and normalise per-100g КБЖУ from an OFF product dict."""
        nutriments: Dict[str, Any] = product.get("nutriments") or {}

        # OFF stores kcal as "energy-kcal_100g"; kJ as "energy-kj_100g" /
        # "energy_100g".  Prefer kcal directly, convert kJ as a fallback.
        calories = self._float_or_none(nutriments.get("energy-kcal_100g"))
        if calories is None:
            kj = self._float_or_none(
                nutriments.get("energy-kj_100g") or nutriments.get("energy_100g")
            )
            if kj is not None:
                calories = round(kj / 4.184, 1)

        proteins = self._float_or_none(nutriments.get("proteins_100g"))
        fats = self._float_or_none(nutriments.get("fat_100g"))
        carbs = self._float_or_none(nutriments.get("carbohydrates_100g"))

        product_name: Optional[str] = (
            product.get("product_name") or product.get("product_name_en") or None
        )
        brand: Optional[str] = product.get("brands") or None
        off_code: str = str(product.get("code") or barcode)

        # raw_data: keep all OFF fields for future extensibility (Nutri-Score,
        # ingredients, allergens…).  We include nutriments explicitly so the
        # stored snapshot is complete even though we also parse it above.
        raw_data: Dict[str, Any] = {k: v for k, v in product.items()}

        return OFFLookupResult(
            barcode=barcode,
            off_code=off_code,
            product_name=product_name,
            brand=brand,
            calories_per_100g=calories,
            proteins_per_100g=proteins,
            fats_per_100g=fats,
            carbohydrates_per_100g=carbs,
            raw_data=raw_data,
            from_cache=False,
        )

    # ------------------------------------------------------------------
    # Private — cache write
    # ------------------------------------------------------------------

    def _write_cache(self, result: OFFLookupResult) -> None:
        """Persist a fresh OFF result to ``product_cache`` (best-effort).

        ``get_or_create`` is idempotent: a race between two concurrent lookups
        for the same barcode will not produce a duplicate row.
        """
        try:
            obj_in = ProductCacheCreate(
                barcode=result.barcode,
                off_code=result.off_code,
                product_name=result.product_name,
                brand=result.brand,
                calories_per_100g=result.calories_per_100g,
                proteins_per_100g=result.proteins_per_100g,
                fats_per_100g=result.fats_per_100g,
                carbohydrates_per_100g=result.carbohydrates_per_100g,
                raw_data=result.raw_data,
            )
            crud_product_cache.get_or_create(self._db, obj_in)
        except Exception as exc:  # pragma: no cover — best effort
            logger.warning(
                "Failed to cache OFF result for barcode %s: %s",
                result.barcode,
                exc,
                exc_info=True,
            )
            # Leave the session clean regardless of the caller's session
            # lifetime — a failed write otherwise strands a shared/long-lived
            # session in a PendingRollback state for its next operation.
            try:
                self._db.rollback()
            except Exception:  # pragma: no cover — defensive
                logger.debug("rollback after cache-write failure also failed")

    # ------------------------------------------------------------------
    # Static helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _float_or_none(value: Any) -> Optional[float]:
        """Coerce *value* to ``float``, or return ``None`` for missing /
        non-numeric entries."""
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
