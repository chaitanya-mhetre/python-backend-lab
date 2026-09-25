from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from flowforge.db.models import Notification, StepRunRow, TaskRow, WorkflowExecutionRow
from flowforge.domain.workflow import ExecutionStatus, StepType
from flowforge.jobs.queue import RecordingJobQueue
from flowforge.worker.runner import WorkerDeps, backoff_delay, run_execution
from flowforge.worker.steps import StepInput, StepResult
from tests.integration.conftest import Headers, MakeUser, drain, post_json

API = "/api/v1"


async def workspace(client: AsyncClient, make_user: MakeUser) -> tuple[Headers, str, str]:
    headers = await make_user()
    org = await post_json(client, f"{API}/orgs", headers, {"name": "Acme"})
    project = await post_json(client, f"{API}/orgs/{org['id']}/projects", headers, {"name": "P"})
    return headers, org["id"], project["id"]


async def create_workflow(
    client: AsyncClient, headers: Headers, org_id: str, body: dict[str, Any]
) -> dict[str, Any]:
    created: dict[str, Any] = await post_json(
        client, f"{API}/orgs/{org_id}/workflows", headers, body
    )
    return created


async def count(deps: WorkerDeps, model: Any, *where: Any) -> int:
    async with deps.sessionmaker() as s:
        return int(await s.scalar(select(func.count()).select_from(model).where(*where)) or 0)


async def executions(deps: WorkerDeps) -> list[WorkflowExecutionRow]:
    async with deps.sessionmaker() as s:
        rows = await s.scalars(
            select(WorkflowExecutionRow).order_by(WorkflowExecutionRow.created_at)
        )
        return list(rows)


