from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    ENV: Literal["local", "prod"] = "prod"
    AGENTGATEWAY_BASE_URL: str
    AGENTGATEWAY_API_KEY: str
    AUTH_SERVICE_URL: str = "http://localhost:3005"
    ALLOWED_TARGET_HOSTS: str = "backend"
    LOG_LEVEL: Literal["debug", "info", "warning", "error"] = "info"
    LOG_FORMAT: Literal["json", "human"] = "human"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
