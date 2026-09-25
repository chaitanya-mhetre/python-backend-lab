"""Keyset (cursor) pagination.

Offset pagination (``OFFSET 1000``) re-scans skipped rows and returns duplicates or gaps when
rows are inserted while a client pages. Keyset pagination remembers the last row's sort key
and asks for rows *after* it: ``WHERE (created_at, id) > (:last_created_at, :last_id)``.
The ``id`` tie-breaker makes the order total, so no row is ever skipped or repeated.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any

from flowforge.domain.errors import DomainError


class InvalidCursorError(DomainError):
    code = "invalid_cursor"


@dataclass(frozen=True, slots=True)
class Page[T]:
    items: list[T]
    next_cursor: str | None


def encode_cursor(values: list[Any]) -> str:
    raw = json.dumps(values, default=str, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> list[Any]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        values = json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, json.JSONDecodeError) as exc:
        raise InvalidCursorError("cursor is malformed") from exc
    if not isinstance(values, list):
        raise InvalidCursorError("cursor is malformed")
    return values
