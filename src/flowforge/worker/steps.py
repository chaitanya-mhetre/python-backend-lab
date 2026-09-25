"""Step handlers used by the background worker.

A handler runs *inside the worker's transaction* for that step, together with the
``step_runs`` row that records its success. So "task created" and "step recorded as done"
commit atomically: a crash can never leave a created task without its step marked done,
which is what makes re-running an execution after a crash safe (no duplicate tasks).
"""

from __future__ import annotations

import string
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from flowforge.db.models import Notification, Project, TaskRow, WorkflowExecutionRow
from flowforge.domain.errors import ConflictError, NotFoundError
from flowforge.domain.task import Task, TaskStatus
from flowforge.domain.workflow import Event, StepType, TriggerType
from flowforge.repositories.audit import AuditRepository
from flowforge.repositories.tasks import TaskRepository


@dataclass
class StepInput:
    session: AsyncSession
    execution: WorkflowExecutionRow
    event: Event
    actor_user_id: uuid.UUID  # the workflow's creator; used as created_by for new tasks
    config: Mapping[str, Any]


@dataclass
class StepResult:
    output: dict[str, Any] = field(default_factory=dict)
    emitted: list[Event] = field(default_factory=list)
    defer_seconds: float | None = None


class AsyncStepHandler(Protocol):
    async def __call__(self, step: StepInput) -> StepResult: ...


class _SafeFormat(dict[str, Any]):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def render(template: str, payload: Mapping[str, Any]) -> str:
    """``"Task {title} done"`` -> filled from the event payload; unknown keys stay as-is."""
    return string.Formatter().vformat(template, (), _SafeFormat(payload))


async def create_task(step: StepInput) -> StepResult:
    raw_project = step.config.get("project_id") or step.event.payload.get("project_id")
    if raw_project is None:
        raise ConflictError("no project_id in step config or triggering event")
    project = await step.session.get(Project, uuid.UUID(str(raw_project)))
    if project is None or project.org_id != step.execution.org_id:
        raise NotFoundError("project", raw_project)
    task = Task(project_id=project.id, title=render(str(step.config["title"]), step.event.payload))
    row = await TaskRepository(step.session).add(
        TaskRow(
            id=task.id,
            project_id=project.id,
            title=task.title,
            status=task.status,
            priority=task.priority,
            created_by=step.actor_user_id,
            version=0,
        )
    )
    AuditRepository(step.session).record(
        org_id=project.org_id,
        action="task.created",
        entity_type="task",
        entity_id=row.id,
        after={"title": row.title, "via_execution": str(step.execution.id)},
    )
    payload = {"task_id": str(row.id), "project_id": str(project.id), "title": row.title}
    return StepResult(
        output={"task_id": str(row.id)},
        emitted=[step.event.child(TriggerType.TASK_CREATED, payload)],
    )


async def update_field(step: StepInput) -> StepResult:
    task_id = uuid.UUID(str(step.event.payload["task_id"]))
    repo = TaskRepository(step.session)
    row = await repo.get(task_id)
    if row is None:
        raise NotFoundError("task", task_id)
    field_name, value = str(step.config["field"]), step.config["value"]
    emitted: list[Event] = []
    if field_name == "status":
        domain = Task(project_id=row.project_id, title=row.title, status=row.status)
        old = domain.status
        domain.transition_to(TaskStatus(str(value)))
        values: dict[str, Any] = {"status": domain.status}
        emitted.append(
            step.event.child(
                TriggerType.TASK_STATUS_CHANGED,
                {
                    "task_id": str(task_id),
                    "project_id": str(row.project_id),
                    "from": old.value,
                    "to": domain.status.value,
                },
            )
        )
    else:
        values = {"priority": int(value)}
        Task(project_id=row.project_id, title=row.title, priority=values["priority"])
    if await repo.update_if_version(task_id, row.version, values) is None:
        raise ConflictError("task changed concurrently; will retry")
    return StepResult(output={"field": field_name, "value": value}, emitted=emitted)


async def send_notification(step: StepInput) -> StepResult:
    target = step.config["to"]
    if target in ("assignee", "creator"):
        task_id = step.event.payload.get("task_id")
        row = await step.session.get(TaskRow, uuid.UUID(str(task_id))) if task_id else None
        user_id = (
            None if row is None else (row.assignee_id if target == "assignee" else row.created_by)
        )
        if user_id is None:
            return StepResult(output={"skipped": f"no {target} to notify"})
    else:
        user_id = uuid.UUID(str(target))
    message = render(str(step.config["message"]), step.event.payload)
    step.session.add(
        Notification(
            user_id=user_id,
            org_id=step.execution.org_id,
            kind="workflow",
            payload={"message": message, "execution_id": str(step.execution.id)},
        )
    )
    return StepResult(output={"user_id": str(user_id), "message": message})


async def delay(step: StepInput) -> StepResult:
    # No sleeping inside a worker slot: the runner re-enqueues the execution with a defer.
    seconds = float(step.config["seconds"])
    return StepResult(output={"delayed_seconds": seconds}, defer_seconds=seconds)


DEFAULT_HANDLERS: dict[StepType, AsyncStepHandler] = {
    StepType.CREATE_TASK: create_task,
    StepType.UPDATE_FIELD: update_field,
    StepType.SEND_NOTIFICATION: send_notification,
    StepType.DELAY: delay,
}
