"""Thin client for the my-health hub's /consult endpoint.

Mirrors the OpenAIService pattern: the external integration lives in a small
service class so the HTTP details (timeout, headers, response validation) are
reusable and unit-testable independent of the Telegram handler. This path must
never touch OpenAI or store medical data — the hub owns all medical reasoning.
"""

import httpx

from app.core.config import settings


class ConsultService:
    async def ask(self, question: str) -> dict:
        """Relay a question to the hub and return its parsed JSON object.

        Raises httpx.HTTPError / httpx.InvalidURL on transport/HTTP failures and
        ValueError on a malformed (non-JSON or non-object) body. Callers handle
        these and show a friendly message.
        """
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                settings.MYHEALTH_CONSULT_URL,
                json={"question": question},
                headers={"X-Consult-Token": settings.CONSULT_TOKEN},
            )
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict):
            raise ValueError(f"unexpected consult response shape: {type(data)}")
        return data


consult_service = ConsultService()
