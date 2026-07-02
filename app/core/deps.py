"""FastAPI dependencies (auth gates for the REST API and the Telegram webhook).

Both use a constant-time comparison and are fail-closed: if the corresponding
secret is not configured, access is denied rather than allowed.
"""

import hmac
from typing import Optional

from fastapi import Header, HTTPException, status

from app.core.config import settings


def require_api_token(
    x_api_token: Optional[str] = Header(None, alias="X-API-Token"),
) -> None:
    """Guard the REST API with a shared token sent in the X-API-Token header.

    Fail-closed: if API_TOKEN is unset the whole API is disabled (503); a missing
    or wrong token is rejected (401).
    """
    if not settings.API_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API is disabled (no API_TOKEN configured).",
        )
    if not x_api_token or not hmac.compare_digest(x_api_token, settings.API_TOKEN):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API token.",
        )


def require_webhook_secret(
    x_telegram_bot_api_secret_token: Optional[str] = Header(
        None, alias="X-Telegram-Bot-Api-Secret-Token"
    ),
) -> None:
    """Verify Telegram's webhook secret token header.

    Fail-closed: if TELEGRAM_WEBHOOK_SECRET is unset, or the header is missing or
    does not match, the request is rejected (403). This prevents forged updates.
    """
    secret = settings.TELEGRAM_WEBHOOK_SECRET
    if (
        not secret
        or not x_telegram_bot_api_secret_token
        or not hmac.compare_digest(x_telegram_bot_api_secret_token, secret)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid webhook secret.",
        )
