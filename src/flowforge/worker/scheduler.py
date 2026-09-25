"""Cron scheduler: turns ``schedule.cron`` workflows into executions.

arq runs :func:`schedule_tick` once a minute on every worker process. Several workers means
several schedulers racing, so correctness cannot depend on "only one scheduler runs".
Instead the database decides: an execution row is inserted with ``ON CONFLICT DO NOTHING``
against a unique index on ``(definition_id, scheduled_for)``. Whoever inserts first wins; the
others insert nothing and enqueue nothing. That index is the lock, and it survives crashes,
restarts and clock skew between workers (they all compute the same ``scheduled_for``).
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert

from flowforge.db.models import WorkflowExecutionRow
from flowforge.db.session import session_scope
from flowforge.domain.schedule import due_fire_time
from flowforge.domain.workflow import Event, ExecutionStatus, TriggerType, event_to_dict
from flowforge.repositories.workflows import WorkflowRepository, to_domain
from flowforge.worker.runner import WorkerDeps

log = structlog.get_logger("flowforge.scheduler")

DEFAULT_LOOKBACK = timedelta(minutes=5)


async def schedule_tick(ctx: dict[str, Any]) -> list[str]:
    """Create (at most) one execution per due cron workflow. Returns the ids created here."""
    deps: WorkerDeps = ctx["deps"]
    lookback: timedelta = ctx.get("schedule_lookback", DEFAULT_LOOKBACK)
    now = deps.clock()
    created: list[uuid.UUID] = []
    async with session_scope(deps.sessionmaker) as session:
        for row in await WorkflowRepository(session).enabled_scheduled():
            cron = str(row.trigger_filter.get("cron", ""))
            fire_at = due_fire_time(cron, now=now, not_before=row.created_at, lookback=lookback)
            if fire_at is None:
                continue
            definition = to_domain(row)
            event = Event(
                type=TriggerType.SCHEDULE_CRON,
                org_id=row.org_id,
                payload={"cron": cron, "scheduled_for": fire_at.isoformat()},
            )
            execution_id = uuid.uuid4()
            inserted = await session.scalar(
                insert(WorkflowExecutionRow)
                .values(
                    id=execution_id,
                    definition_id=row.id,
                    org_id=row.org_id,
                    definition_version=row.version,
                    steps_snapshot=[
                        {"position": s.position, "type": s.type.value, "config": dict(s.config)}
                        for s in definition.ordered_steps
                    ],
                    trigger_event=event_to_dict(event),
                    scheduled_for=fire_at,
                    status=ExecutionStatus.PENDING,
                )
                .on_conflict_do_nothing(
                    index_elements=["definition_id", "scheduled_for"],
                    index_where=text("scheduled_for IS NOT NULL"),
                )
                .returning(WorkflowExecutionRow.id)
            )
            if inserted is not None:
                created.append(inserted)
                log.info("cron_fired", workflow_id=str(row.id), scheduled_for=fire_at.isoformat())

    # After commit, like dispatch_event: a crash here leaves a pending row that
    # scripts/requeue_pending.py picks up, never a duplicate run.
    for execution_id in created:
        await deps.queue.enqueue(
            "run_execution", str(execution_id), job_id=f"exec:{execution_id}:start"
        )
    return [str(e) for e in created]
