"""Cron triggers (issue #2): due workflows run, and never twice for the same time."""

from __future__ import annotations

import asyncio
import dataclasses
from datetime import UTC, datetime, timedelta
from typing import Any

from httpx import AsyncClient
from sqlalchemy import func, select

from flowforge.db.models import TaskRow, WorkflowExecutionRow
from flowforge.domain.workflow import ExecutionStatus
from flowforge.jobs.queue import RecordingJobQueue
from flowforge.worker.runner import WorkerDeps
from flowforge.worker.scheduler import schedule_tick
from tests.integration.conftest import MakeUser, drain
from tests.integration.test_workflows import create_workflow, workspace

API = "/api/v1"


def next_minute_after_now(minutes: int = 1) -> datetime:
    """A clock value safely after the workflow's created_at (which is the real now())."""
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    return now + timedelta(minutes=minutes)


def cron_body(project_id: str, cron: str = "* * * * *") -> dict[str, Any]:
    return {
        "name": "daily standup task",
        "trigger_type": "schedule.cron",
        "trigger_filter": {"cron": cron},
        "steps": [
            {"type": "create_task", "config": {"title": "Standup", "project_id": project_id}}
        ],
    }


def at(deps: WorkerDeps, when: datetime) -> WorkerDeps:
    return dataclasses.replace(deps, clock=lambda: when)


async def scheduled_executions(deps: WorkerDeps) -> list[WorkflowExecutionRow]:
    async with deps.sessionmaker() as s:
        rows = await s.scalars(
            select(WorkflowExecutionRow).where(WorkflowExecutionRow.scheduled_for.is_not(None))
        )
        return list(rows)


async def test_due_cron_workflow_runs_end_to_end(
    client: AsyncClient, make_user: MakeUser, queue: RecordingJobQueue, worker_deps: WorkerDeps
) -> None:
    headers, org_id, project_id = await workspace(client, make_user)
    await create_workflow(client, headers, org_id, cron_body(project_id))
    deps = at(worker_deps, next_minute_after_now() + timedelta(seconds=3))

    created = await schedule_tick({"deps": deps})
    assert len(created) == 1
    await drain(deps, queue)

    [execution] = await scheduled_executions(deps)
    assert execution.status is ExecutionStatus.SUCCEEDED
    assert execution.trigger_event["type"] == "schedule.cron"
    async with deps.sessionmaker() as s:
        titles = list(await s.scalars(select(TaskRow.title)))
    assert titles == ["Standup"]


async def test_two_schedulers_racing_fire_exactly_once(
    client: AsyncClient, make_user: MakeUser, queue: RecordingJobQueue, worker_deps: WorkerDeps
) -> None:
    headers, org_id, project_id = await workspace(client, make_user)
    await create_workflow(client, headers, org_id, cron_body(project_id))
    deps = at(worker_deps, next_minute_after_now())

    # Two "worker processes" tick at the same instant, each with its own DB session.
    results = await asyncio.gather(schedule_tick({"deps": deps}), schedule_tick({"deps": deps}))
    assert sorted(len(r) for r in results) == [0, 1]
    assert len(await scheduled_executions(deps)) == 1
    assert [j.function for j in queue.jobs] == ["run_execution"]


async def test_repeated_ticks_in_the_same_minute_do_not_refire(
    client: AsyncClient, make_user: MakeUser, worker_deps: WorkerDeps
) -> None:
    headers, org_id, project_id = await workspace(client, make_user)
    await create_workflow(client, headers, org_id, cron_body(project_id))
    minute = next_minute_after_now()
    for second in (0, 20, 59):
        await schedule_tick({"deps": at(worker_deps, minute + timedelta(seconds=second))})
    assert len(await scheduled_executions(worker_deps)) == 1
    # the next minute is a new scheduled time, so it fires again
    await schedule_tick({"deps": at(worker_deps, minute + timedelta(minutes=1))})
    assert len(await scheduled_executions(worker_deps)) == 2


async def test_disabled_and_not_due_workflows_do_not_fire(
    client: AsyncClient, make_user: MakeUser, worker_deps: WorkerDeps
) -> None:
    headers, org_id, project_id = await workspace(client, make_user)
    off = await create_workflow(client, headers, org_id, cron_body(project_id))
    r = await client.post(f"{API}/workflows/{off['id']}:disable", headers=headers)
    assert r.status_code == 200, r.text
    # "0 0 1 1 *" = midnight on Jan 1st: essentially never due in the lookback window
    await create_workflow(client, headers, org_id, cron_body(project_id, "0 0 1 1 *"))
    assert await schedule_tick({"deps": at(worker_deps, next_minute_after_now())}) == []


async def test_invalid_schedules_rejected_at_save_time(
    client: AsyncClient, make_user: MakeUser
) -> None:
    headers, org_id, project_id = await workspace(client, make_user)
    bad_bodies = [
        cron_body(project_id, "every monday"),
        {**cron_body(project_id), "trigger_filter": {}},
        {
            **cron_body(project_id),
            "steps": [{"type": "create_task", "config": {"title": "no project"}}],
        },
        {
            **cron_body(project_id),
            "steps": [{"type": "update_field", "config": {"field": "priority", "value": 1}}],
        },
    ]
    for body in bad_bodies:
        r = await client.post(f"{API}/orgs/{org_id}/workflows", json=body, headers=headers)
        assert r.status_code == 422, (body, r.text)
        assert r.json()["error"]["code"] == "invalid_schedule"


async def test_cron_expression_is_normalised(
    client: AsyncClient, make_user: MakeUser, worker_deps: WorkerDeps
) -> None:
    headers, org_id, project_id = await workspace(client, make_user)
    wf = await create_workflow(client, headers, org_id, cron_body(project_id, " */5  *  * * *"))
    assert wf["trigger_filter"] == {"cron": "*/5 * * * *"}
    async with worker_deps.sessionmaker() as s:
        assert await s.scalar(select(func.count()).select_from(WorkflowExecutionRow)) == 0
