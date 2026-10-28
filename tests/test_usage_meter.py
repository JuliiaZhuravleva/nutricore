"""The 💸 meter: what one request's model calls cost.

Usage objects are the SDK's own types, built the way the SDK parses a reply, so
a renamed field shows up here and not as a silent $0 in the chat.
"""

import asyncio

import pytest
from openai.types import CompletionUsage, CreateEmbeddingResponse
from openai.types.responses import ResponseUsage

from app.services import usage_meter as um
from app.services.usage_meter import metering, record


def _chat(prompt, completion, cached=0, **extra):
    return CompletionUsage.model_validate(
        {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
            "prompt_tokens_details": {"cached_tokens": cached},
            **extra,
        }
    )


def _responses(tokens_in, tokens_out, cached=0):
    return ResponseUsage.model_validate(
        {
            "input_tokens": tokens_in,
            "output_tokens": tokens_out,
            "total_tokens": tokens_in + tokens_out,
            "input_tokens_details": {"cached_tokens": cached, "cache_write_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 0},
        }
    )


def _embedding(tokens):
    return CreateEmbeddingResponse.model_validate(
        {
            "data": [{"embedding": [0.1], "index": 0, "object": "embedding"}],
            "model": "text-embedding-3-small",
            "object": "list",
            "usage": {"prompt_tokens": tokens, "total_tokens": tokens},
        }
    ).usage


def test_a_chat_call_is_priced_with_cached_input_cheaper():
    # gpt-6-luna: $0.10 in, $0.01 cached, $0.50 out per 1M tokens.
    with metering() as meter:
        record("openai:gpt-6-luna", _chat(1_000_000, 100_000, cached=400_000))
    assert meter.usd == pytest.approx(0.6 * 0.10 + 0.4 * 0.01 + 0.1 * 0.50)
    assert meter.calls == 1 and meter.unpriced == []


def test_a_web_search_adds_its_call_fee_to_the_tokens():
    with metering() as meter:
        record("gpt-6-luna", _responses(8_000, 300), web_searches=2)
    tokens = (8_000 * 0.10 + 300 * 0.50) / 1_000_000
    assert meter.usd == pytest.approx(tokens + 2 * 0.01)


def test_an_embedding_is_priced_by_its_prompt_tokens():
    with metering() as meter:
        record("text-embedding-3-small", _embedding(500_000))
    assert meter.usd == pytest.approx(0.01)


def test_openrouter_reported_cost_wins_over_the_price_list():
    usage = _chat(1_000, 100, cost=0.0021)  # an extra field, as OpenRouter sends
    with metering() as meter:
        record("openrouter:anthropic/claude-haiku-5.5", usage)
    assert meter.usd == pytest.approx(0.0021)
    assert meter.unpriced == []


def test_an_unknown_model_is_counted_not_guessed():
    with metering() as meter:
        record("openrouter:qwen/qwen3.8-flash", _chat(1_000, 100))
        record("gpt-6-luna", _chat(1_000, 100))
    assert meter.calls == 2
    assert meter.unpriced == ["openrouter:qwen/qwen3.8-flash"]
    assert "цена неизвестна: openrouter:qwen/qwen3.8-flash" in meter.line()


@pytest.mark.parametrize("usage", [None, object(), {"prompt_tokens": "много"}])
def test_a_reply_without_usable_usage_never_breaks_the_call(usage):
    with metering() as meter:
        record("gpt-6-luna", usage)
    assert meter.calls == 1 and meter.usd == 0
    assert meter.unpriced == ["gpt-6-luna"]


def test_no_meter_no_record():
    record("gpt-6-luna", _chat(1_000, 100))  # outside a meter: nothing to fail
    with metering() as meter:
        pass
    assert meter.calls == 0
    assert meter.line() == "💸 Запрос: $0 · без вызовов ИИ"


def test_concurrent_tasks_add_to_the_same_meter():
    async def calls():
        await asyncio.gather(
            *(
                asyncio.create_task(
                    asyncio.to_thread(record, "gpt-6-luna", _chat(10, 1))
                )
                for _ in range(3)
            )
        )

    with metering() as meter:
        asyncio.run(calls())
    assert meter.calls == 3


def test_a_meter_ends_with_its_block():
    with metering() as outer:
        with metering() as inner:
            record("gpt-6-luna", _chat(10, 1))
        record("gpt-6-luna", _chat(10, 1))
    assert (outer.calls, inner.calls) == (1, 1)
    assert um._current.get() is None


@pytest.mark.parametrize(
    "n, words",
    [(1, "1 вызов ИИ"), (3, "3 вызова ИИ"), (5, "5 вызовов ИИ"), (11, "11 вызовов ИИ")],
)
def test_the_line_counts_calls_in_russian(n, words):
    with metering() as meter:
        for _ in range(n):
            record("gpt-6-luna", _chat(1_000, 100))
    assert meter.line() == f"💸 Запрос: ${meter.usd:.4f} · {words}"
