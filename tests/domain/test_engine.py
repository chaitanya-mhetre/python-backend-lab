from collections.abc import Mapping
from typing import Any
from uuid import UUID, uuid4

import pytest

from flowforge.domain.engine import StepContext, WorkflowEngine
from flowforge.domain.repository import InMemoryRepository
from flowforge.domain.steps import CreateTaskStep, RecordingNotifier, UpdateFieldStep
from flowforge.domain.task import Task, TaskStatus
from flowforge.domain.workflow import (
    Event,
    ExecutionStatus,
    Step,
    StepType,
    TriggerType,
    WorkflowDefinition,
)

ORG = uuid4()
PROJECT = uuid4()


@pytest.fixture
def tasks() -> InMemoryRepository[Task]:
    return InMemoryRepository[Task]("task")


@pytest.fixture
def notifier() -> RecordingNotifier:
    return RecordingNotifier()


@pytest.fixture
def engine(tasks: InMemoryRepository[Task], notifier: RecordingNotifier) -> WorkflowEngine:
    return WorkflowEngine(
        {
            StepType.CREATE_TASK: CreateTaskStep(tasks),
            StepType.UPDATE_FIELD: UpdateFieldStep(tasks),
            StepType.SEND_NOTIFICATION: notifier,
        }
    )


def status_changed(task: Task, to: TaskStatus) -> Event:
    return Event(
        TriggerType.TASK_STATUS_CHANGED,
        ORG,
        {"task_id": str(task.id), "project_id": str(task.project_id), "to": to.value},
    )


def test_happy_path_runs_steps_in_position_order(
    engine: WorkflowEngine, tasks: InMemoryRepository[Task], notifier: RecordingNotifier
) -> None:
    done_task = Task(project_id=PROJECT, title="Ship", status=TaskStatus.DONE)
    tasks.add(done_task)
    wf = WorkflowDefinition(
        org_id=ORG,
        name="follow-up on done",
        trigger_type=TriggerType.TASK_STATUS_CHANGED,
        trigger_filter={"to": "done"},
        steps=(  # deliberately out of order
            Step(2, StepType.SEND_NOTIFICATION, {"to": "team", "message": "task {task_id} done"}),
            Step(1, StepType.CREATE_TASK, {"title": "Write retro"}),
        ),
    )

    [execution] = engine.dispatch(status_changed(done_task, TaskStatus.DONE), [wf])

    assert execution.status is ExecutionStatus.SUCCEEDED
    assert [r.step.position for r in execution.step_runs] == [1, 2]
    assert {t.title for t in tasks.list()} == {"Ship", "Write retro"}
    assert notifier.sent == [{"to": "team", "message": f"task {done_task.id} done"}]
    assert execution.definition_version == wf.version


def test_filter_mismatch_runs_nothing(engine: WorkflowEngine) -> None:
    task = Task(project_id=PROJECT, title="x")
    wf = WorkflowDefinition(
        org_id=ORG,
        name="only done",
        trigger_type=TriggerType.TASK_STATUS_CHANGED,
        trigger_filter={"to": "done"},
        steps=(Step(1, StepType.CREATE_TASK, {"title": "never"}),),
    )
    assert engine.dispatch(status_changed(task, TaskStatus.IN_PROGRESS), [wf]) == []


def test_other_org_and_disabled_definitions_are_ignored(engine: WorkflowEngine) -> None:
    event = Event(TriggerType.TASK_CREATED, ORG, {"project_id": str(PROJECT)})
    steps = (Step(1, StepType.CREATE_TASK, {"title": "t"}),)
    other_org = WorkflowDefinition(uuid4(), "other", TriggerType.TASK_CREATED, steps)
    disabled = WorkflowDefinition(ORG, "off", TriggerType.TASK_CREATED, steps, enabled=False)
    assert engine.dispatch(event, [other_org, disabled]) == []


def test_step_failure_stops_execution_and_records_error(
    engine: WorkflowEngine, tasks: InMemoryRepository[Task], notifier: RecordingNotifier
) -> None:
    task = Task(project_id=PROJECT, title="x")  # status TODO
    tasks.add(task)
    wf = WorkflowDefinition(
        org_id=ORG,
        name="bad transition",
        trigger_type=TriggerType.TASK_CREATED,
        steps=(
            Step(
                1, StepType.UPDATE_FIELD, {"field": "status", "value": "done"}
            ),  # illegal: todo->done
            Step(2, StepType.SEND_NOTIFICATION, {"message": "unreachable"}),
        ),
    )
    event = Event(TriggerType.TASK_CREATED, ORG, {"task_id": str(task.id)})

    [execution] = engine.dispatch(event, [wf])

    assert execution.status is ExecutionStatus.FAILED
    assert execution.error_code == "invalid_status_transition"
    assert len(execution.step_runs) == 1
    assert execution.step_runs[0].status is ExecutionStatus.FAILED
    assert notifier.sent == []
    assert task.status is TaskStatus.TODO


def test_unknown_step_type_fails_cleanly(tasks: InMemoryRepository[Task]) -> None:
    engine = WorkflowEngine({})
    wf = WorkflowDefinition(
        ORG, "no handlers", TriggerType.TASK_CREATED, (Step(1, StepType.DELAY, {"seconds": 1}),)
    )
    [execution] = engine.dispatch(Event(TriggerType.TASK_CREATED, ORG), [wf])
    assert execution.error_code == "unknown_step_type"


def test_workflow_loop_is_cut_off_at_max_depth(tasks: InMemoryRepository[Task]) -> None:
    # "When a task is created, create a task" triggers itself forever.
    engine = WorkflowEngine({StepType.CREATE_TASK: CreateTaskStep(tasks)}, max_depth=5)
    wf = WorkflowDefinition(
        ORG,
        "self-trigger",
        TriggerType.TASK_CREATED,
        (Step(1, StepType.CREATE_TASK, {"title": "again"}),),
    )
    event = Event(TriggerType.TASK_CREATED, ORG, {"project_id": str(PROJECT)})

    executions = engine.dispatch(event, [wf])

    assert [e.trigger_event.depth for e in executions] == [0, 1, 2, 3, 4, 5, 6]
    assert all(e.status is ExecutionStatus.SUCCEEDED for e in executions[:-1])
    assert executions[-1].status is ExecutionStatus.FAILED
    assert executions[-1].error_code == "workflow_loop_detected"
    assert len(tasks) == 6


def test_custom_handler_via_protocol() -> None:
    seen: list[UUID] = []

    def spy(step: Step, ctx: StepContext) -> Mapping[str, Any]:
        seen.append(ctx.execution.id)
        return {"ok": True}

    engine = WorkflowEngine({StepType.DELAY: spy})
    wf = WorkflowDefinition(ORG, "spy", TriggerType.TASK_CREATED, (Step(1, StepType.DELAY),))
    [execution] = engine.dispatch(Event(TriggerType.TASK_CREATED, ORG), [wf])
    assert seen == [execution.id]
    assert execution.step_runs[0].output == {"ok": True}


def test_duplicate_step_positions_rejected() -> None:
    with pytest.raises(ValueError):
        WorkflowDefinition(
            ORG,
            "dup",
            TriggerType.TASK_CREATED,
            (Step(1, StepType.DELAY), Step(1, StepType.DELAY)),
        )
