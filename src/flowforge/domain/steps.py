"""Built-in step handlers for the in-memory (M1) engine.

Each handler is a small class with ``__call__`` so it can hold dependencies (a repository,
a notifier) while still matching the ``StepHandler`` protocol.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from flowforge.domain.engine import StepContext
from flowforge.domain.repository import InMemoryRepository
from flowforge.domain.task import Task, TaskStatus
from flowforge.domain.workflow import Step, TriggerType


class CreateTaskStep:
    def __init__(self, tasks: InMemoryRepository[Task]) -> None:
        self._tasks = tasks

    def __call__(self, step: Step, ctx: StepContext) -> Mapping[str, Any]:
        project_id = UUID(str(step.config.get("project_id", ctx.event.payload["project_id"])))
        task = Task(project_id=project_id, title=str(step.config["title"]))
        self._tasks.add(task)
        ctx.emit(
            TriggerType.TASK_CREATED,
            {"task_id": str(task.id), "project_id": str(project_id)},
        )
        return {"task_id": str(task.id)}


class UpdateFieldStep:
    """Sets ``status`` (through the state machine) or ``priority`` on the triggering task."""

    def __init__(self, tasks: InMemoryRepository[Task]) -> None:
        self._tasks = tasks

    def __call__(self, step: Step, ctx: StepContext) -> Mapping[str, Any]:
        task = self._tasks.get(UUID(str(ctx.event.payload["task_id"])))
        field_name, value = str(step.config["field"]), step.config["value"]
        if field_name == "status":
            old = task.status
            task.transition_to(TaskStatus(value))
            ctx.emit(
                TriggerType.TASK_STATUS_CHANGED,
                {"task_id": str(task.id), "from": old.value, "to": task.status.value},
            )
        elif field_name == "priority":
            task.priority = int(value)
        else:
            raise ValueError(f"field {field_name!r} is not updatable by workflows")
        return {"field": field_name, "value": value}


@dataclass
class RecordingNotifier:
    """Collects notifications in memory; the real one writes to the notifications table."""

    sent: list[Mapping[str, Any]] = field(default_factory=list)

    def __call__(self, step: Step, ctx: StepContext) -> Mapping[str, Any]:
        message = str(step.config.get("message", "")).format_map(dict(ctx.event.payload))
        self.sent.append({"to": step.config.get("to"), "message": message})
        return {"message": message}
