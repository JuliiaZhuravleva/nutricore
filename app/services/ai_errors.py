"""AI failures the user should hear about by name, not as «не удалось».

Two of them need a different answer from «попробуйте ещё раз»:

- **Out of credit** — the balance or the spend limit is used up: OpenAI answers
  429 with ``type="insufficient_quota"`` (its ``code`` varies: the same word, or
  ``credit_balance_exhausted``), OpenRouter answers 402. A retry will not pass
  until someone tops up or raises the limit.
- **Rate limited** — any other 429. It passes in a minute (the SDK has already
  retried it ``OPENAI_MAX_RETRIES`` times by the time it reaches us).

The error is looked for along the whole ``__cause__`` / ``__context__`` chain:
the photo pipeline swallows the vision error and raises its own
«pipeline exhausted» from it.
"""

from __future__ import annotations

from typing import Iterator

import openai

_QUOTA_CODE = "insufficient_quota"

# Where the owner tops up, by the API host that refused.
_BILLING = {
    "api.openai.com": ("OpenAI", "platform.openai.com → Settings → Billing / Limits"),
    "openrouter.ai": ("OpenRouter", "openrouter.ai → Settings → Credits"),
}


def _chain(exc: BaseException | None) -> Iterator[BaseException]:
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        yield exc
        exc = exc.__cause__ or exc.__context__


def _is_quota(e: openai.RateLimitError) -> bool:
    return _QUOTA_CODE in (e.type, e.code)


def out_of_credit(exc: BaseException) -> openai.APIStatusError | None:
    """The refusal that says the balance or spend limit is used up, if any."""
    for e in _chain(exc):
        if not isinstance(e, openai.APIStatusError):
            continue
        if e.status_code == 402:
            return e
        if isinstance(e, openai.RateLimitError) and _is_quota(e):
            return e
    return None


def rate_limited(exc: BaseException) -> bool:
    """A 429 that is not about money: wait a minute and it passes."""
    return any(
        isinstance(e, openai.RateLimitError) and not _is_quota(e) for e in _chain(exc)
    )


def billing(err: openai.APIStatusError) -> tuple[str, str]:
    """(provider, where to top up) for the API that refused."""
    host = err.request.url.host
    return _BILLING.get(host, (host, f"кабинет {host}"))