async def test_invalid_step_config_rejected(client: AsyncClient, make_user: MakeUser) -> None:
    headers, org_id, _ = await workspace(client, make_user)
    r = await client.post(
        f"{API}/orgs/{org_id}/workflows",
        json={
            "name": "bad",
            "trigger_type": "task.created",
            "steps": [{"type": "delay", "config": {"seconds": -5}}],
        },
        headers=headers,
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_step_config"


async def test_status_change_runs_workflow_end_to_end(
    client: AsyncClient,
    make_user: MakeUser,
    queue: RecordingJobQueue,
    worker_deps: WorkerDeps,
) -> None:
    headers, org_id, project_id = await workspace(client, make_user)
    me = (await client.get(f"{API}/me", headers=headers)).json()
    wf = await create_workflow(
        client,
        headers,
        org_id,
        {
            "name": "follow-up when done",
            "trigger_type": "task.status_changed",
            "trigger_filter": {"to": "done"},
            "steps": [
                {"type": "create_task", "config": {"title": "Retro for {title}"}},
                {
                    "type": "send_notification",
                    "config": {"to": "creator", "message": "{title} done"},
                },
            ],
        },
    )
    task = await post_json(
        client, f"{API}/projects/{project_id}/tasks", headers, {"title": "Launch"}
    )
    for status in ("in_progress", "done"):
        await client.patch(f"{API}/tasks/{task['id']}", json={"status": status}, headers=headers)

    await drain(worker_deps, queue)

    [execution] = await executions(worker_deps)
    assert execution.status is ExecutionStatus.SUCCEEDED
    assert execution.definition_id == uuid.UUID(wf["id"])
    tasks = (await client.get(f"{API}/projects/{project_id}/tasks", headers=headers)).json()
    assert {t["title"] for t in tasks["items"]} == {"Launch", "Retro for Launch"}
    assert await count(worker_deps, Notification, Notification.user_id == uuid.UUID(me["id"])) == 1

    detail = (await client.get(f"{API}/executions/{execution.id}", headers=headers)).json()
    assert [(r["position"], r["status"]) for r in detail["step_runs"]] == [
        (1, "succeeded"),
        (2, "succeeded"),
    ]


def flaky(fail_times: int) -> Any:
    calls = {"n": 0}

    async def handler(step: StepInput) -> StepResult:
        calls["n"] += 1
        if calls["n"] <= fail_times:
            raise RuntimeError(f"transient failure {calls['n']}")
        return StepResult(output={"calls": calls["n"]})

    return handler


async def setup_single_step(
    client: AsyncClient, make_user: MakeUser, queue: RecordingJobQueue, step_type: str = "delay"
) -> tuple[Headers, str, str]:
    headers, org_id, project_id = await workspace(client, make_user)
    config = {"seconds": 1} if step_type == "delay" else {"title": "x"}
    await create_workflow(
        client,
        headers,
        org_id,
        {
            "name": "one step",
            "trigger_type": "task.created",
            "steps": [{"type": step_type, "config": config}],
        },
    )
    await post_json(client, f"{API}/projects/{project_id}/tasks", headers, {"title": "trigger"})
    return headers, org_id, project_id


async def test_transient_failures_retry_with_exponential_backoff(
    client: AsyncClient, make_user: MakeUser, queue: RecordingJobQueue, worker_deps: WorkerDeps
) -> None:
    worker_deps.handlers = {StepType.DELAY: flaky(fail_times=2)}
    await setup_single_step(client, make_user, queue)

    await drain(worker_deps, queue, run_deferred=False)  # first attempt fails
    [retry1] = queue.jobs
    assert retry1.defer_by == 1.0
    await drain(worker_deps, queue, run_deferred=True)

    [execution] = await executions(worker_deps)
    assert execution.status is ExecutionStatus.SUCCEEDED
    async with worker_deps.sessionmaker() as s:
        runs = list(await s.scalars(select(StepRunRow).order_by(StepRunRow.attempt)))
    assert [(r.attempt, r.status) for r in runs] == [
        (1, ExecutionStatus.FAILED),
        (2, ExecutionStatus.FAILED),
        (3, ExecutionStatus.SUCCEEDED),
    ]


def test_backoff_schedule() -> None:
    assert [backoff_delay(a, base=2, jitter=False) for a in (1, 2, 3, 4)] == [2, 4, 8, 16]
    assert backoff_delay(20, base=2, cap=60, jitter=False) == 60
    assert all(0 <= backoff_delay(3, base=2) <= 8 for _ in range(50))


async def test_permanent_failure_fails_execution_after_max_attempts(
    client: AsyncClient, make_user: MakeUser, queue: RecordingJobQueue, worker_deps: WorkerDeps
) -> None:
    worker_deps.handlers = {StepType.DELAY: flaky(fail_times=99)}
    await setup_single_step(client, make_user, queue)
    await drain(worker_deps, queue)
    [execution] = await executions(worker_deps)
    assert execution.status is ExecutionStatus.FAILED
    assert "after 3 attempts" in (execution.error or "")
    assert await count(worker_deps, StepRunRow) == 3


async def test_step_timeout_counts_as_failure(
    client: AsyncClient, make_user: MakeUser, queue: RecordingJobQueue, worker_deps: WorkerDeps
) -> None:
    async def slow(step: StepInput) -> StepResult:
        await asyncio.sleep(10)
        return StepResult()

    worker_deps.handlers = {StepType.DELAY: slow}
    worker_deps.step_timeout = 0.05
    worker_deps.max_attempts = 1
    await setup_single_step(client, make_user, queue)
    await drain(worker_deps, queue)
    [execution] = await executions(worker_deps)
    assert execution.status is ExecutionStatus.FAILED
    assert "timed out" in (execution.error or "")


class SimulatedCrash(BaseException):
    """BaseException, like KeyboardInterrupt/SystemExit: not caught by `except Exception`."""


async def test_worker_crash_mid_execution_resumes_without_duplicates(
    client: AsyncClient, make_user: MakeUser, queue: RecordingJobQueue, worker_deps: WorkerDeps
) -> None:
    headers, org_id, project_id = await workspace(client, make_user)
    await create_workflow(
        client,
        headers,
        org_id,
        {
            "name": "two steps",
            "trigger_type": "task.status_changed",
            "steps": [
                {"type": "create_task", "config": {"title": "created by step 1"}},
                {"type": "delay", "config": {"seconds": 1}},
            ],
        },
    )
    crashed = {"done": False}

    async def crash_once(step: StepInput) -> StepResult:
        if not crashed["done"]:
            crashed["done"] = True
            raise SimulatedCrash
        return StepResult(output={"ok": True})

    handlers = dict(worker_deps.handlers)
    handlers[StepType.DELAY] = crash_once
    worker_deps.handlers = handlers
    task = await post_json(client, f"{API}/projects/{project_id}/tasks", headers, {"title": "t"})
    await client.patch(f"{API}/tasks/{task['id']}", json={"status": "in_progress"}, headers=headers)

    with pytest.raises(SimulatedCrash):
        await drain(worker_deps, queue)  # step 1 commits, step 2 "kills the worker"

    [execution] = await executions(worker_deps)
    assert execution.status is ExecutionStatus.RUNNING
    # The job is gone with the crashed worker. An operator/requeue runs it again:
    await run_execution({"deps": worker_deps}, str(execution.id))
    await drain(worker_deps, queue)

    [execution] = await executions(worker_deps)
    assert execution.status is ExecutionStatus.SUCCEEDED
    assert await count(worker_deps, TaskRow, TaskRow.title == "created by step 1") == 1


async def test_workflow_loop_is_cut_off(
    client: AsyncClient, make_user: MakeUser, queue: RecordingJobQueue, worker_deps: WorkerDeps
) -> None:
    await setup_single_step(client, make_user, queue, step_type="create_task")
    await drain(worker_deps, queue)
    runs = await executions(worker_deps)
    depths = [r.trigger_event["depth"] for r in runs]
    assert depths == [0, 1, 2, 3, 4, 5, 6]
    assert runs[-1].status is ExecutionStatus.FAILED
    assert runs[-1].error_code == "workflow_loop_detected"


async def test_cancel_before_run(
    client: AsyncClient, make_user: MakeUser, queue: RecordingJobQueue, worker_deps: WorkerDeps
) -> None:
    headers, org_id, project_id = await workspace(client, make_user)
    await create_workflow(
        client,
        headers,
        org_id,
        {
            "name": "cancel me",
            "trigger_type": "task.created",
            "steps": [{"type": "create_task", "config": {"title": "never"}}],
        },
    )
    await post_json(client, f"{API}/projects/{project_id}/tasks", headers, {"title": "go"})
    dispatch = queue.pop()
    assert dispatch is not None
    await drain_one(worker_deps, dispatch.function, dispatch.args)
    pending = [e for e in await executions(worker_deps) if e.status is ExecutionStatus.PENDING]
    [target] = pending

    r = await client.post(f"{API}/executions/{target.id}:cancel", headers=headers)
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    await drain(worker_deps, queue)
    assert await count(worker_deps, TaskRow, TaskRow.title == "never") == 0
    r = await client.post(f"{API}/executions/{target.id}:cancel", headers=headers)
    assert r.status_code == 409


async def drain_one(deps: WorkerDeps, function: str, args: tuple[Any, ...]) -> None:
    from tests.integration.conftest import JOB_FUNCTIONS

    await JOB_FUNCTIONS[function]({"deps": deps}, *args)


async def test_delay_step_defers_the_rest_of_the_execution(
    client: AsyncClient, make_user: MakeUser, queue: RecordingJobQueue, worker_deps: WorkerDeps
) -> None:
    headers, org_id, project_id = await workspace(client, make_user)
    await create_workflow(
        client,
        headers,
        org_id,
        {
            "name": "remind later",
            "trigger_type": "task.status_changed",
            "steps": [
                {"type": "delay", "config": {"seconds": 3600}},
                {"type": "create_task", "config": {"title": "follow up"}},
            ],
        },
    )
    task = await post_json(client, f"{API}/projects/{project_id}/tasks", headers, {"title": "t"})
    await client.patch(f"{API}/tasks/{task['id']}", json={"status": "in_progress"}, headers=headers)

    await drain(worker_deps, queue, run_deferred=False)
    [execution] = await executions(worker_deps)
    assert execution.status is ExecutionStatus.RUNNING
    assert [j.defer_by for j in queue.jobs] == [3600]

    await drain(worker_deps, queue)
    [execution] = await executions(worker_deps)
    assert execution.status is ExecutionStatus.SUCCEEDED
    assert await count(worker_deps, TaskRow, TaskRow.title == "follow up") == 1


async def test_editing_workflow_does_not_change_pinned_execution(
    client: AsyncClient, make_user: MakeUser, queue: RecordingJobQueue, worker_deps: WorkerDeps
) -> None:
    headers, org_id, project_id = await workspace(client, make_user)
    wf = await create_workflow(
        client,
        headers,
        org_id,
        {
            "name": "v1",
            "trigger_type": "task.status_changed",
            "steps": [{"type": "create_task", "config": {"title": "from v1"}}],
        },
    )
    task = await post_json(client, f"{API}/projects/{project_id}/tasks", headers, {"title": "t"})
    await drain(worker_deps, queue)  # task.created matches nothing
    await client.patch(f"{API}/tasks/{task['id']}", json={"status": "in_progress"}, headers=headers)
    dispatch = queue.pop()
    assert dispatch is not None
    await drain_one(worker_deps, dispatch.function, dispatch.args)  # execution pinned to v1

    r = await client.put(
        f"{API}/workflows/{wf['id']}",
        json={
            "name": "v2",
            "trigger_type": "task.status_changed",
            "steps": [{"type": "create_task", "config": {"title": "from v2"}}],
        },
        headers=headers,
    )
    assert r.json()["version"] == 2
    await drain(worker_deps, queue)

    [execution] = await executions(worker_deps)
    assert execution.definition_version == 1
    assert await count(worker_deps, TaskRow, TaskRow.title == "from v1") == 1
    assert await count(worker_deps, TaskRow, TaskRow.title == "from v2") == 0


async def test_per_org_concurrency_limit(
    client: AsyncClient, make_user: MakeUser, queue: RecordingJobQueue, worker_deps: WorkerDeps
) -> None:
    running, peak = 0, 0

    async def slow(step: StepInput) -> StepResult:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.05)
        running -= 1
        return StepResult()

    worker_deps.handlers = {StepType.DELAY: slow}
    worker_deps.per_org_concurrency = 2
    worker_deps.__post_init__()
    headers, org_id, project_id = await workspace(client, make_user)
    await create_workflow(
        client,
        headers,
        org_id,
        {
            "name": "slow",
            "trigger_type": "task.created",
            "steps": [{"type": "delay", "config": {"seconds": 1}}],
        },
    )
    for i in range(6):
        await post_json(client, f"{API}/projects/{project_id}/tasks", headers, {"title": f"t{i}"})
    dispatches = list(queue.jobs)  # dispatch all six events first
    queue.jobs.clear()
    for job in dispatches:
        await drain_one(worker_deps, job.function, job.args)
    run_jobs = list(queue.jobs)
    queue.jobs.clear()

    await asyncio.gather(*(drain_one(worker_deps, j.function, j.args) for j in run_jobs))

    assert len(run_jobs) == 6
    assert peak == 2
