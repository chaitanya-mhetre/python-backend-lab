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
    refresh_ttl_days: int = Field(default=14, ge=1, le=90)
    sql_echo: bool = False
    # Token bucket: burst of `capacity` requests, refilled at `refill_per_sec`.
    rate_limit_capacity: int = Field(default=60, ge=1)
    rate_limit_refill_per_sec: float = Field(default=1.0, gt=0)
    cache_ttl_seconds: int = Field(default=60, ge=1)
    # Fernet key that encrypts webhook signing secrets at rest. Dev default only!
    secret_encryption_key: SecretStr = SecretStr("HJjtDt5BGKz2BlyOyjuIkp6bA6TYT9sGhqIGU6VKJKQ=")
    # Only for local development against e.g. http://localhost receivers.
    webhook_allow_private_targets: bool = False
    max_request_body_bytes: int = 1_000_000
    log_level: str = "INFO"
    log_json: bool = True
    worker_metrics_port: int = 9101
    # Cron runs older than this are treated as missed and skipped (see domain/schedule.py).
    schedule_lookback_seconds: int = Field(default=300, ge=60)


@lru_cache
def get_settings() -> Settings:
    """Cached so the environment is parsed once per process."""
    return Settings()
