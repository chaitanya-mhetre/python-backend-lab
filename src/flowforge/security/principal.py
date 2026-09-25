"""Who is calling: a logged-in user (JWT) or a machine (API key)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from flowforge.db.models import ApiKey, User


@dataclass(frozen=True, slots=True)
class Principal:
    user: User | None = None
    api_key: ApiKey | None = None

    def __post_init__(self) -> None:
        if (self.user is None) == (self.api_key is None):
            raise ValueError("a principal is exactly one of: user, api_key")

    @property
    def acting_user_id(self) -> uuid.UUID:
        """User recorded as creator of things: the user, or the key's creator."""
        if self.user is not None:
            return self.user.id
        assert self.api_key is not None
        return self.api_key.created_by
