"""Unit tests for OpenFoodFactsService (A2).

No real network calls: httpx.AsyncClient is mocked at the module level via
``unittest.mock.patch``.  The DB layer (product_cache) uses the standard
``db_session`` fixture backed by SQLite in-memory so we test the full
cache-read/write cycle without a real Postgres.

Coverage
--------
* Cache hit  → no HTTP call, OFFLookupResult.from_cache=True
* Cache miss, product found  → HTTP called, result returned, row written
* Cache miss, product not found (status=0)  → None
* Cache miss, product found on second call  → second call is a cache hit
* HTTP timeout  → None (graceful)
* HTTP request error  → None (graceful)
* HTTP non-200  → None (graceful)
* HTTP non-JSON response  → None (graceful)
* Nutrient normalisation: direct kcal key
* Nutrient normalisation: kJ → kcal conversion fallback
* Nutrient normalisation: empty nutriments dict
* Nutrient normalisation: non-numeric nutriment value (_float_or_none)
* User-Agent header is sent on every HTTP call
* Fields parameter is sent to reduce payload size
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.services.open_food_facts_service import (
    OFF_USER_AGENT,
    OFFLookupResult,
    OpenFoodFactsService,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_EAN = "4607195501226"


def _make_off_json(
    *,
    status: int = 1,
    barcode: str = _EAN,
    product_name: str = "Творог Простоквашино 5%",
    brand: str = "Простоквашино",
    kcal_100g: float = 121.0,
    proteins_100g: float = 17.0,
    fat_100g: float = 5.0,
    carbs_100g: float = 1.8,
    nutriscore: str = "a",
) -> dict:
    """Minimal OFF API response payload for a found product."""
    return {
        "status": status,
        "status_verbose": "product found" if status == 1 else "product not found",
        "product": {
            "code": barcode,
            "product_name": product_name,
            "brands": brand,
            "nutriscore_grade": nutriscore,
            "nutriments": {
                "energy-kcal_100g": kcal_100g,
                "proteins_100g": proteins_100g,
                "fat_100g": fat_100g,
                "carbohydrates_100g": carbs_100g,
            },
        },
    }


def _make_response(status_code: int = 200, json_data: dict | None = None) -> MagicMock:
    """Build a mock httpx.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json = MagicMock(return_value=json_data or {})
    return resp


def _patch_client(response: MagicMock):
    """Patch ``httpx.AsyncClient`` used inside the service module.

    The service does::

        async with httpx.AsyncClient(...) as client:
            response = await client.get(...)

    So we need an async context manager whose ``__aenter__`` returns a mock
    with a ``get`` coroutine.
    """
    mock_inner = AsyncMock()
    mock_inner.get = AsyncMock(return_value=response)

    mock_cm = AsyncMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_inner)
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    return (
        patch(
            "app.services.open_food_facts_service.httpx.AsyncClient",
            return_value=mock_cm,
        ),
        mock_inner,  # exposed so tests can inspect call args
    )


def _svc(db_session) -> OpenFoodFactsService:
    return OpenFoodFactsService(db_session)


# ---------------------------------------------------------------------------
# Cache hit
# ---------------------------------------------------------------------------


def test_cache_hit_returns_result_without_http(db_session):
    """A pre-cached product must not trigger an HTTP call."""
    from app.crud.crud_product_cache import crud_product_cache
    from app.schemas.product_cache import ProductCacheCreate

    crud_product_cache.create(
        db_session,
        ProductCacheCreate(
            barcode=_EAN,
            off_code=_EAN,
            product_name="Творог",
            brand="Простоквашино",
            calories_per_100g=121.0,
            proteins_per_100g=17.0,
            fats_per_100g=5.0,
            carbohydrates_per_100g=1.8,
        ),
    )

    svc = _svc(db_session)

    with patch(
        "app.services.open_food_facts_service.httpx.AsyncClient"
    ) as mock_client_cls:
        result = asyncio.run(svc.lookup(_EAN))
        mock_client_cls.assert_not_called()

    assert result is not None
    assert result.from_cache is True
    assert result.barcode == _EAN
    assert result.calories_per_100g == 121.0
    assert result.proteins_per_100g == 17.0


