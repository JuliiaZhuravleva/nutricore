"""ai_errors: which refusals mean «the AI balance is used up» vs «wait a minute».

The errors come out of a real ``AsyncOpenAI`` call against a mocked transport,
so the status → exception class → ``.code`` mapping is the SDK's own, not ours.
"""

import asyncio

import httpx
import openai
import pytest

from app.services import ai_errors

# OpenAI's answer when the balance / monthly budget is spent.
QUOTA_BODY = {
    "error": {
        "message": "You exceeded your current quota, please check your plan and "
        "billing details.",
        "type": "insufficient_quota",
        "param": None,
        "code": "insufficient_quota",
    }
}
# A plain rate limit: requests per minute.
RATE_BODY = {
    "error": {
        "message": "Rate limit reached for gpt-6-luna on requests per min (RPM).",
        "type": "requests",
        "param": None,
        "code": "rate_limit_exceeded",
    }
}
# OpenRouter's answer with no credits left (its code is a number).
CREDITS_BODY = {"error": {"code": 402, "message": "Insufficient credits."}}


def sdk_error(status: int, body: dict, host: str = "api.openai.com") -> Exception:
    """The exception a real chat call raises when the API answers ``status``."""
    transport = httpx.MockTransport(lambda request: httpx.Response(status, json=body))
    client = openai.AsyncOpenAI(
        api_key="sk-test",
        base_url=f"https://{host}/v1",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=transport),
    )

    async def call():
        await client.chat.completions.create(
            model="gpt-6-luna", messages=[{"role": "user", "content": "hi"}]
        )

    with pytest.raises(openai.APIStatusError) as caught:
        asyncio.run(call())
    return caught.value


def test_openai_quota_is_out_of_credit():
    err = sdk_error(429, QUOTA_BODY)
    assert ai_errors.out_of_credit(err) is err
    assert not ai_errors.rate_limited(err)
    assert ai_errors.billing(err)[0] == "OpenAI"


def test_openrouter_402_is_out_of_credit():
    err = sdk_error(402, CREDITS_BODY, host="openrouter.ai/api")
    assert type(err) is openai.APIStatusError  # the SDK has no class for 402
    assert ai_errors.out_of_credit(err) is err
    assert ai_errors.billing(err) == (
        "OpenRouter",
        "openrouter.ai → Settings → Credits",
    )


def test_plain_429_is_rate_limited_not_out_of_credit():
    err = sdk_error(429, RATE_BODY)
    assert ai_errors.out_of_credit(err) is None
    assert ai_errors.rate_limited(err)


@pytest.mark.parametrize("status", [400, 401, 500])
def test_other_refusals_are_neither(status):
    err = sdk_error(status, {"error": {"message": "nope", "code": None}})
    assert ai_errors.out_of_credit(err) is None
    assert not ai_errors.rate_limited(err)


def test_plain_exception_is_neither():
    assert ai_errors.out_of_credit(RuntimeError("boom")) is None
    assert not ai_errors.rate_limited(RuntimeError("boom"))


def test_found_through_the_cause_chain():
    # The photo pipeline swallows the vision error and raises its own from it.
    quota = sdk_error(429, QUOTA_BODY)
    try:
        try:
            raise quota
        except openai.RateLimitError as inner:
            raise RuntimeError("Pipeline exhausted") from inner
    except RuntimeError as outer:
        assert ai_errors.out_of_credit(outer) is quota


def test_unknown_host_is_named_not_guessed():
    err = sdk_error(402, CREDITS_BODY, host="llm.example.test")
    assert ai_errors.billing(err) == ("llm.example.test", "кабинет llm.example.test")
