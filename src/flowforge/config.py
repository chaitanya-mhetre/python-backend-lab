"""Typed settings loaded from environment variables (prefix ``FLOWFORGE_``) or ``.env``."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FLOWFORGE_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://flowforge:flowforge@localhost:55432/flowforge"
    redis_url: str = "redis://localhost:56379/0"
    jwt_secret: SecretStr = SecretStr("dev-only-secret-change-me-please-32b")
    jwt_ttl_minutes: int = Field(default=15, ge=1, le=60 * 24)
    jwt_algorithm: str = "HS256"
    sql_echo: bool = False


@lru_cache
def get_settings() -> Settings:
    """Cached so the environment is parsed once per process."""
    return Settings()
