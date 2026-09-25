"""Measure task-list latency with and without the composite (project_id, created_at, id) index.

Seeds N tasks into one project (generate_series, fast), then times the repository's keyset
query many times. The "without index" run drops the index *inside a transaction* and rolls
back afterwards: Postgres DDL is transactional, so nothing is permanently changed.

    uv run python scripts/bench_tasks.py --rows 100000 --runs 200

Writes a markdown report to docs/benchmarks/. Numbers depend on the machine; the report
records the environment so results are reproducible and never quoted without context.
"""

from __future__ import annotations

import argparse
import asyncio
import platform
import statistics
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from flowforge.config import get_settings
from flowforge.repositories.tasks import TaskFilter, TaskRepository

SEED_SQL = """
WITH u AS (
  INSERT INTO users (id, email, password_hash, full_name)
  VALUES (:user_id, :email, 'x', 'bench') RETURNING id
), o AS (
  INSERT INTO organizations (id, name, slug) VALUES (:org_id, 'bench', :slug) RETURNING id
)
INSERT INTO projects (id, org_id, name) SELECT :project_id, o.id, 'bench' FROM o;
"""

TASKS_SQL = """
INSERT INTO tasks (id, project_id, title, status, priority, created_by, created_at, updated_at, version)
SELECT gen_random_uuid(), :project_id, 'task ' || g,
       (ARRAY['todo','in_progress','done','cancelled'])[1 + g % 4]::task_status,
       1 + g % 5, :user_id,
       now() - (g || ' seconds')::interval, now(), 0
FROM generate_series(1, :rows) AS g;
"""


async def time_queries(session: AsyncSession, project_id: uuid.UUID, runs: int) -> list[float]:
    repo = TaskRepository(session)
    samples = []
    for _ in range(runs):
        start = time.perf_counter()
        page = await repo.list(
            TaskFilter(project_id=project_id), sort="-created_at", limit=50, cursor=None
        )
        if page.next_cursor:  # second page too: exercises the keyset predicate
            await repo.list(
                TaskFilter(project_id=project_id),
                sort="-created_at",
                limit=50,
                cursor=page.next_cursor,
            )
        samples.append((time.perf_counter() - start) * 1000)
    return samples


def summary(samples: list[float]) -> str:
    q = statistics.quantiles(samples, n=100)
    return f"p50 {q[49]:.2f} ms · p95 {q[94]:.2f} ms · mean {statistics.mean(samples):.2f} ms"


async def main(rows: int, runs: int) -> None:
    engine = create_async_engine(get_settings().database_url)
    user_id, org_id, project_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with engine.begin() as conn:
        await conn.execute(
            text(SEED_SQL),
            {
                "user_id": user_id,
                "email": f"bench-{user_id}@example.com",
                "org_id": org_id,
                "slug": f"bench-{org_id}",
                "project_id": project_id,
            },
        )
        await conn.execute(
            text(TASKS_SQL), {"project_id": project_id, "user_id": user_id, "rows": rows}
        )
        await conn.execute(text("ANALYZE tasks"))

    try:
        async with AsyncSession(engine) as session:
            await time_queries(session, project_id, 10)  # warm-up
            with_index = await time_queries(session, project_id, runs)

        async with engine.connect() as conn:
            trans = await conn.begin()
            await conn.execute(text("DROP INDEX ix_tasks_project_created"))
            await conn.execute(
                text("DROP INDEX ix_tasks_project_status")
            )  # would otherwise be used
            session = AsyncSession(bind=conn)
            await time_queries(session, project_id, 5)
            without_index = await time_queries(session, project_id, runs)
            await trans.rollback()  # indexes come back
    finally:
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM organizations WHERE id = :id"), {"id": org_id})
            await conn.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})
        await engine.dispose()

    report = Path("docs/benchmarks") / f"task-list-{datetime.now(UTC):%Y%m%d}.md"
    report.write_text(
        f"""# Task list latency benchmark

- Date: {datetime.now(UTC):%Y-%m-%d %H:%M} UTC
- Machine: {platform.platform()}, Python {platform.python_version()}
- Database: PostgreSQL 16 in Docker on the same machine (docker compose, default settings)
- Data: {rows:,} tasks in one project · each sample = first page + second page (limit 50, sort -created_at)
- Runs: {runs} samples per variant after warm-up
- Command: `uv run python scripts/bench_tasks.py --rows {rows} --runs {runs}`

| Variant | Latency |
|---|---|
| With `(project_id, created_at, id)` index | {summary(with_index)} |
| Without it (and without `(project_id, status)`) | {summary(without_index)} |

Measured on a developer laptop, not production hardware. Use it for the *relative* difference.
"""
    )
    print(report.read_text())


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, default=100_000)
    parser.add_argument("--runs", type=int, default=200)
    args = parser.parse_args()
    asyncio.run(main(args.rows, args.runs))
