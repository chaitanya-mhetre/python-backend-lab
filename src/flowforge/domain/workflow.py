"""Workflow definitions, events and executions (pure domain, no I/O)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from functools import cached_property
from typing import Any
from uuid import UUID, uuid4


class TriggerType(StrEnum):
    TASK_CREATED = "task.created"
    TASK_STATUS_CHANGED = "task.status_changed"
    SCHEDULE_CRON = "schedule.cron"


class StepType(StrEnum):
    CREATE_TASK = "create_task"
    UPDATE_FIELD = "update_field"
    SEND_NOTIFICATION = "send_notification"
    CALL_WEBHOOK = "call_webhook"
    DELAY = "delay"


class ExecutionStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class Event:
    """Something happened. ``depth`` counts how many workflows caused it (loop protection)."""

    type: TriggerType
    org_id: UUID
    payload: Mapping[str, Any] = field(default_factory=dict)
    depth: int = 0

    def child(self, type_: TriggerType, payload: Mapping[str, Any]) -> Event:
        return Event(type=type_, org_id=self.org_id, payload=payload, depth=self.depth + 1)


@dataclass(frozen=True, slots=True)
class Step:
    position: int
    type: StepType
    config: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class WorkflowDefinition:
    org_id: UUID
    name: str
    trigger_type: TriggerType
    steps: tuple[Step, ...]
    trigger_filter: Mapping[str, Any] = field(default_factory=dict)
    enabled: bool = True
    version: int = 1
    id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        positions = [s.position for s in self.steps]
        if len(positions) != len(set(positions)):
            raise ValueError("step positions must be unique")

    @cached_property
    def ordered_steps(self) -> tuple[Step, ...]:
        return tuple(sorted(self.steps, key=lambda s: s.position))

    def matches(self, event: Event) -> bool:
        """Enabled, same org, same trigger, and every filter key equals the payload value."""
        return (
            self.enabled
            and event.org_id == self.org_id
            and event.type == self.trigger_type
            and all(event.payload.get(k) == v for k, v in self.trigger_filter.items())
        )


@dataclass
class StepRun:
    step: Step
    status: ExecutionStatus
    output: Mapping[str, Any] = field(default_factory=dict)
    error: str | None = None
    attempt: int = 1


@dataclass
class Execution:
    definition_id: UUID
    definition_version: int
    trigger_event: Event
    status: ExecutionStatus = ExecutionStatus.PENDING
    step_runs: list[StepRun] = field(default_factory=list)
    error: str | None = None
    error_code: str | None = None
    id: UUID = field(default_factory=uuid4)
    started_at: datetime | None = None
    finished_at: datetime | None = None

    def start(self) -> None:
        self.status = ExecutionStatus.RUNNING
        self.started_at = datetime.now(UTC)

    def finish(
        self, status: ExecutionStatus, error: str | None = None, code: str | None = None
    ) -> None:
        self.status = status
        self.error = error
        self.error_code = code
        self.finished_at = datetime.now(UTC)


def event_to_dict(event: Event) -> dict[str, Any]:
    """JSON-safe form, used for job payloads and ``workflow_executions.trigger_event``."""
    return {
        "type": event.type.value,
        "org_id": str(event.org_id),
        "payload": dict(event.payload),
        "depth": event.depth,
    }


def event_from_dict(data: Mapping[str, Any]) -> Event:
    return Event(
        type=TriggerType(data["type"]),
        org_id=UUID(str(data["org_id"])),
        payload=dict(data.get("payload", {})),
        depth=int(data.get("depth", 0)),
    )
