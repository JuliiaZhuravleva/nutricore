"""What one request cost: the model calls made while a meter runs, priced.

    with metering() as meter:
        await resolve_meal_nutrition(...)   # vision, web search, embeddings…
    meter.line()  →  "💸 Запрос: $0.0012 · 3 вызова ИИ"

Every model call goes through ``openai_service``, which ``record``s the
response's ``usage`` here; outside a meter that is a no-op. The meter lives in
a ContextVar, so the pipeline's concurrent tasks (copies of the context) add
to the same meter.

The price is an estimate from the token counts and ``PRICES`` (OpenAI's
standard tier, checked 2026-10-28 on developers.openai.com/api/docs/pricing);
OpenRouter reports its own ``usage.cost``, which is taken as is. A call with
no known price is counted, not guessed: the line says so.
"""

from __future__ import annotations

import contextlib
import logging
from contextvars import ContextVar
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# USD per 1M tokens: input, cached input, output (reasoning tokens are output).
PRICES = {
    "gpt-6-luna": (0.10, 0.01, 0.50),
    "gpt-5.6-luna": (0.20, 0.02, 1.20),
    "gpt-6.1-terra": (1.00, 0.05, 5.00),
    "gpt-4o-mini": (0.15, 0.075, 0.60),
    "text-embedding-3-small": (0.02, 0.02, 0.0),
}
WEB_SEARCH_CALL_USD = 10.00 / 1000  # per call, on top of the tokens


@dataclass
class Meter:
    calls: int = 0
    usd: float = 0.0
    unpriced: list[str] = field(default_factory=list)

    def add(self, model: str, usage, *, web_searches: int = 0) -> None:
        self.calls += 1
        cost = _reported_cost(usage)
        if cost is None:
            cost = _estimate(model, usage)
        if cost is None:
            self.unpriced.append(model)
            return
        self.usd += cost + web_searches * WEB_SEARCH_CALL_USD

    def line(self) -> str:
        if not self.calls:
            return "💸 Запрос: $0 · без вызовов ИИ"
        text = f"💸 Запрос: ${self.usd:.4f} · {_calls(self.calls)}"
        if self.unpriced:
            text += f" (цена неизвестна: {', '.join(sorted(set(self.unpriced)))})"
        return text


_current: ContextVar[Meter | None] = ContextVar("usage_meter", default=None)


@contextlib.contextmanager
def metering():
    meter = Meter()
    token = _current.set(meter)
    try:
        yield meter
    finally:
        _current.reset(token)


def record(model: str, usage, *, web_searches: int = 0) -> None:
    """One model call's ``usage``; never raises (a meter must not break a call)."""
    meter = _current.get()
    if meter is None:
        return
    try:
        meter.add(model, usage, web_searches=web_searches)
    except Exception as e:
        logger.warning("Could not meter a %s call: %s", model, e)
        meter.unpriced.append(model)


def _calls(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        word = "вызов"
    elif n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        word = "вызова"
    else:
        word = "вызовов"
    return f"{n} {word} ИИ"


def _get(obj, name: str):
    # SDK objects keep fields they do not declare (OpenRouter's "cost") as
    # attributes too, so getattr reads both.
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _reported_cost(usage) -> float | None:
    cost = _get(usage, "cost")
    return float(cost) if isinstance(cost, (int, float)) else None


def _count(usage, *names: str) -> int | None:
    for name in names:
        value = _get(usage, name)
        if isinstance(value, int):
            return value
    return None


def _estimate(model: str, usage) -> float | None:
    """Chat (prompt/completion), Responses (input/output) and embeddings usage."""
    prices = PRICES.get(model.split(":", 1)[-1])
    tokens_in = _count(usage, "prompt_tokens", "input_tokens")
    if prices is None or tokens_in is None:
        return None
    fresh_in, cached_in, out = prices
    tokens_out = _count(usage, "completion_tokens", "output_tokens") or 0
    details = _get(usage, "prompt_tokens_details") or _get(
        usage, "input_tokens_details"
    )
    cached = _count(details, "cached_tokens") or 0
    return (
        (tokens_in - cached) * fresh_in + cached * cached_in + tokens_out * out
    ) / 1_000_000
