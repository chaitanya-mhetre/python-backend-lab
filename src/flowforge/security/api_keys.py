"""API key format and hashing.

Key shape: ``ff_live_<prefix>_<secret>``
  * ``prefix`` (8 hex chars) is stored in plain text and indexed: it finds the row quickly and
    lets users recognise their keys in the UI.
  * The full key is stored only as a SHA-256 hash. A fast hash is fine here (unlike passwords)
    because the secret has ~256 bits of randomness: brute force is hopeless anyway.
  * Comparison uses ``hmac.compare_digest`` (constant time) so response timing leaks nothing.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass

KEY_PREFIX = "ff_live_"


@dataclass(frozen=True, slots=True)
class GeneratedKey:
    full_key: str
    prefix: str
    key_hash: str


def hash_key(full_key: str) -> str:
    return hashlib.sha256(full_key.encode()).hexdigest()


def generate_key() -> GeneratedKey:
    prefix = secrets.token_hex(4)
    full_key = f"{KEY_PREFIX}{prefix}_{secrets.token_urlsafe(32)}"
    return GeneratedKey(full_key=full_key, prefix=prefix, key_hash=hash_key(full_key))


def looks_like_api_key(token: str) -> bool:
    return token.startswith(KEY_PREFIX)


def parse_prefix(full_key: str) -> str | None:
    rest = full_key.removeprefix(KEY_PREFIX)
    prefix, sep, secret = rest.partition("_")
    if not sep or len(prefix) != 8 or not secret:
        return None
    return prefix


def verify_key(full_key: str, stored_hash: str) -> bool:
    return hmac.compare_digest(hash_key(full_key), stored_hash)
