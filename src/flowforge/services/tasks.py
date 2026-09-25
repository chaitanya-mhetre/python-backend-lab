from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from flowforge.db.models import Notification, TaskRow
from flowforge.domain.errors import ConflictError, NotFoundError
from flowforge.domain.task import Task, TaskStatus
from flowforge.domain.workflow import Event, TriggerType, event_to_dict
from flowforge.jobs.queue import JobQueue
from flowforge.repositories.audit import AuditRepository
from flowforge.repositories.orgs import OrgRepository
from flowforge.repositories.pagination import Page
from flowforge.repositories.tasks import TaskFilter, TaskRepository
from flowforge.security.permissions import Action
from flowforge.services.access import OrgAccess, requires
from flowforge.services.cache import ProjectStatsCache
from flowforge.services.projects import ProjectService


@dataclass(frozen=True, slots=True)
class TaskCreate:
    title: str
    description: str = ""
    priority: int = 3
    assignee_id: uuid.UUID | None = None
    due_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class TaskChanges:
    """Only the fields in ``fields_set`` are applied (PATCH semantics)."""

    values: dict[str, Any] = field(default_factory=dict)


class VersionConflictError(ConflictError):
    code = "task_version_conflict"


def to_domain(row: TaskRow) -> Task:
    return Task(
        id=row.id,
        project_id=row.project_id,
        title=row.title,
        description=row.description,
        status=row.status,
        priority=row.priority,
        assignee_id=row.assignee_id,
        due_at=row.due_at,
        version=row.version,
    )


