"""Integration fixtures: a real Postgres (docker compose, port 55432), migrated with Alembic.

If the database is not reachable the whole integration suite is *skipped* (never faked).
Start it with ``make up``.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import pytest
from alembic.config import Config
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import command
from flowforge.api.app import create_app
from flowforge.config import Settings, get_settings
from flowforge.jobs.queue import RecordingJobQueue
from flowforge.worker.runner import WorkerDeps, dispatch_event, run_execution

TEST_DB_URL = os.environ.get(
    "FLOWFORGE_TEST_DATABASE_URL",
    "postgresql+asyncpg://flowforge:flowforge@localhost:55432/flowforge_test",
)
TABLES_TO_KEEP = {"alembic_version"}


async def _ping(url: str) -> None:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    finally:
        await engine.dispose()


@pytest.fixture(scope="session")
def migrated_db() -> str:
    try:
        asyncio.run(asyncio.wait_for(_ping(TEST_DB_URL), timeout=5))
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Postgres not reachable at {TEST_DB_URL} ({exc}); run `make up`")
    os.environ["FLOWFORGE_DATABASE_URL"] = TEST_DB_URL
    get_settings.cache_clear()
    cfg = Config("alembic.ini")
    command.downgrade(cfg, "base")  # prove downgrades work, then start clean
    command.upgrade(cfg, "head")
    return TEST_DB_URL


@pytest.fixture
def settings(migrated_db: str) -> Settings:
    return Settings(
        database_url=migrated_db, jwt_secret=SecretStr("test-secret-that-is-long-enough-32b")
    )


@pytest.fixture
def queue() -> RecordingJobQueue:
    return RecordingJobQueue()


@pytest.fixture
async def app(settings: Settings, queue: RecordingJobQueue) -> AsyncIterator[FastAPI]:
    application = create_app(settings, pooled=False, queue=queue)
    async with application.router.lifespan_context(application):
        yield application
    await _truncate_all(settings.database_url)


async def _truncate_all(url: str) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        rows = await conn.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        )
        tables = [r[0] for r in rows if r[0] not in TABLES_TO_KEEP]
        if tables:
            await conn.execute(text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE"))
    await engine.dispose()


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


Headers = dict[str, str]
MakeUser = Callable[..., Awaitable[Headers]]


@pytest.fixture
def make_user(client: AsyncClient) -> MakeUser:
    """Register + log in a user; returns auth headers."""
    counter = 0

    async def _make(email: str | None = None, password: str = "correct-horse-battery") -> Headers:
        nonlocal counter
        counter += 1
        email = email or f"user{counter}@example.com"
        r = await client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": password, "full_name": f"User {counter}"},
        )
        assert r.status_code == 201, r.text
        r = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['access_token']}"}

    return _make


async def post_json(client: AsyncClient, url: str, headers: Headers, body: Any) -> Any:
    r = await client.post(url, json=body, headers=headers)
    assert r.status_code in (200, 201), r.text
    return r.json()


@pytest.fixture
def worker_deps(app: FastAPI, queue: RecordingJobQueue) -> WorkerDeps:
    return WorkerDeps(
        sessionmaker=app.state.sessionmaker,
        queue=queue,
        backoff_base=1.0,
        backoff_jitter=False,
        step_timeout=5.0,
    )


JOB_FUNCTIONS: dict[str, Callable[..., Awaitable[Any]]] = {
    "dispatch_event": dispatch_event,
    "run_execution": run_execution,
}


async def drain(
    deps: WorkerDeps, queue: RecordingJobQueue, *, run_deferred: bool = True, max_jobs: int = 200
) -> int:
    """Run queued jobs like a worker would, until the queue is empty. Returns jobs run.

    With ``run_deferred=False`` jobs scheduled for later (retries, delays) are left queued.
    """
    ctx: dict[str, Any] = {"deps": deps}
    ran = 0
    deferred = []
    while (job := queue.pop()) is not None:
        if job.defer_by and not run_deferred:
            deferred.append(job)
            continue
        await JOB_FUNCTIONS[job.function](ctx, *job.args)
        ran += 1
        assert ran < max_jobs, "runaway job loop"
    queue.jobs.extend(deferred)
    return ran
