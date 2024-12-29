from typing import Any, Dict, Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    PROJECT_NAME: str = "Nutrition Bot"
    API_V1_STR: str = "/api/v1"

    # PostgreSQL
    POSTGRES_SERVER: str
    POSTGRES_USER: str
    POSTGRES_PASSWORD: str
    POSTGRES_DB: str
    POSTGRES_PORT: str = "5432"
    SQLALCHEMY_DATABASE_URI: Optional[str] = None

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

    # Telegram
    TELEGRAM_BOT_TOKEN: str
    TELEGRAM_WEBHOOK_URL: Optional[str] = None
    TELEGRAM_ADMIN_IDS: list[int] = []

    # OpenAI
    OPENAI_API_KEY: Optional[str] = None
    OPENAI_MODEL: str = "gpt-4-turbo-preview"
    OPENAI_TEMPERATURE: float = 0.7
    OPENAI_MAX_TOKENS: int = 2000

    # SSL/Domain settings
    DOMAIN: Optional[str] = None
    CERTBOT_EMAIL: Optional[str] = None

    # Application Settings
    DEBUG: bool = False
    ENVIRONMENT: str = "production"
    ALLOWED_HOSTS: list[str] = ["*"]

    # Nutrition Analysis Settings
    DEFAULT_TIMEZONE: str = "Europe/Moscow"
    MEAL_PHOTO_MAX_SIZE: int = 10 * 1024 * 1024  # 10MB
    SUPPORTED_PHOTO_TYPES: list[str] = ["image/jpeg", "image/png"]

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=True,
        extra='allow'  # Разрешаем дополнительные поля
    )

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.SQLALCHEMY_DATABASE_URI = (
            f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_SERVER}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )


settings = Settings()