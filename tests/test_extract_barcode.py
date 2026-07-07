"""Unit tests for OpenAIService.extract_barcode_from_image (A3).

Vision-reads-digits approach: the OpenAI vision model reads the barcode
digits off the image; no pyzbar / libzbar dependency required.

All tests mock chat.completions.create — no network.
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import openai
import pytest

from app.services.openai_service import (
    ModelUnavailableError,
    OpenAIService,
    _has_valid_gs1_check_digit,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _service(raw_json: str):
    """Return an OpenAIService with create mocked to return *raw_json* as content."""
    svc = OpenAIService()
    fake_response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=raw_json))]
    )
    svc.client.chat.completions.create = AsyncMock(return_value=fake_response)
    return svc


def _call(svc: OpenAIService, url: str = "https://example.test/product.jpg"):
    return asyncio.run(svc.extract_barcode_from_image(url))


def _create_kwargs(svc: OpenAIService):
    return svc.client.chat.completions.create.call_args.kwargs


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


def test_returns_ean13():
    svc = _service(json.dumps({"barcode": "4607086060537"}))
    result = _call(svc)
    assert result == "4607086060537"


def test_returns_ean8():
    svc = _service(json.dumps({"barcode": "73513537"}))
    result = _call(svc)
    assert result == "73513537"


def test_returns_upc_a():
    svc = _service(json.dumps({"barcode": "012345678905"}))
    result = _call(svc)
    assert result == "012345678905"


def test_strips_spaces_from_model_value():
    """Model might include spaces between digit groups — normalise them away."""
    svc = _service(json.dumps({"barcode": "460 7086 060537"}))
    result = _call(svc)
    assert result == "4607086060537"


def test_strips_dashes_from_model_value():
    """Model might include dashes — normalise them away."""
    svc = _service(json.dumps({"barcode": "4607086-060537"}))
    result = _call(svc)
    assert result == "4607086060537"


# ---------------------------------------------------------------------------
# No-barcode paths → None
# ---------------------------------------------------------------------------


def test_returns_none_when_barcode_is_null():
    svc = _service(json.dumps({"barcode": None}))
    result = _call(svc)
    assert result is None


def test_returns_none_when_barcode_is_empty_string():
    svc = _service(json.dumps({"barcode": ""}))
    result = _call(svc)
    assert result is None


def test_returns_none_when_barcode_key_missing():
    svc = _service(json.dumps({"note": "no barcode visible"}))
    result = _call(svc)
    assert result is None


# ---------------------------------------------------------------------------
# Validation guards → None
# ---------------------------------------------------------------------------


def test_returns_none_for_non_digit_characters(caplog):
    """If model returns letters it should not guess — reject."""
    svc = _service(json.dumps({"barcode": "46070860X0537"}))
    with caplog.at_level("WARNING"):
        result = _call(svc)
    assert result is None
    assert "invalid barcode value" in caplog.text


def test_returns_none_for_too_short_value(caplog):
    """5 digits is below EAN-8 minimum; likely a partial read."""
    svc = _service(json.dumps({"barcode": "12345"}))
    with caplog.at_level("WARNING"):
        result = _call(svc)
    assert result is None
    assert "invalid barcode value" in caplog.text


def test_returns_none_for_too_long_value(caplog):
    """19 digits exceeds ITF-14 maximum; implausible barcode."""
    svc = _service(json.dumps({"barcode": "1234567890123456789"}))
    with caplog.at_level("WARNING"):
        result = _call(svc)
    assert result is None
    assert "invalid barcode value" in caplog.text


# ---------------------------------------------------------------------------
# Malformed model response → None
# ---------------------------------------------------------------------------


def test_returns_none_on_json_parse_failure(caplog):
    svc = _service("not valid json at all")
    with caplog.at_level("WARNING"):
        result = _call(svc)
    assert result is None
    assert "failed to parse response" in caplog.text


def test_returns_none_on_empty_response(caplog):
    svc = _service("")
    with caplog.at_level("WARNING"):
        result = _call(svc)
    assert result is None
    assert "failed to parse response" in caplog.text


# ---------------------------------------------------------------------------
# Request hygiene
# ---------------------------------------------------------------------------


def test_sends_image_in_nested_image_url_format():
    url = "https://example.test/product.jpg"
    svc = _service(json.dumps({"barcode": "4607086060537"}))
    _call(svc, url=url)

    user_content = _create_kwargs(svc)["messages"][1]["content"]
    image_parts = [p for p in user_content if p.get("type") == "image_url"]
    assert image_parts == [{"type": "image_url", "image_url": {"url": url}}]


def test_sends_text_instruction_alongside_image():
    svc = _service(json.dumps({"barcode": "4607086060537"}))
    _call(svc)

    user_content = _create_kwargs(svc)["messages"][1]["content"]
    text_parts = [p for p in user_content if p.get("type") == "text"]
    assert text_parts, "user turn must carry a text instruction"


def test_uses_json_object_response_format():
    svc = _service(json.dumps({"barcode": "4607086060537"}))
    _call(svc)

    kwargs = _create_kwargs(svc)
    assert kwargs["response_format"] == {"type": "json_object"}


def test_uses_small_max_tokens():
    """64-token cap keeps cost low for the barcode extraction call."""
    svc = _service(json.dumps({"barcode": "4607086060537"}))
    _call(svc)

    kwargs = _create_kwargs(svc)
    assert kwargs["max_tokens"] == 64


def test_uses_configured_model():
    from app.core.config import settings

    svc = _service(json.dumps({"barcode": "4607086060537"}))
    _call(svc)

    assert _create_kwargs(svc)["model"] == settings.OPENAI_MODEL


# ---------------------------------------------------------------------------
# Error propagation
# ---------------------------------------------------------------------------


def test_model_unavailable_error_propagates():
    """ModelUnavailableError from _create must bubble up unchanged."""
    svc = OpenAIService()
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    exc = openai.NotFoundError(
        "The model `gpt-x` does not exist or you do not have access to it.",
        response=httpx.Response(404, request=request),
        body=None,
    )
    svc.client.chat.completions.create = AsyncMock(side_effect=exc)

    with pytest.raises(ModelUnavailableError):
        _call(svc)


@pytest.mark.parametrize(
    "code, valid",
    [
        ("4006381333931", True),  # valid EAN-13
        ("4006381333932", False),  # EAN-13, wrong check digit (single misread)
        ("96385074", True),  # valid EAN-8
        ("96385070", False),  # EAN-8, wrong check digit
        ("036000291452", True),  # valid UPC-A (12)
        ("036000291453", False),  # UPC-A, wrong check digit
        ("123456", False),  # non-standard length — not validated here
        ("", False),
        ("abc", False),
    ],
)
def test_gs1_check_digit(code, valid):
    # review H1: reject most single-digit vision misreads before they resolve
    # to a *different* real product badged "точно".
    assert _has_valid_gs1_check_digit(code) is valid
