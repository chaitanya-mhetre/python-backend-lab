"""Background execution of workflows (arq job functions + the logic they call).

Two jobs:

``dispatch_event(event)``
    Find enabled workflows matching the event, create one ``workflow_executions`` row per
    match (pinning the definition version and a snapshot of its steps), commit, *then*
    enqueue ``run_execution`` for each.

``run_execution(execution_id)``
    Run the next unfinished step, one step per transaction:
      lock execution row -> skip if cancelled/finished -> find first step without a
      successful step_run -> run handler inside a SAVEPOINT (with a timeout) ->
      record step_run -> commit -> publish emitted events -> next step.
    Failure: record the failed attempt; retry later with exponential backoff (re-enqueue with
    a defer) until ``max_attempts``, then fail the execution.

Crash safety: each step's side effects and its step_run commit together. If the worker dies,
the uncommitted step is rolled back and simply runs again on the next attempt; committed
steps are skipped. The partial unique index on step_runs(status='succeeded') backs this up.

Known trade-off (documented in docs/adr/0004): events are enqueued *after* the DB commit.
If the process dies between commit and enqueue, that event is lost. production-fastapi fixes
this with a transactional outbox.
"""

from __future__ import annotations

import asyncio
import random
import time
import uuid
from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from flowforge.db.models import StepRunRow, WorkflowDefinitionRow, WorkflowExecutionRow
from flowforge.db.session import session_scope
from flowforge.domain.engine import DEFAULT_MAX_DEPTH
from flowforge.domain.errors import DomainError, WorkflowLoopError
from flowforge.domain.workflow import (
    Event,
    ExecutionStatus,
    StepType,
    event_from_dict,
    event_to_dict,
)
from flowforge.jobs.queue import JobQueue
from flowforge.observability.metrics import STEP_DURATION, WORKFLOW_EXECUTIONS
from flowforge.repositories.workflows import ExecutionRepository, WorkflowRepository, to_domain
from flowforge.security.webhooks import Resolver, SecretBox, system_resolver
from flowforge.worker.deliveries import create_deliveries
from flowforge.worker.steps import DEFAULT_HANDLERS, AsyncStepHandler, StepInput, StepResult

log = structlog.get_logger("flowforge.worker")

TERMINAL = frozenset({ExecutionStatus.SUCCEEDED, ExecutionStatus.FAILED, ExecutionStatus.CANCELLED})


def backoff_delay(attempt: int, *, base: float, cap: float = 600.0, jitter: bool = True) -> float:
    """Delay before retry number ``attempt`` (1-based): base * 2^(attempt-1), capped.

    "Full jitter" (random between 0 and the delay) spreads retries out so that many
    executions failing at once don't all hammer the dependency again at the same instant.
    """
    delay = min(cap, base * 2 ** (attempt - 1))
    return random.uniform(0, delay) if jitter else delay


@dataclass
class WorkerDeps:
    sessionmaker: async_sessionmaker[AsyncSession]
    queue: JobQueue
    handlers: Mapping[StepType, AsyncStepHandler] = field(
        default_factory=lambda: dict(DEFAULT_HANDLERS)
    )
    max_depth: int = DEFAULT_MAX_DEPTH
    max_attempts: int = 3
    step_timeout: float = 30.0
    backoff_base: float = 5.0
    backoff_jitter: bool = True
    per_org_concurrency: int = 4
    # webhook delivery
    secret_box: SecretBox | None = None
    resolver: Resolver = system_resolver
    allow_private_targets: bool = False
    http_client: Callable[[], httpx.AsyncClient] = lambda: httpx.AsyncClient(timeout=10.0)
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    _org_limits: defaultdict[uuid.UUID, asyncio.Semaphore] = field(init=False)

    def __post_init__(self) -> None:
        self._org_limits = defaultdict(lambda: asyncio.Semaphore(self.per_org_concurrency))

    def org_limit(self, org_id: uuid.UUID) -> asyncio.Semaphore:
        """One noisy org can't take every worker slot: at most N of its executions run at once."""
        return self._org_limits[org_id]