# ---------------------------------------------------------------------------
# Cache miss → product found
# ---------------------------------------------------------------------------


def test_cache_miss_found_returns_result(db_session):
    """On a cache miss the service hits OFF, returns a typed result."""
    resp = _make_response(200, _make_off_json())
    patch_ctx, mock_inner = _patch_client(resp)

    with patch_ctx:
        result = asyncio.run(_svc(db_session).lookup(_EAN))

    assert result is not None
    assert isinstance(result, OFFLookupResult)
    assert result.barcode == _EAN
    assert result.off_code == _EAN
    assert result.product_name == "Творог Простоквашино 5%"
    assert result.brand == "Простоквашино"
    assert result.calories_per_100g == 121.0
    assert result.proteins_per_100g == 17.0
    assert result.fats_per_100g == 5.0
    assert result.carbohydrates_per_100g == 1.8
    assert result.from_cache is False


def test_cache_miss_found_writes_to_cache(db_session):
    """After a successful API lookup the row must be persisted to product_cache."""
    from app.crud.crud_product_cache import crud_product_cache

    resp = _make_response(200, _make_off_json())
    patch_ctx, _ = _patch_client(resp)

    with patch_ctx:
        asyncio.run(_svc(db_session).lookup(_EAN))

    cached = crud_product_cache.get_by_barcode(db_session, _EAN)
    assert cached is not None
    assert cached.calories_per_100g == 121.0
    assert cached.product_name == "Творог Простоквашино 5%"


def test_second_lookup_is_cache_hit(db_session):
    """Second call for the same barcode must not make a second HTTP request."""
    resp = _make_response(200, _make_off_json())
    patch_ctx, mock_inner = _patch_client(resp)

    with patch_ctx:
        asyncio.run(_svc(db_session).lookup(_EAN))
        # Replace the mock with one that should NOT be called
        mock_inner.get.reset_mock()
        result2 = asyncio.run(_svc(db_session).lookup(_EAN))

    assert result2 is not None
    assert result2.from_cache is True
    mock_inner.get.assert_not_called()


# ---------------------------------------------------------------------------
# Cache miss → product not found
# ---------------------------------------------------------------------------


def test_product_not_found_returns_none(db_session):
    """OFF status=0 (not found) must produce ``None``, not an exception."""
    resp = _make_response(200, _make_off_json(status=0))
    resp.json.return_value.pop("product", None)
    resp.json = MagicMock(
        return_value={"status": 0, "status_verbose": "product not found"}
    )
    patch_ctx, _ = _patch_client(resp)

    with patch_ctx:
        result = asyncio.run(_svc(db_session).lookup("0000000000000"))

    assert result is None


# ---------------------------------------------------------------------------
# HTTP error paths → graceful None
# ---------------------------------------------------------------------------


def test_http_timeout_returns_none(db_session):
    mock_inner = AsyncMock()
    mock_inner.get = AsyncMock(
        side_effect=httpx.TimeoutException("timed out", request=MagicMock())
    )
    mock_cm = AsyncMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_inner)
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    with patch(
        "app.services.open_food_facts_service.httpx.AsyncClient",
        return_value=mock_cm,
    ):
        result = asyncio.run(_svc(db_session).lookup(_EAN))

    assert result is None


def test_http_connect_error_returns_none(db_session):
    mock_inner = AsyncMock()
    mock_inner.get = AsyncMock(
        side_effect=httpx.ConnectError("connection refused", request=MagicMock())
    )
    mock_cm = AsyncMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_inner)
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    with patch(
        "app.services.open_food_facts_service.httpx.AsyncClient",
        return_value=mock_cm,
    ):
        result = asyncio.run(_svc(db_session).lookup(_EAN))

    assert result is None


def test_http_non_200_returns_none(db_session):
    resp = _make_response(503, {})
    patch_ctx, _ = _patch_client(resp)

    with patch_ctx:
        result = asyncio.run(_svc(db_session).lookup(_EAN))

    assert result is None


def test_http_non_json_returns_none(db_session):
    resp = _make_response(200)
    resp.json = MagicMock(side_effect=ValueError("not JSON"))
    patch_ctx, _ = _patch_client(resp)

    with patch_ctx:
        result = asyncio.run(_svc(db_session).lookup(_EAN))

    assert result is None