class TaskService:
    def __init__(
        self,
        session: AsyncSession,
        queue: JobQueue | None = None,
        cache: ProjectStatsCache | None = None,
    ) -> None:
        self._session = session
        self._queue = queue
        self._cache = cache or ProjectStatsCache(None, ttl_seconds=1)
        self._tasks = TaskRepository(session)
        self._projects = ProjectService(session)
        self._audit = AuditRepository(session)

    async def _check_assignee(self, org_id: uuid.UUID, assignee_id: uuid.UUID | None) -> None:
        if assignee_id is None:
            return
        if await OrgRepository(self._session).get_membership(org_id, assignee_id) is None:
            raise ConflictError("assignee is not a member of this organization")

    async def _load(self, access: OrgAccess, task_id: uuid.UUID) -> TaskRow:
        row = await self._tasks.get(task_id)
        if row is None:
            raise NotFoundError("task", task_id)
        project = await self._projects.load_in_org(access.org_id, row.project_id)
        if project.org_id != access.org_id:
            raise NotFoundError("task", task_id)
        return row

    @requires(Action.TASK_CREATE)
    async def create(self, access: OrgAccess, project_id: uuid.UUID, data: TaskCreate) -> TaskRow:
        project = await self._projects.load_in_org(access.org_id, project_id)
        await self._check_assignee(project.org_id, data.assignee_id)
        # Validate through the domain model so rules live in exactly one place.
        task = Task(
            project_id=project.id,
            title=data.title,
            description=data.description,
            priority=data.priority,
            assignee_id=data.assignee_id,
            due_at=data.due_at,
        )
        row = await self._tasks.add(
            TaskRow(
                id=task.id,
                project_id=task.project_id,
                title=task.title,
                description=task.description,
                status=task.status,
                priority=task.priority,
                assignee_id=task.assignee_id,
                due_at=task.due_at,
                created_by=access.user_id,
                version=0,
            )
        )
        if row.assignee_id is not None and row.assignee_id != access.user_id:
            self._notify_assigned(access, row, row.assignee_id)
        self._audit.record(
            org_id=access.org_id,
            action="task.created",
            entity_type="task",
            entity_id=row.id,
            after=snapshot(row),
        )
        await self._session.commit()
        await self._cache.invalidate(row.project_id)
        await self._publish(
            access.org_id,
            TriggerType.TASK_CREATED,
            {
                "task_id": str(row.id),
                "project_id": str(row.project_id),
                "title": row.title,
                "status": row.status.value,
            },
        )
        return row

    async def _publish(
        self, org_id: uuid.UUID, type_: TriggerType, payload: dict[str, Any]
    ) -> None:
        """Enqueue a domain event for the workflow worker, *after* the commit.

        Enqueue-after-commit means a crash right here loses the event (the task change itself
        is safe). Accepted trade-off for this lab; see docs/adr/0004-enqueue-after-commit.md.
        """
        if self._queue is not None:
            event = Event(type=type_, org_id=org_id, payload=payload)
            await self._queue.enqueue("dispatch_event", event_to_dict(event))

    def _notify_assigned(self, access: OrgAccess, row: TaskRow, assignee_id: uuid.UUID) -> None:
        # Same transaction as the change: no notification for a rolled-back assignment.
        self._session.add(
            Notification(
                user_id=assignee_id,
                org_id=access.org_id,
                kind="task_assigned",
                payload={"task_id": str(row.id), "title": row.title},
            )
        )

    @requires(Action.TASK_READ)
    async def get(self, access: OrgAccess, task_id: uuid.UUID) -> TaskRow:
        return await self._load(access, task_id)

    @requires(Action.TASK_READ)
    async def list(
        self,
        access: OrgAccess,
        flt: TaskFilter,
        *,
        sort: str,
        limit: int,
        cursor: str | None,
    ) -> Page[TaskRow]:
        await self._projects.load_in_org(access.org_id, flt.project_id)
        return await self._tasks.list(flt, sort=sort, limit=limit, cursor=cursor)

    @requires(Action.TASK_UPDATE)
    async def update(
        self,
        access: OrgAccess,
        task_id: uuid.UUID,
        changes: TaskChanges,
        expected_version: int | None = None,
    ) -> TaskRow:
        row = await self._load(access, task_id)
        before = snapshot(row)
        before_status = row.status
        task = to_domain(row)
        if expected_version is not None and expected_version != row.version:
            raise VersionConflictError(
                f"task was modified (current version {row.version}, you sent {expected_version})"
            )
        version = row.version

        values = dict(changes.values)
        if "status" in values:
            task.transition_to(TaskStatus(values["status"]))  # raises on illegal moves
        if "assignee_id" in values:
            await self._check_assignee(access.org_id, values["assignee_id"])
        # Re-run the domain validation on the merged state (title/priority rules).
        merged = {**_domain_fields(task), **{k: v for k, v in values.items() if k != "status"}}
        validated = Task(**merged)
        if "title" in values:
            values["title"] = validated.title

        updated = await self._tasks.update_if_version(task_id, version, values)
        if updated is None:
            raise VersionConflictError("task was modified by someone else; reload and retry")
        new_assignee = values.get("assignee_id")
        if new_assignee is not None and new_assignee != before["assignee_id"]:
            self._notify_assigned(access, updated, new_assignee)
        self._audit.record(
            org_id=access.org_id,
            action="task.updated",
            entity_type="task",
            entity_id=task_id,
            before=before,
            after=snapshot(updated),
        )
        await self._session.commit()
        await self._cache.invalidate(updated.project_id)
        if updated.status != before_status:
            await self._publish(
                access.org_id,
                TriggerType.TASK_STATUS_CHANGED,
                {
                    "task_id": str(task_id),
                    "project_id": str(updated.project_id),
                    "title": updated.title,
                    "from": before_status.value,
                    "to": updated.status.value,
                },
            )
        return updated

    @requires(Action.TASK_DELETE)
    async def delete(self, access: OrgAccess, task_id: uuid.UUID) -> None:
        row = await self._load(access, task_id)
        before = snapshot(row)
        await self._tasks.delete(row)
        self._audit.record(
            org_id=access.org_id,
            action="task.deleted",
            entity_type="task",
            entity_id=task_id,
            before=before,
        )
        await self._session.commit()
        await self._cache.invalidate(row.project_id)


def snapshot(row: TaskRow) -> dict[str, Any]:
    return {
        "title": row.title,
        "status": row.status.value,
        "priority": row.priority,
        "assignee_id": row.assignee_id,
        "due_at": row.due_at,
        "version": row.version,
    }


def _domain_fields(task: Task) -> dict[str, Any]:
    return {
        "id": task.id,
        "project_id": task.project_id,
        "title": task.title,
        "description": task.description,
        "status": task.status,
        "priority": task.priority,
        "assignee_id": task.assignee_id,
        "due_at": task.due_at,
        "version": task.version,
    }
