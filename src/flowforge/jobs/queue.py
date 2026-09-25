"""Job queue abstraction.

Services and the worker depend on the small ``JobQueue`` protocol, not on arq directly.
Production uses :class:`ArqJobQueue` (Redis); tests use :class:`RecordingJobQueue` and run
the jobs themselves, which makes background behaviour deterministic to test.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Protocol

from arq.connections import ArqRedis


class JobQueue(Protocol):
    async def enqueue(
        self,
        function: str,
        *args: Any,
        job_id: str | None = None,
        defer_by: float | None = None,
    ) -> None: ...


class ArqJobQueue:
    def __init__(self, redis: ArqRedis) -> None:
        self._redis = redis

    async def enqueue(
        self,
        function: str,
        *args: Any,
        job_id: str | None = None,
        defer_by: float | None = None,
    ) -> None:
        await self._redis.enqueue_job(
            function,
            *args,
            _job_id=job_id,
            _defer_by=timedelta(seconds=defer_by) if defer_by else None,
        )


@dataclass(frozen=True, slots=True)
class EnqueuedJob:
    function: str
    args: tuple[Any, ...]
    job_id: str | None
    defer_by: float | None


@dataclass
class RecordingJobQueue:
    """In-memory queue for tests. Honours job_id de-duplication like arq does."""

    jobs: list[EnqueuedJob] = field(default_factory=list)
    _seen_ids: set[str] = field(default_factory=set)

    async def enqueue(
        self,
        function: str,
        *args: Any,
        job_id: str | None = None,
        defer_by: float | None = None,
    ) -> None:
        if job_id is not None:
            if job_id in self._seen_ids:
                return
            self._seen_ids.add(job_id)
        self.jobs.append(EnqueuedJob(function, args, job_id, defer_by))

    def pop(self) -> EnqueuedJob | None:
        return self.jobs.pop(0) if self.jobs else None
