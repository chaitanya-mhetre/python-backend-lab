"""Domain errors.

Every business-rule violation derives from :class:`DomainError`, so the web layer can
translate all of them to HTTP responses in one place.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from flowforge.domain.task import TaskStatus


class DomainError(Exception):
    """Base class for all business-rule errors."""

    code: str = "domain_error"


class InvalidTransitionError(DomainError):
    """A task was asked to move to a status it is not allowed to reach."""

    code = "invalid_status_transition"

    def __init__(self, current: TaskStatus, target: TaskStatus) -> None:
        super().__init__(f"cannot move task from {current.value!r} to {target.value!r}")
        self.current = current
        self.target = target


class NotFoundError(DomainError):
    code = "not_found"

    def __init__(self, entity: str, entity_id: object) -> None:
        super().__init__(f"{entity} {entity_id} not found")
        self.entity = entity
        self.entity_id = entity_id


class WorkflowLoopError(DomainError):
    """Workflows triggered each other deeper than the allowed limit."""

    code = "workflow_loop_detected"

    def __init__(self, depth: int, limit: int) -> None:
        super().__init__(f"workflow trigger depth {depth} exceeds limit {limit}")
        self.depth = depth
        self.limit = limit


class StepFailedError(DomainError):
    code = "workflow_step_failed"

    def __init__(self, position: int, reason: str) -> None:
        super().__init__(f"step {position} failed: {reason}")
        self.position = position
        self.reason = reason
