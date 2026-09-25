"""Cache-aside for project statistics.

Read path:  try Redis -> on miss, compute from Postgres -> store with TTL -> return.
Write path: any change to a project's tasks deletes the key (invalidate, don't update).
Deleting is simpler and safer than writing the new value: two concurrent writers can't leave
a stale value behind, the next reader just recomputes.

The TTL is a safety net for writes that don't invalidate (e.g. tasks created by the worker).
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from redis.asyncio import Redis


class ProjectStatsCache:
    def __init__(self, redis: Redis | None, ttl_seconds: int) -> None:
        self._redis = redis
        self._ttl = ttl_seconds

    @staticmethod
    def key(project_id: uuid.UUID) -> str:
        return f"cache:project_stats:{project_id}"

    async def get(self, project_id: uuid.UUID) -> dict[str, Any] | None:
        if self._redis is None:
            return None
        raw = await self._redis.get(self.key(project_id))
        if raw is None:
            return None
        value: dict[str, Any] = json.loads(raw)
        return value

    async def set(self, project_id: uuid.UUID, value: dict[str, Any]) -> None:
        if self._redis is not None:
            await self._redis.set(self.key(project_id), json.dumps(value), ex=self._ttl)

    async def invalidate(self, project_id: uuid.UUID) -> None:
        if self._redis is not None:
            await self._redis.delete(self.key(project_id))
