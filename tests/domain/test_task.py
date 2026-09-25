"""Lesson 1 spec: enums, dataclasses, type hints, custom exceptions.

Make these pass by writing:
  src/flowforge/domain/errors.py  -> DomainError, InvalidTransitionError
  src/flowforge/domain/task.py    -> TaskStatus, Task
"""

from uuid import UUID, uuid4

import pytest

from flowforge.domain.errors import DomainError, InvalidTransitionError
from flowforge.domain.task import Task, TaskStatus


def make_task(**overrides: object) -> Task:
    fields: dict[str, object] = {"project_id": uuid4(), "title": "Write M1"}
    fields.update(overrides)
    return Task(**fields)  # type: ignore[arg-type]


# --- TaskStatus -------------------------------------------------------------


def test_status_values_are_lowercase_strings() -> None:
    # They will be stored in Postgres and sent in JSON, so the value must be a plain string.
    assert TaskStatus.IN_PROGRESS == "in_progress"
    assert [s.value for s in TaskStatus] == ["todo", "in_progress", "done", "cancelled"]


def test_terminal_statuses() -> None:
    assert TaskStatus.DONE.is_terminal
    assert TaskStatus.CANCELLED.is_terminal
    assert not TaskStatus.TODO.is_terminal


# --- Task construction ------------------------------------------------------


def test_defaults() -> None:
    task = make_task()
    assert isinstance(task.id, UUID)
    assert task.status is TaskStatus.TODO
    assert task.priority == 3
    assert task.assignee_id is None
    assert task.version == 0


def test_each_task_gets_its_own_id() -> None:
    assert make_task().id != make_task().id


def test_title_is_stripped() -> None:
    assert make_task(title="  Write M1  ").title == "Write M1"


@pytest.mark.parametrize("title", ["", "   "])
def test_blank_title_rejected(title: str) -> None:
    with pytest.raises(ValueError):
        make_task(title=title)


@pytest.mark.parametrize("priority", [0, 6, -1])
def test_priority_must_be_1_to_5(priority: int) -> None:
    with pytest.raises(ValueError):
        make_task(priority=priority)


def test_equality_is_by_value() -> None:
    task_id, project_id = uuid4(), uuid4()
    a = Task(id=task_id, project_id=project_id, title="x")
    b = Task(id=task_id, project_id=project_id, title="x")
    assert a == b


# --- Status transitions -----------------------------------------------------
# Allowed:  todo -> in_progress | cancelled
#           in_progress -> done | todo | cancelled
#           done, cancelled -> (nothing, terminal)


@pytest.mark.parametrize(
    ("start", "target"),
    [
        (TaskStatus.TODO, TaskStatus.IN_PROGRESS),
        (TaskStatus.TODO, TaskStatus.CANCELLED),
        (TaskStatus.IN_PROGRESS, TaskStatus.DONE),
        (TaskStatus.IN_PROGRESS, TaskStatus.TODO),
        (TaskStatus.IN_PROGRESS, TaskStatus.CANCELLED),
    ],
)
def test_allowed_transition_changes_status_and_bumps_version(
    start: TaskStatus, target: TaskStatus
) -> None:
    task = make_task(status=start)
    task.transition_to(target)
    assert task.status is target
    assert task.version == 1


@pytest.mark.parametrize(
    ("start", "target"),
    [
        (TaskStatus.TODO, TaskStatus.DONE),
        (TaskStatus.DONE, TaskStatus.TODO),
        (TaskStatus.CANCELLED, TaskStatus.IN_PROGRESS),
        (TaskStatus.TODO, TaskStatus.TODO),
    ],
)
def test_forbidden_transition_raises_and_changes_nothing(
    start: TaskStatus, target: TaskStatus
) -> None:
    task = make_task(status=start)
    with pytest.raises(InvalidTransitionError) as exc_info:
        task.transition_to(target)

    err = exc_info.value
    assert err.current is start
    assert err.target is target
    assert start.value in str(err) and target.value in str(err)
    assert isinstance(err, DomainError)  # callers can catch every domain error in one place
    assert task.status is start and task.version == 0
