"""Model refs and per-model call parameters (ADR-0004). Pure functions, no I/O."""

import pytest

from app.services.model_registry import (
    OPENROUTER_PROVIDER_PREFS,
    REASONING_TOKEN_FLOOR,
    REGISTRY,
    ModelRef,
    build_chat_params,
    parse_model_ref,
    spec_for,
)


@pytest.mark.parametrize(
    "ref, expected",
    [
        ("gpt-4o-mini", ModelRef("openai", "gpt-4o-mini")),
        ("openai:gpt-6-luna", ModelRef("openai", "gpt-6-luna")),
        (
            "openrouter:google/gemini-3.8-flash",
            ModelRef("openrouter", "google/gemini-3.8-flash"),
        ),
        # OpenRouter variant suffixes keep their colon.
        ("openrouter:openai/gpt-6-luna:free", ModelRef("openrouter", "openai/gpt-6-luna:free")),
        # A fine-tune id has colons but no route prefix: still OpenAI.
        ("ft:gpt-4o-mini:org:x:1", ModelRef("openai", "ft:gpt-4o-mini:org:x:1")),
        ("  gpt-4o  ", ModelRef("openai", "gpt-4o")),
    ],
)
def test_parse_model_ref(ref, expected):
    assert parse_model_ref(ref) == expected


def test_ref_round_trips_through_str():
    assert str(parse_model_ref("openrouter:qwen/qwen3.8-flash")) == (
        "openrouter:qwen/qwen3.8-flash"
    )
    assert str(parse_model_ref("gpt-4o-mini")) == "openai:gpt-4o-mini"


def test_gpt_4o_mini_gets_exactly_todays_parameters():
    """The default path must not move: same keys, same values as before."""
    assert build_chat_params("gpt-4o-mini", "text", max_tokens=2000, temperature=0.7) == {
        "model": "gpt-4o-mini",
        "temperature": 0.7,
        "max_tokens": 2000,
    }
    assert build_chat_params("gpt-4o-mini", "barcode", max_tokens=64) == {
        "model": "gpt-4o-mini",
        "max_tokens": 64,
    }


def test_legacy_chat_family_keeps_temperature_and_max_tokens():
    params = build_chat_params("gpt-4o", "text", max_tokens=2000, temperature=0.7)
    assert params == {"model": "gpt-4o", "temperature": 0.7, "max_tokens": 2000}


def test_reasoning_model_at_default_effort_drops_temperature_and_raises_budget():
    params = build_chat_params("openai:gpt-6-luna", "text", max_tokens=2000, temperature=0.7)
    assert "temperature" not in params
    assert "max_tokens" not in params
    assert params["max_completion_tokens"] == REASONING_TOKEN_FLOOR
    # Default effort: nothing sent, the model's own default (medium) applies.
    assert "reasoning_effort" not in params


def test_reasoning_model_with_effort_none_keeps_temperature_and_budget():
    params = build_chat_params(
        "openai:gpt-6-luna", "text", max_tokens=2000, temperature=0.7, effort="none"
    )
    assert params == {
        "model": "gpt-6-luna",
        "temperature": 0.7,
        "max_completion_tokens": 2000,
        "reasoning_effort": "none",
    }


def test_barcode_and_label_default_to_no_reasoning_where_possible():
    barcode = build_chat_params("openai:gpt-6-luna", "barcode", max_tokens=64)
    assert barcode["reasoning_effort"] == "none"
    assert barcode["max_completion_tokens"] == 64
    label = build_chat_params("openai:gpt-6-luna", "label_ocr", max_tokens=256)
    assert label["reasoning_effort"] == "none"
    assert label["max_completion_tokens"] == 256


def test_mandatory_reasoning_model_gets_lowest_effort_and_the_floor_for_barcode():
    """Gemini 3.8 Flash can't turn thinking off: 64 tokens would come back empty."""
    params = build_chat_params("openrouter:google/gemini-3.8-flash", "barcode", max_tokens=64)
    assert params["extra_body"]["reasoning"] == {"effort": "low"}
    assert params["max_tokens"] == REASONING_TOKEN_FLOOR


def test_flash_lite_barcode_uses_minimal():
    params = build_chat_params("openrouter:google/gemini-3.5-flash-lite", "barcode", max_tokens=64)
    assert params["extra_body"]["reasoning"] == {"effort": "minimal"}


@pytest.mark.parametrize("ref", [r for r in REGISTRY if r.startswith("openrouter:")])
def test_every_openrouter_request_requires_parameters_and_denies_data_collection(ref):
    params = build_chat_params(ref, "vision", max_tokens=2000)
    provider = params["extra_body"]["provider"]
    assert provider["require_parameters"] is True
    assert provider["data_collection"] == "deny"
    assert params["model"] == ref.split(":", 1)[1]


def test_openrouter_prefs_are_copied_not_shared():
    params = build_chat_params("openrouter:qwen/qwen3.8-flash", "text", max_tokens=10)
    params["extra_body"]["provider"]["data_collection"] = "allow"
    assert OPENROUTER_PROVIDER_PREFS["data_collection"] == "deny"


def test_openrouter_effort_goes_in_extra_body_not_reasoning_effort():
    params = build_chat_params(
        "openrouter:anthropic/claude-haiku-5.5", "text", max_tokens=2000, effort="low"
    )
    assert "reasoning_effort" not in params
    assert params["extra_body"]["reasoning"] == {"effort": "low"}


def test_openrouter_model_without_temperature_never_gets_one():
    """require_parameters + an unlisted temperature = no eligible provider."""
    for effort in (None, "none"):
        params = build_chat_params(
            "openrouter:openai/gpt-6-luna",
            "text",
            max_tokens=2000,
            temperature=0.7,
            effort=effort,
        )
        assert "temperature" not in params


def test_gemini_keeps_temperature_while_reasoning():
    params = build_chat_params(
        "openrouter:google/gemini-3.8-flash", "text", max_tokens=2000, temperature=0.7
    )
    assert params["temperature"] == 0.7


def test_model_without_effort_knob_gets_no_effort_even_if_configured():
    params = build_chat_params(
        "openrouter:qwen/qwen3.8-flash", "text", max_tokens=2000, effort="high"
    )
    assert "reasoning" not in params["extra_body"]
    assert params["max_tokens"] == REASONING_TOKEN_FLOOR


def test_unsupported_configured_effort_falls_back_to_task_default():
    params = build_chat_params(
        "openrouter:google/gemini-3.8-flash", "barcode", max_tokens=64, effort="none"
    )
    assert params["extra_body"]["reasoning"] == {"effort": "low"}


def test_unknown_openai_model_gets_the_cautious_default():
    spec = spec_for("gpt-9-nova")
    assert spec.known is False
    params = build_chat_params("gpt-9-nova", "text", max_tokens=2000, temperature=0.7)
    assert params == {"model": "gpt-9-nova", "max_completion_tokens": REASONING_TOKEN_FLOOR}


def test_unknown_openrouter_model_uses_max_tokens():
    params = build_chat_params("openrouter:mistral/x", "text", max_tokens=100)
    assert params["max_tokens"] == REASONING_TOKEN_FLOOR
    assert "max_completion_tokens" not in params
    assert params["extra_body"]["provider"]["data_collection"] == "deny"


def test_gpt_4o_mini_row_names_its_snapshot_and_lifecycle_source():
    spec = spec_for("gpt-4o-mini")
    assert spec.snapshot == "gpt-4o-mini-2024-07-18"
    assert spec.first_party == ("openai", "gpt-4o-mini")
