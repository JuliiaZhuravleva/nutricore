from typing import Any, Dict, Optional, List
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    # Telegram
    TELEGRAM_BOT_TOKEN: str
    TELEGRAM_ADMIN_USERNAME: str = "@admin_username"  # Default admin username
    TELEGRAM_ADMIN_IDS: str = "123456789"  # Replace with your admin Telegram IDs (comma-separated)
    TELEGRAM_WEBHOOK_URL: Optional[str] = None

    @property
    def admin_ids(self) -> List[int]:
        """Convert comma-separated string of admin IDs to list of integers"""
        if not self.TELEGRAM_ADMIN_IDS:
            return []
        # Remove brackets and split by comma
        clean_ids = self.TELEGRAM_ADMIN_IDS.strip('[]').replace(' ', '')
        return [int(id_) for id_ in clean_ids.split(',') if id_]

    # Certbot
    CERTBOT_EMAIL: Optional[str] = None
    DOMAIN: Optional[str] = None

    # OpenAI
    OPENAI_API_KEY: Optional[str] = None
    OPENAI_MODEL: str = "gpt-4o-mini"
    OPENAI_TEMPERATURE: float = 0.7
    OPENAI_MAX_TOKENS: int = 2000

    # Application Settings
    DEBUG: bool = False
    ENVIRONMENT: str = "development"
    ALLOWED_HOSTS: List[str] = ["*"]

    # Nutrition Analysis Settings
    DEFAULT_TIMEZONE: str = "Asia/Tbilisi"

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=True)


settings = Settings()