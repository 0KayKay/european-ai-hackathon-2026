from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables or .env file."""

    APP_NAME: str = "European AI Hackathon 2026"
    API_BASE: str = "/api/v1"

    ENV: Literal["local", "prod"] = "local"
    LOG_LEVEL: Literal["debug", "info", "warning", "error", "critical"] = "debug"
    LOG_FORMAT: Literal["json", "human"] = "human"

    AGENTGATEWAY_URL: str
    AGENTGATEWAY_API_KEY: str
    AUTH_SERVICE_URL: str = "http://localhost:3005"
    GOOGLE_STORAGE_BUCKET: str
    FILE_STORAGE: Literal["local", "gcs"] = "local"
    PHOENIX_OTLP_ENDPOINT: str = ""

    PRETTY_DOMAIN: str

    DB_HOST: str = "localhost"
    DB_PORT: int = 5432
    DB_DRIVER: Literal["asyncpg"] = "asyncpg"
    DB_USER: str
    DB_PASSWORD: str
    DB_NAME: str

    @property
    def is_local(self) -> bool:
        """Check if running locally."""
        return self.ENV == "local"

    def database_url(self, include_driver: bool = False) -> str:
        """Get the database URL, optionally including the asyncpg driver."""
        schema = "postgresql+asyncpg" if include_driver else "postgresql"
        return f"{schema}://{self.DB_USER}:{self.DB_PASSWORD}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"

    @property
    def version(self) -> str:
        """Get the application version using importlib."""
        from importlib.metadata import version

        return version("hackathon-backend")

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
