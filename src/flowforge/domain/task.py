"""The Task aggregate and its status state machine."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from flowforge.domain.errors import InvalidTransitionError


class TaskStatus(StrEnum):
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in _TERMINAL


_TERMINAL = frozenset({TaskStatus.DONE, TaskStatus.CANCELLED})

# The rules live in data, not in an if/else chain: adding a transition is a one-line change.
ALLOWED_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.TODO: frozenset({TaskStatus.IN_PROGRESS, TaskStatus.CANCELLED}),
    TaskStatus.IN_PROGRESS: frozenset({TaskStatus.DONE, TaskStatus.TODO, TaskStatus.CANCELLED}),
    TaskStatus.DONE: frozenset(),
    TaskStatus.CANCELLED: frozenset(),
}

MIN_PRIORITY, MAX_PRIORITY = 1, 5


@dataclass
class Task:
    project_id: UUID
    title: str
    # default_factory runs once per instance; `= uuid4()` would run once at class definition.
    id: UUID = field(default_factory=uuid4)
    description: str = ""
    status: TaskStatus = TaskStatus.TODO
    priority: int = 3
    assignee_id: UUID | None = None
    due_at: datetime | None = None
    version: int = 0

    def __post_init__(self) -> None:
        self.title = self.title.strip()
        if not self.title:
            raise ValueError("title must not be blank")
        if not MIN_PRIORITY <= self.priority <= MAX_PRIORITY:
            raise ValueError(f"priority must be between {MIN_PRIORITY} and {MAX_PRIORITY}")

    def can_transition_to(self, target: TaskStatus) -> bool:
        return target in ALLOWED_TRANSITIONS[self.status]

    def transition_to(self, target: TaskStatus) -> None:
        if not self.can_transition_to(target):
            raise InvalidTransitionError(self.status, target)
        self.status = target
        # Every state change bumps the version; the DB layer uses it for optimistic locking.
        self.version += 1