async def publish_events(queue: JobQueue, events: list[Event]) -> None:
    if not events:
        return
    async with asyncio.TaskGroup() as tg:  # enqueue concurrently; any failure cancels the rest
        for event in events:
            tg.create_task(queue.enqueue("dispatch_event", event_to_dict(event)))


# --- dispatch ---------------------------------------------------------------------------


async def dispatch_event(ctx: dict[str, Any], event_data: dict[str, Any]) -> list[str]:
    deps: WorkerDeps = ctx["deps"]
    event = event_from_dict(event_data)
    created: list[WorkflowExecutionRow] = []
    async with session_scope(deps.sessionmaker) as session:
        definitions = await WorkflowRepository(session).enabled_for_trigger(
            event.org_id, event.type
        )
        for row in definitions:
            definition = to_domain(row)
            if not definition.matches(event):
                continue
            execution = WorkflowExecutionRow(
                definition_id=row.id,
                org_id=row.org_id,
                definition_version=row.version,
                steps_snapshot=[
                    {"position": s.position, "type": s.type.value, "config": dict(s.config)}
                    for s in definition.ordered_steps
                ],
                trigger_event=event_to_dict(event),
                status=ExecutionStatus.PENDING,
            )
            if event.depth > deps.max_depth:
                err = WorkflowLoopError(event.depth, deps.max_depth)
                execution.status = ExecutionStatus.FAILED
                execution.error, execution.error_code = str(err), err.code
                execution.finished_at = datetime.now(UTC)
            session.add(execution)
            created.append(execution)
        # Only first-hand events go to webhook subscribers; workflow-caused cascades don't,
        # so a loop can't flood a customer's endpoint.
        deliveries = (
            await create_deliveries(session, event.org_id, event.type.value, dict(event.payload))
            if event.depth == 0
            else []
        )

    for delivery in deliveries:
        await deps.queue.enqueue("deliver_webhook", str(delivery.id), job_id=f"whd:{delivery.id}:0")
    for execution in created:
        if execution.status is ExecutionStatus.PENDING:
            await deps.queue.enqueue(
                "run_execution", str(execution.id), job_id=f"exec:{execution.id}:start"
            )
    return [str(e.id) for e in created]


# --- run ----------------------------------------------------------------------------------


@dataclass
class _StepOutcome:
    done: bool  # execution reached a terminal state (or nothing left to do in this job)
    events: list[Event] = field(default_factory=list)
    jobs: list[tuple[str, tuple[Any, ...]]] = field(default_factory=list)


async def run_execution(ctx: dict[str, Any], execution_id: str) -> str:
    deps: WorkerDeps = ctx["deps"]
    exec_uuid = uuid.UUID(execution_id)
    async with deps.sessionmaker() as session:
        row = await session.get(WorkflowExecutionRow, exec_uuid)
        if row is None:
            return "missing"
        org_id = row.org_id

    async with deps.org_limit(org_id):
        while True:
            outcome = await _run_next_step(deps, exec_uuid)
            await publish_events(deps.queue, outcome.events)  # after commit (see module doc)
            for function, args in outcome.jobs:
                await deps.queue.enqueue(function, *args)
            if outcome.done:
                break
    async with deps.sessionmaker() as session:
        final = await session.get(WorkflowExecutionRow, exec_uuid)
        return final.status.value if final else "missing"


