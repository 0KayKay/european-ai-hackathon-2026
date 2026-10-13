from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables or .env file."""

    APP_NAME: str = "Challenge App"

    APP_ENV: Literal["local", "prod"] = "prod"
    LOG_LEVEL: str = "info"
    LOG_FORMAT: str = "human"

    DB_HOST: str = "localhost"
    DB_PORT: str = "5432"
    DB_USER: str = "postgres"
    DB_PASSWORD: str = "postgres"  # noqa: S105
    DB_NAME: str = "challenge_db"
    DB_DRIVER: Literal["asyncpg"] = "asyncpg"
    CLOUD_SQL_CONNECTION_NAME: str | None = None

    GOOGLE_CLOUD_PROJECT: str = "prompt-injection-challenge"
    VERTEXAI_LOCATION: str = "europe-west1"

    CHAINLIT_ROOT_PATH: str = "/challenge/chat"
    CHALLENGE_PAGES_ROOT_PATH: str = "/challenge"

    IDENTITY_PLATFORM_API_KEY: str = ""
    SIGNING_SECRET: str = ""

    # Gemini API key
    GEMINI_API_KEY: str = ""
    # OpenAI API key for OpenAI Guardrails
    OPENAI_API_KEY: str = ""

    # LangGraph PostgreSQL Psycopg3 connection pool size
    PSYCOPG_MIN_SIZE: int = 1
    PSYCOPG_MAX_SIZE: int = 20

    # For LangGraph ModelCallLimitMiddleware
    LANGGRAPH_MW_MODEL_THREAD_LIMIT: int = 50
    LANGGRAPH_MW_MODEL_RUN_LIMIT: int = 10
    # For LangGraph ToolCallLimitMiddleware
    LANGGRAPH_MW_TOOL_THREAD_LIMIT: int = 50
    LANGGRAPH_MW_TOOL_RUN_LIMIT: int = 10
    # LangGraph recursion limit
    LANGGRAPH_CONFIG_GRAPH_RECURSION_LIMIT: int = 100

    def database_url(self, include_driver: bool = False) -> str:
        """Get the database URL, optionally including the asyncpg driver."""
        schema = "postgresql+asyncpg" if include_driver else "postgresql"
        return f"{schema}://{self.DB_USER}:{self.DB_PASSWORD}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
