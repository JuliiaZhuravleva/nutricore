"""FastAPI dependencies (auth gates for the REST API and the Telegram webhook).

Both use a constant-time comparison and are fail-closed: if the corresponding
secret is not configured, access is denied rather than allowed.
"""

import hmac
from typing import Optional

from fastapi import Header, HTTPException, status

from app.core.config import settings


def _token_matches(provided: Optional[str], expected: Optional[str]) -> bool:
    """Constant-time compare of two secrets; False if either is missing.

    Compares as bytes because header values may be non-ASCII (latin-1 decoded),
    and hmac.compare_digest raises TypeError on non-ASCII str operands.
    """
    if not provided or not expected:
        return False
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


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
    if not _token_matches(x_api_token, settings.API_TOKEN):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API token.",
        )


def require_export_token(
    x_export_token: Optional[str] = Header(None, alias="X-Export-Token"),
) -> None:
    """Second gate for the read-only meals export (on top of require_api_token).

    Fail-closed: if EXPORT_API_TOKEN is unset the export is disabled (403); a
    missing or wrong token is rejected (403). Constant-time compare, same as
    require_api_token.
    """
    if not settings.EXPORT_API_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Export is disabled (no EXPORT_API_TOKEN configured).",
        )
    if not _token_matches(x_export_token, settings.EXPORT_API_TOKEN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or missing export token.",
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
    if not _token_matches(
        x_telegram_bot_api_secret_token, settings.TELEGRAM_WEBHOOK_SECRET
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid webhook secret.",
        )
