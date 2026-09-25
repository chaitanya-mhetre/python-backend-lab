"""Recovery after an outage: re-enqueue work the database says is unfinished.

Because events are enqueued *after* commit, a crash in between can leave an execution
'pending' with no job in Redis (or a webhook delivery 'pending'). The database is the source
of truth, so this script simply asks it what is unfinished and enqueues jobs again. The job
functions are idempotent (row locks + skip-if-done), so re-running something that was
actually still queued is harmless.

    uv run python scripts/requeue_pending.py [--older-than-minutes 5]
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime, timedelta

from arq import create_pool
from arq.connections import RedisSettings
from sqlalchemy import select

from flowforge.config import get_settings
from flowforge.db.models import DeliveryStatus, WebhookDelivery, WorkflowExecutionRow
from flowforge.db.session import make_engine, make_sessionmaker
from flowforge.domain.workflow import ExecutionStatus
from flowforge.jobs.queue import ArqJobQueue


async def main(older_than: timedelta) -> None:
    settings = get_settings()
    engine = make_engine(settings.database_url, pooled=False)
    redis = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    queue = ArqJobQueue(redis)
    cutoff = datetime.now(UTC) - older_than
    async with make_sessionmaker(engine)() as session:
        executions = await session.scalars(
            select(WorkflowExecutionRow.id).where(
                WorkflowExecutionRow.status.in_([ExecutionStatus.PENDING, ExecutionStatus.RUNNING]),
                WorkflowExecutionRow.created_at < cutoff,
            )
        )
        deliveries = await session.scalars(
            select(WebhookDelivery.id).where(
                WebhookDelivery.status == DeliveryStatus.PENDING,
                WebhookDelivery.created_at < cutoff,
                (WebhookDelivery.next_retry_at.is_(None))
                | (WebhookDelivery.next_retry_at < datetime.now(UTC)),
            )
        )
        exec_ids, delivery_ids = list(executions), list(deliveries)
    for execution_id in exec_ids:
        await queue.enqueue("run_execution", str(execution_id))
    for delivery_id in delivery_ids:
        await queue.enqueue("deliver_webhook", str(delivery_id))
    print(f"requeued {len(exec_ids)} executions and {len(delivery_ids)} webhook deliveries")
    await redis.aclose()
    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--older-than-minutes", type=int, default=5)
    asyncio.run(main(timedelta(minutes=parser.parse_args().older_than_minutes)))