# ---------------------------------------------------------------------------
# Nutrient normalisation
# ---------------------------------------------------------------------------


def test_normalise_direct_kcal_key(db_session):
    """The primary path: energy-kcal_100g is present."""
    data = _make_off_json(kcal_100g=200.0)
    resp = _make_response(200, data)
    patch_ctx, _ = _patch_client(resp)

    with patch_ctx:
        result = asyncio.run(_svc(db_session).lookup(_EAN))

    assert result is not None
    assert result.calories_per_100g == 200.0


def test_normalise_kj_fallback_conversion(db_session):
    """When only kJ data is present it must be converted to kcal."""
    off_json = {
        "status": 1,
        "product": {
            "code": _EAN,
            "product_name": "Energy bar",
            "brands": "Test",
            "nutriments": {
                # No energy-kcal_100g — only kJ
                "energy-kj_100g": 418.4,  # = 100 kcal
                "proteins_100g": 5.0,
                "fat_100g": 2.0,
                "carbohydrates_100g": 10.0,
            },
        },
    }
    resp = _make_response(200, off_json)
    patch_ctx, _ = _patch_client(resp)

    with patch_ctx:
        result = asyncio.run(_svc(db_session).lookup(_EAN))

    assert result is not None
    assert result.calories_per_100g == pytest.approx(100.0, abs=0.5)


def test_empty_nutriments_treated_as_miss(db_session):
    """A product that EXISTS in OFF but carries no usable macros (empty
    nutriments — common for regional/store-brand items) must be treated as a
    miss (lookup → None) so the pipeline falls through to the vision estimate.
    It must never surface as a high-confidence 0-ккал/0-БЖУ result badged
    'точно' (review finding C1)."""
    off_json = {
        "status": 1,
        "product": {
            "code": _EAN,
            "product_name": "Mystery product",
            "brands": "Unknown",
            "nutriments": {},
        },
    }
    resp = _make_response(200, off_json)
    patch_ctx, _ = _patch_client(resp)

    with patch_ctx:
        result = asyncio.run(_svc(db_session).lookup(_EAN))

    assert result is None


def test_normalise_non_numeric_nutriment_returns_none_not_error(db_session):
    """A non-numeric nutriment value (OFF data quality issue) must be coerced
    to None, not raise a ValueError."""
    off_json = {
        "status": 1,
        "product": {
            "code": _EAN,
            "product_name": "Weird product",
            "brands": "Brand",
            "nutriments": {
                "energy-kcal_100g": "n/a",  # bad string — should silently → None
                "proteins_100g": 10.0,
                "fat_100g": 3.0,
                "carbohydrates_100g": 15.0,
            },
        },
    }
    resp = _make_response(200, off_json)
    patch_ctx, _ = _patch_client(resp)

    with patch_ctx:
        result = asyncio.run(_svc(db_session).lookup(_EAN))

    assert result is not None
    assert result.calories_per_100g is None  # coerced, not raised
    assert result.proteins_per_100g == 10.0  # other fields unaffected


def test_normalise_product_name_en_fallback(db_session):
    """Falls back to product_name_en when product_name is absent."""
    off_json = {
        "status": 1,
        "product": {
            "code": _EAN,
            # product_name missing
            "product_name_en": "Cottage Cheese",
            "brands": "Brand",
            "nutriments": {"energy-kcal_100g": 100.0},
        },
    }
    resp = _make_response(200, off_json)
    patch_ctx, _ = _patch_client(resp)

    with patch_ctx:
        result = asyncio.run(_svc(db_session).lookup(_EAN))

    assert result is not None
    assert result.product_name == "Cottage Cheese"


# ---------------------------------------------------------------------------
# HTTP hygiene: User-Agent + fields param
# ---------------------------------------------------------------------------


def test_user_agent_header_is_sent(db_session):
    """The OFF fair-use policy requires a descriptive User-Agent header."""
    resp = _make_response(200, _make_off_json())
    patch_ctx, mock_inner = _patch_client(resp)

    with patch_ctx:
        asyncio.run(_svc(db_session).lookup(_EAN))

    _args, kwargs = mock_inner.get.call_args
    assert "headers" in kwargs
    assert kwargs["headers"]["User-Agent"] == OFF_USER_AGENT


