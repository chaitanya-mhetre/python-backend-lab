"""End-to-end with a *real* arq worker and Redis (port 56379), not the recording queue."""

from __future__ import annotations

from typing import Any

import pytest
from arq import create_pool
from arq.connections import ArqRedis, RedisSettings
from arq.worker import Worker
from httpx import AsyncClient
from sqlalchemy import select

from flowforge.db.models import TaskRow, WorkflowExecutionRow
from flowforge.domain.workflow import ExecutionStatus
from flowforge.jobs.queue import ArqJobQueue, RecordingJobQueue
from flowforge.worker.runner import WorkerDeps, dispatch_event, run_execution
from tests.integration.conftest import MakeUser, post_json

API = "/api/v1"
REDIS_URL = "redis://localhost:56379/15"  # separate DB index for tests


@pytest.fixture
async def redis() -> Any:
    try:
        pool = await create_pool(RedisSettings.from_dsn(REDIS_URL), retry=0)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Redis not reachable at {REDIS_URL} ({exc}); run `make up`")
    await pool.flushdb()
    yield pool
    await pool.flushdb()
    await pool.aclose()


async def test_real_arq_worker_runs_workflow(
    client: AsyncClient,
    make_user: MakeUser,
    queue: RecordingJobQueue,
    worker_deps: WorkerDeps,
    redis: ArqRedis,
) -> None:
    headers = await make_user()
    org = await post_json(client, f"{API}/orgs", headers, {"name": "Acme"})
    project = await post_json(client, f"{API}/orgs/{org['id']}/projects", headers, {"name": "P"})
    await post_json(
        client,
        f"{API}/orgs/{org['id']}/workflows",
        headers,
        {
            "name": "welcome",
            "trigger_type": "task.created",
            "trigger_filter": {"title": "hello"},
            "steps": [{"type": "create_task", "config": {"title": "hello back"}}],
        },
    )
    await post_json(client, f"{API}/projects/{project['id']}/tasks", headers, {"title": "hello"})

    # Move the recorded event onto the real Redis queue.
    arq_queue = ArqJobQueue(redis)
    for job in queue.jobs:
        await arq_queue.enqueue(job.function, *job.args)
    queue.jobs.clear()

    deps = WorkerDeps(sessionmaker=worker_deps.sessionmaker, queue=arq_queue, backoff_jitter=False)
    worker = Worker(
        functions=[dispatch_event, run_execution],
        redis_pool=redis,
        burst=True,
        poll_delay=0.05,
        handle_signals=False,
        ctx={"deps": deps},
    )
    await worker.main()

    async with deps.sessionmaker() as session:
        executions = list(await session.scalars(select(WorkflowExecutionRow)))
        created = await session.scalar(select(TaskRow).where(TaskRow.title == "hello back"))
    assert [e.status for e in executions] == [ExecutionStatus.SUCCEEDED]
    assert created is not None
    assert worker.jobs_complete >= 2  # dispatch + run (+ the follow-up dispatch)
