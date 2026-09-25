"""Per-request context stored in ``contextvars``.

A ContextVar is like a thread-local, but safe for asyncio: every request (task) sees its own
value, even though thousands of requests share one thread. Logs, audit rows and error bodies
read the request id from here instead of passing it through every function.
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from enum import StrEnum


class ActorType(StrEnum):
    USER = "user"
    API_KEY = "api_key"
    SYSTEM = "system"


@dataclass(frozen=True, slots=True)
class Actor:
    type: ActorType
    id: uuid.UUID | None


SYSTEM_ACTOR = Actor(ActorType.SYSTEM, None)

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
actor_var: ContextVar[Actor] = ContextVar("actor", default=SYSTEM_ACTOR)