def test_fields_param_is_sent(db_session):
    """The ``fields`` query param must be included to reduce payload size."""
    resp = _make_response(200, _make_off_json())
    patch_ctx, mock_inner = _patch_client(resp)

    with patch_ctx:
        asyncio.run(_svc(db_session).lookup(_EAN))

    _args, kwargs = mock_inner.get.call_args
    assert "params" in kwargs
    assert "fields" in kwargs["params"]


# ---------------------------------------------------------------------------
# _float_or_none (pure unit)
# ---------------------------------------------------------------------------


def test_float_or_none_with_int():
    assert OpenFoodFactsService._float_or_none(100) == 100.0


def test_float_or_none_with_float():
    assert OpenFoodFactsService._float_or_none(3.14) == 3.14


def test_float_or_none_with_numeric_string():
    assert OpenFoodFactsService._float_or_none("42.5") == 42.5


def test_float_or_none_with_none():
    assert OpenFoodFactsService._float_or_none(None) is None


def test_float_or_none_with_non_numeric_string():
    assert OpenFoodFactsService._float_or_none("n/a") is None


def test_float_or_none_with_empty_string():
    assert OpenFoodFactsService._float_or_none("") is None


def _mk_result(**macros):
    base = dict(
        barcode="4006381333931",
        off_code="4006381333931",
        product_name="X",
        brand=None,
        raw_data=None,
        from_cache=False,
        calories_per_100g=None,
        proteins_per_100g=None,
        fats_per_100g=None,
        carbohydrates_per_100g=None,
    )
    base.update(macros)
    return OFFLookupResult(**base)


def test_has_macros_all_none_is_false():
    # review C1: an OFF entry with no usable macros must report has_macros=False
    # so the caller treats it as a miss (falls through to vision).
    assert _mk_result().has_macros is False


def test_has_macros_any_present_is_true():
    assert _mk_result(calories_per_100g=52.0).has_macros is True
    assert _mk_result(proteins_per_100g=0.0).has_macros is True


# ---------------------------------------------------------------------------
# search_by_name (A8) — OFF full-text search
# ---------------------------------------------------------------------------


def _search_product(
    *,
    code: str = _EAN,
    product_name: str = "Чипсы Pringles Original",
    brand: str = "Pringles",
    nutriments: dict | None = None,
) -> dict:
    """One product entry as returned in the cgi/search.pl products list."""
    if nutriments is None:
        nutriments = {
            "energy-kcal_100g": 520.0,
            "proteins_100g": 5.5,
            "fat_100g": 31.0,
            "carbohydrates_100g": 53.0,
        }
    return {
        "code": code,
        "product_name": product_name,
        "brands": brand,
        "nutriments": nutriments,
    }


def _make_search_json(products: list) -> dict:
    return {"count": len(products), "page": 1, "page_size": 5, "products": products}


def test_search_by_name_returns_best_match(db_session):
    resp = _make_response(200, _make_search_json([_search_product()]))
    patch_ctx, _ = _patch_client(resp)

    with patch_ctx:
        result = asyncio.run(_svc(db_session).search_by_name("Pringles Original"))

    assert result is not None
    assert isinstance(result, OFFLookupResult)
    assert result.product_name == "Чипсы Pringles Original"
    assert result.calories_per_100g == 520.0
    assert result.from_cache is False


def test_search_by_name_skips_candidates_without_macros(db_session):
    """The first relevance-ranked hit with real nutrition wins; an earlier hit
    with an empty nutriments block is skipped (same rule as the barcode path)."""
    no_macros = _search_product(product_name="Empty regional item", nutriments={})
    good = _search_product(product_name="Real product")
    resp = _make_response(200, _make_search_json([no_macros, good]))
    patch_ctx, _ = _patch_client(resp)

    with patch_ctx:
        result = asyncio.run(_svc(db_session).search_by_name("query"))

    assert result is not None
    assert result.product_name == "Real product"


def test_search_by_name_all_candidates_no_macros_returns_none(db_session):
    empty = _search_product(nutriments={})
    resp = _make_response(200, _make_search_json([empty, empty]))
    patch_ctx, _ = _patch_client(resp)

    with patch_ctx:
        result = asyncio.run(_svc(db_session).search_by_name("query"))

    assert result is None


