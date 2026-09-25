from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Header, Query, Response, status

from flowforge.api.deps import CurrentUser, SessionDep
from flowforge.api.schemas import PageOut, TaskIn, TaskOut, TaskPatch
from flowforge.domain.errors import DomainError
from flowforge.domain.task import TaskStatus
from flowforge.repositories.tasks import TaskFilter
from flowforge.services.tasks import TaskChanges, TaskCreate, TaskService

router = APIRouter(tags=["tasks"])


class InvalidIfMatchError(DomainError):
    code = "invalid_if_match"


def parse_if_match(value: str | None) -> int | None:
    """``If-Match: "3"`` or ``If-Match: 3`` -> 3. Absent header -> no version check."""
    if value is None:
        return None
    try:
        return int(value.strip().strip('"').removeprefix('W/"').rstrip('"'))
    except ValueError:
        raise InvalidIfMatchError("If-Match must be the task version number") from None


@router.post(
    "/projects/{project_id}/tasks", response_model=TaskOut, status_code=status.HTTP_201_CREATED
)
async def create_task(
    project_id: uuid.UUID, body: TaskIn, user: CurrentUser, session: SessionDep
) -> object:
    data = TaskCreate(**body.model_dump())
    return await TaskService(session).create(user, project_id, data)


@router.get("/projects/{project_id}/tasks", response_model=PageOut[TaskOut])
async def list_tasks(
    project_id: uuid.UUID,
    user: CurrentUser,
    session: SessionDep,
    task_status: Annotated[TaskStatus | None, Query(alias="status")] = None,
    assignee: Annotated[str | None, Query(description="user id or 'me'")] = None,
    due_before: datetime | None = None,
    sort: Annotated[str, Query(pattern=r"^-?[a-z_]+$")] = "created_at",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: str | None = None,
) -> PageOut[TaskOut]:
    assignee_id = user.id if assignee == "me" else (uuid.UUID(assignee) if assignee else None)
    flt = TaskFilter(
        project_id=project_id, status=task_status, assignee_id=assignee_id, due_before=due_before
    )
    page = await TaskService(session).list(user, flt, sort=sort, limit=limit, cursor=cursor)
    return PageOut[TaskOut](
        items=[TaskOut.model_validate(t) for t in page.items], next_cursor=page.next_cursor
    )


@router.get("/tasks/{task_id}", response_model=TaskOut)
async def get_task(
    task_id: uuid.UUID, user: CurrentUser, session: SessionDep, response: Response
) -> object:
    task = await TaskService(session).get(user, task_id)
    response.headers["ETag"] = f'"{task.version}"'
    return task


@router.patch("/tasks/{task_id}", response_model=TaskOut)
async def update_task(
    task_id: uuid.UUID,
    body: TaskPatch,
    user: CurrentUser,
    session: SessionDep,
    response: Response,
    if_match: Annotated[str | None, Header()] = None,
) -> object:
    # exclude_unset: only fields the client actually sent are changed (true PATCH semantics).
    changes = TaskChanges(body.model_dump(exclude_unset=True))
    task = await TaskService(session).update(user, task_id, changes, parse_if_match(if_match))
    response.headers["ETag"] = f'"{task.version}"'
    return task


@router.delete("/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(task_id: uuid.UUID, user: CurrentUser, session: SessionDep) -> None:
    await TaskService(session).delete(user, task_id)
