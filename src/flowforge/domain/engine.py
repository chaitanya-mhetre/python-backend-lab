"""Synchronous workflow engine (M1).

Given an event, the engine finds matching workflow definitions, runs their steps in order,
and processes any events those steps emit (e.g. "create_task" emits ``task.created``).
Events are processed breadth-first from a queue, and each carries a ``depth`` so that
workflows triggering each other forever are cut off at ``max_depth``.

M4 moves execution into an async background worker; the rules here stay the same.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from flowforge.domain.errors import DomainError, WorkflowLoopError
from flowforge.domain.workflow import (
    Event,
    Execution,
    ExecutionStatus,
    Step,
    StepRun,
    StepType,
    TriggerType,
    WorkflowDefinition,
)

DEFAULT_MAX_DEPTH = 5


@dataclass
class StepContext:
    """What a step handler can see and do."""

    event: Event
    execution: Execution
    emitted: list[Event] = field(default_factory=list)

    def emit(self, type_: TriggerType, payload: Mapping[str, Any]) -> None:
        self.emitted.append(self.event.child(type_, payload))


class StepHandler(Protocol):
    def __call__(self, step: Step, ctx: StepContext) -> Mapping[str, Any]: ...


class UnknownStepTypeError(DomainError):
    code = "unknown_step_type"


class WorkflowEngine:
    def __init__(
        self,
        handlers: Mapping[StepType, StepHandler],
        max_depth: int = DEFAULT_MAX_DEPTH,
    ) -> None:
        self._handlers = dict(handlers)
        self._max_depth = max_depth

    def run(self, definition: WorkflowDefinition, event: Event) -> tuple[Execution, list[Event]]:
        """Run one definition for one event. Returns the execution and the events it emitted."""
        execution = Execution(
            definition_id=definition.id,
            definition_version=definition.version,
            trigger_event=event,
        )
        execution.start()
        if event.depth > self._max_depth:
            err = WorkflowLoopError(event.depth, self._max_depth)
            execution.finish(ExecutionStatus.FAILED, str(err), err.code)
            return execution, []

        emitted: list[Event] = []
        for step in definition.ordered_steps:
            ctx = StepContext(event=event, execution=execution)
            try:
                handler = self._handlers.get(step.type)
                if handler is None:
                    raise UnknownStepTypeError(f"no handler for step type {step.type!r}")
                output = handler(step, ctx)
            except Exception as exc:  # noqa: BLE001 - any step failure fails the execution
                code = exc.code if isinstance(exc, DomainError) else "step_error"
                execution.step_runs.append(
                    StepRun(step=step, status=ExecutionStatus.FAILED, error=str(exc))
                )
                execution.finish(ExecutionStatus.FAILED, f"step {step.position}: {exc}", code)
                return execution, []  # events from a failed run are discarded
            execution.step_runs.append(
                StepRun(step=step, status=ExecutionStatus.SUCCEEDED, output=output)
            )
            emitted.extend(ctx.emitted)

        execution.finish(ExecutionStatus.SUCCEEDED)
        return execution, emitted

    def dispatch(self, event: Event, definitions: Iterable[WorkflowDefinition]) -> list[Execution]:
        """Process an event and everything it causes, breadth-first."""
        defs = list(definitions)
        queue: deque[Event] = deque([event])
        executions: list[Execution] = []
        while queue:
            current = queue.popleft()
            for definition in defs:
                if definition.matches(current):
                    execution, emitted = self.run(definition, current)
                    executions.append(execution)
                    queue.extend(emitted)
        return executions
