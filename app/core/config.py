import logging
from typing import Any, Dict, List, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


def _parse_id_list(raw: Optional[str], field_name: str) -> List[int]:
    """Parse a comma-separated id string into ints, skipping malformed entries.

    Tolerant by design: these lists are evaluated on the hot path (every access
    check), so a config typo must never raise and break the bot for everyone.
    """
    if not raw:
        return []
    clean = raw.strip("[]").replace(" ", "")
    ids: List[int] = []
    for id_ in clean.split(","):
        if not id_:
            continue
        try:
            ids.append(int(id_))
        except ValueError:
            logger.warning("Ignoring non-integer %s entry: %r", field_name, id_)
    return ids


class Settings(BaseSettings):
    PROJECT_NAME: str = "Nutrition Bot"
    API_V1_STR: str = "/api/v1"

    # PostgreSQL
    POSTGRES_SERVER: str
    POSTGRES_USER: str
    POSTGRES_PASSWORD: str
    POSTGRES_DB: str
    POSTGRES_PORT: str

    @property
    def SQLALCHEMY_DATABASE_URI(self) -> str:
        return f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_SERVER}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"

    # Redis
    REDIS_HOST: str
    REDIS_PORT: int

    @property
    def CELERY_BROKER_URL(self) -> str:
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/0"

    @property
    def CELERY_RESULT_BACKEND(self) -> str:
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/0"

    # JWT
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60

    # REST API auth. Callers send it as the X-API-Token header. Empty/unset →
    # the API is disabled (every /api/v1 route returns 503), i.e. fail-closed.
    API_TOKEN: Optional[str] = None

    # Telegram
    TELEGRAM_BOT_TOKEN: str
    TELEGRAM_ADMIN_USERNAME: str = "@admin_username"  # Default admin username
    # Empty by default (fail-closed): no admin until explicitly configured.
    TELEGRAM_ADMIN_IDS: str = ""  # comma-separated admin Telegram IDs
    TELEGRAM_WEBHOOK_URL: Optional[str] = None
    # Shared secret for webhook mode; Telegram echoes it in
    # X-Telegram-Bot-Api-Secret-Token. Required to register a webhook.
    TELEGRAM_WEBHOOK_SECRET: Optional[str] = None

    # Access control (OpenClaw-style modes). Mode: open | whitelist | closed.
    #   open      → everyone may use the bot
    #   whitelist → only admins + ALLOWED_TELEGRAM_IDS; others are dropped silently
    #   closed    → only admins (maintenance)
    # Default is whitelist so an unconfigured bot never answers strangers.
    BOT_ACCESS_MODE: str = "whitelist"
    ALLOWED_TELEGRAM_IDS: str = ""  # comma-separated; admins are always allowed too

    @property
    def admin_ids(self) -> List[int]:
        """Admin (owner) telegram ids; tolerant of malformed config entries."""
        return _parse_id_list(self.TELEGRAM_ADMIN_IDS, "TELEGRAM_ADMIN_IDS")

    @property
    def allowed_ids(self) -> List[int]:
        """Whitelisted (non-admin) telegram ids; tolerant of malformed entries."""
        return _parse_id_list(self.ALLOWED_TELEGRAM_IDS, "ALLOWED_TELEGRAM_IDS")

    @property
    def access_mode(self) -> str:
        """Normalized access mode; unknown values fall back to the safe 'whitelist'."""
        mode = (self.BOT_ACCESS_MODE or "").strip().lower()
        if mode not in ("open", "whitelist", "closed"):
            logger.warning(
                "Unknown BOT_ACCESS_MODE %r; falling back to 'whitelist'",
                self.BOT_ACCESS_MODE,
            )
            return "whitelist"
        return mode

    # Certbot
    CERTBOT_EMAIL: Optional[str] = None
    DOMAIN: Optional[str] = None

    # OpenAI
    OPENAI_API_KEY: Optional[str] = None
    OPENAI_MODEL: str = "gpt-4o-mini"
    OPENAI_TEMPERATURE: float = 0.7
    OPENAI_MAX_TOKENS: int = 2000
    # Retries for transient OpenAI errors (429 / 5xx / network). The SDK applies
    # exponential backoff; 0 disables retrying.
    OPENAI_MAX_RETRIES: int = 2

    # Application Settings
    DEBUG: bool = False
    ENVIRONMENT: str = "development"
    # Root log level (DEBUG surfaces the raw OpenAI traces; INFO keeps them quiet).
    LOG_LEVEL: str = "INFO"
    # Retention for the ai_call_logs debug table; a daily Celery-beat job prunes
    # rows older than this.
    DEBUG_LOG_RETENTION_DAYS: int = 60
    # Retention for the inbound_messages history/replay table (TD-009). Its own
    # knob because these rows are user content (photo references), not just debug
    # traces; a daily Celery-beat job prunes rows older than this.
    INBOUND_MESSAGE_RETENTION_DAYS: int = 60
    # Loopback-only by default (the API is internal, not published). Prod
    # deployments behind a domain must add their host(s) via the env var.
    ALLOWED_HOSTS: List[str] = ["127.0.0.1", "localhost"]

    # Nutrition Analysis Settings
    DEFAULT_TIMEZONE: str = "Asia/Tbilisi"

    # Consult relay → my-health hub (loopback).
    # Empty URL/token → the /consult command is disabled.
    MYHEALTH_CONSULT_URL: Optional[str] = None  # e.g. http://127.0.0.1:8787/consult
    CONSULT_TOKEN: Optional[str] = None  # matches the hub's COPILOT_CONSULT_TOKEN

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=True)


settings = Settings()
