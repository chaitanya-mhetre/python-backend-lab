"""Short-lived JWT access tokens plus opaque, rotating refresh tokens.

Access tokens are stateless JWTs (15 min). Refresh tokens are random strings: the database
stores only their SHA-256 hash, so a leaked database dump cannot be replayed.
"""

from __future__ import annotations

import hashlib
import secrets
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


def new_refresh_token() -> tuple[str, str]:
    """Return ``(plaintext, sha256_hex)``. Only the hash is ever stored."""
    plaintext = secrets.token_urlsafe(32)  # 256 bits of randomness
    return plaintext, hash_refresh_token(plaintext)


def hash_refresh_token(plaintext: str) -> str:
    # A fast hash is fine here (unlike passwords): the input already has 256 bits of entropy,
    # so brute-forcing the hash is hopeless.
    return hashlib.sha256(plaintext.encode()).hexdigest()