def test_search_by_name_empty_query_makes_no_http_call(db_session):
    with patch(
        "app.services.open_food_facts_service.httpx.AsyncClient"
    ) as mock_client_cls:
        result = asyncio.run(_svc(db_session).search_by_name("   "))
        mock_client_cls.assert_not_called()
    assert result is None


def test_search_by_name_no_products_returns_none(db_session):
    resp = _make_response(200, _make_search_json([]))
    patch_ctx, _ = _patch_client(resp)

    with patch_ctx:
        result = asyncio.run(_svc(db_session).search_by_name("nothing here"))

    assert result is None


def test_search_by_name_http_timeout_returns_none(db_session):
    mock_inner = AsyncMock()
    mock_inner.get = AsyncMock(
        side_effect=httpx.TimeoutException("timed out", request=MagicMock())
    )
    mock_cm = AsyncMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_inner)
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    with patch(
        "app.services.open_food_facts_service.httpx.AsyncClient",
        return_value=mock_cm,
    ):
        result = asyncio.run(_svc(db_session).search_by_name("query"))

    assert result is None


def test_search_by_name_does_not_write_cache(db_session):
    """Name-search results are fuzzy and must NOT be persisted to product_cache
    (only exact barcode lookups are authoritative)."""
    from app.crud.crud_product_cache import crud_product_cache

    resp = _make_response(200, _make_search_json([_search_product()]))
    patch_ctx, _ = _patch_client(resp)

    with patch_ctx:
        asyncio.run(_svc(db_session).search_by_name("Pringles Original"))

    assert crud_product_cache.get_by_barcode(db_session, _EAN) is None


def test_search_by_name_sends_user_agent_and_search_terms(db_session):
    resp = _make_response(200, _make_search_json([_search_product()]))
    patch_ctx, mock_inner = _patch_client(resp)

    with patch_ctx:
        asyncio.run(_svc(db_session).search_by_name("Pringles Original"))

    _args, kwargs = mock_inner.get.call_args
    assert kwargs["headers"]["User-Agent"] == OFF_USER_AGENT
    assert kwargs["params"]["search_terms"] == "Pringles Original"
    assert "fields" in kwargs["params"]


def test_search_by_name_non_object_body_returns_none(db_session):
    """A non-object JSON body (e.g. a bare array from a proxy) degrades to a
    miss, not an AttributeError — the shared _off_get_json shape guard."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json = MagicMock(return_value=[1, 2, 3])  # not a dict
    patch_ctx, _ = _patch_client(resp)

    with patch_ctx:
        result = asyncio.run(_svc(db_session).search_by_name("query"))

    assert result is None


def test_search_by_name_skips_non_dict_candidate(db_session):
    """A non-dict element in the products list is skipped, not fatal — a valid
    later candidate still wins."""
    good = _search_product(product_name="Real product")
    resp = _make_response(200, _make_search_json([123, good]))
    patch_ctx, _ = _patch_client(resp)

    with patch_ctx:
        result = asyncio.run(_svc(db_session).search_by_name("query"))

    assert result is not None
    assert result.product_name == "Real product"


def test_lookup_non_object_body_returns_none(db_session):
    """The barcode path shares the same shape guard: a non-object body → None."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json = MagicMock(return_value=[1, 2, 3])
    patch_ctx, _ = _patch_client(resp)

    with patch_ctx:
        result = asyncio.run(_svc(db_session).lookup(_EAN))

    assert result is None


@pytest.mark.parametrize(
    "bad",
    [
        "../../etc/passwd",  # path-injection-y
        "abc123",  # non-digit
        "12",  # too short
        "1" * 20,  # too long
        "１２３４５６７８",  # non-ASCII (fullwidth) digits — pass str.isdigit()
    ],
)
def test_lookup_rejects_implausible_barcode(db_session, bad):
    # Defense-in-depth (security review): an implausible/non-ASCII-digit value
    # never reaches the OFF URL path or the cache — rejected before any I/O.
    assert asyncio.run(_svc(db_session).lookup(bad)) is None
