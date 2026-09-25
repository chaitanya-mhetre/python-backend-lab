"""Short-lived JWT access tokens. Refresh tokens are out of scope here (see production-fastapi)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt

from flowforge.config import Settings
from flowforge.domain.errors import AuthenticationError


def create_access_token(user_id: uuid.UUID, settings: Settings) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_ttl_minutes),
        "typ": "access",
    }
    return jwt.encode(payload, settings.jwt_secret.get_secret_value(), settings.jwt_algorithm)


def decode_access_token(token: str, settings: Settings) -> uuid.UUID:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=[settings.jwt_algorithm],  # never let the token choose its algorithm
            options={"require": ["exp", "sub"]},
        )
    except jwt.PyJWTError as exc:
        raise AuthenticationError("invalid or expired token") from exc
    if payload.get("typ") != "access":
        raise AuthenticationError("wrong token type")
    return uuid.UUID(payload["sub"])