async def _run_next_step(deps: WorkerDeps, execution_id: uuid.UUID) -> _StepOutcome:
    async with session_scope(deps.sessionmaker) as session:
        executions = ExecutionRepository(session)
        # Row lock: two workers can never run steps of the same execution concurrently.
        execution = await executions.get(execution_id, for_update=True)
        if execution is None or execution.status in TERMINAL:
            return _StepOutcome(done=True)
        now = datetime.now(UTC)
        if execution.status is ExecutionStatus.PENDING:
            execution.status, execution.started_at = ExecutionStatus.RUNNING, now

        runs = await executions.step_runs(execution_id)
        succeeded = {r.position for r in runs if r.status is ExecutionStatus.SUCCEEDED}
        pending = [s for s in execution.steps_snapshot if s["position"] not in succeeded]
        if not pending:
            execution.status, execution.finished_at = ExecutionStatus.SUCCEEDED, now
            WORKFLOW_EXECUTIONS.labels("succeeded").inc()
            return _StepOutcome(done=True)

        step = pending[0]
        position, step_type = int(step["position"]), StepType(step["type"])
        attempt = 1 + sum(1 for r in runs if r.position == position)
        definition = await session.get(WorkflowDefinitionRow, execution.definition_id)
        assert definition is not None
        step_input = StepInput(
            session=session,
            execution=execution,
            event=event_from_dict(execution.trigger_event),
            actor_user_id=definition.created_by,
            config=step["config"],
        )

        started = time.perf_counter()
        try:
            result = await _call_handler(deps, session, step_type, step_input)
        except Exception as exc:  # noqa: BLE001 - every handler failure is recorded
            STEP_DURATION.labels(step_type.value, "failed").observe(time.perf_counter() - started)
            return await _record_failure(
                deps, session, execution, position, step_type, attempt, exc
            )
        STEP_DURATION.labels(step_type.value, "succeeded").observe(time.perf_counter() - started)

        session.add(
            StepRunRow(
                execution_id=execution_id,
                position=position,
                step_type=step_type,
                attempt=attempt,
                status=ExecutionStatus.SUCCEEDED,
                output=result.output,
                finished_at=datetime.now(UTC),
            )
        )
        if result.defer_seconds is not None and len(pending) > 1:
            await deps.queue.enqueue(
                "run_execution",
                str(execution_id),
                job_id=f"exec:{execution_id}:after:{position}",
                defer_by=result.defer_seconds,
            )
            return _StepOutcome(done=True, events=result.emitted, jobs=result.after_commit_jobs)
        return _StepOutcome(done=False, events=result.emitted, jobs=result.after_commit_jobs)


async def _call_handler(
    deps: WorkerDeps, session: AsyncSession, step_type: StepType, step_input: StepInput
) -> StepResult:
    handler = deps.handlers.get(step_type)
    if handler is None:
        raise DomainError(f"no handler registered for step type {step_type.value!r}")
    # SAVEPOINT: if the handler fails half-way, only its own writes are rolled back and the
    # outer transaction stays usable for recording the failed attempt.
    async with session.begin_nested():
        async with asyncio.timeout(deps.step_timeout):
            return await handler(step_input)


async def _record_failure(
    deps: WorkerDeps,
    session: AsyncSession,
    execution: WorkflowExecutionRow,
    position: int,
    step_type: StepType,
    attempt: int,
    exc: Exception,
) -> _StepOutcome:
    message = "step timed out" if isinstance(exc, TimeoutError) else str(exc) or type(exc).__name__
    now = datetime.now(UTC)
    session.add(
        StepRunRow(
            execution_id=execution.id,
            position=position,
            step_type=step_type,
            attempt=attempt,
            status=ExecutionStatus.FAILED,
            error=message,
            finished_at=now,
        )
    )
    log.warning(
        "workflow_step_failed",
        execution_id=str(execution.id),
        position=position,
        attempt=attempt,
        error=message,
    )
    if attempt >= deps.max_attempts:
        execution.status, execution.finished_at = ExecutionStatus.FAILED, now
        execution.error = f"step {position} failed after {attempt} attempts: {message}"
        execution.error_code = exc.code if isinstance(exc, DomainError) else "step_error"
        WORKFLOW_EXECUTIONS.labels("failed").inc()
        return _StepOutcome(done=True)
    await deps.queue.enqueue(
        "run_execution",
        str(execution.id),
        job_id=f"exec:{execution.id}:retry:{position}:{attempt}",
        defer_by=backoff_delay(attempt, base=deps.backoff_base, jitter=deps.backoff_jitter),
    )
    return _StepOutcome(done=True)


async def pending_executions(session: AsyncSession) -> list[uuid.UUID]:
    """For an operator 'resume stuck executions' script after an outage."""
    rows = await session.scalars(
        select(WorkflowExecutionRow.id).where(
            WorkflowExecutionRow.status.in_([ExecutionStatus.PENDING, ExecutionStatus.RUNNING])
        )
    )
    return list(rows)
