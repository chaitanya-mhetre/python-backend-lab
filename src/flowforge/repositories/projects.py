from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from flowforge.db.models import Project, TaskRow
from flowforge.domain.task import TaskStatus


class ProjectRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, project_id: uuid.UUID) -> Project | None:
        return await self._session.get(Project, project_id)

    async def add(self, project: Project) -> Project:
        self._session.add(project)
        await self._session.flush()
        return project

    async def list_active(self, org_id: uuid.UUID) -> list[Project]:
        rows = await self._session.scalars(
            select(Project)
            .where(Project.org_id == org_id, Project.archived_at.is_(None))
            .order_by(Project.created_at, Project.id)
        )
        return list(rows)

    async def task_counts(self, project_id: uuid.UUID) -> dict[TaskStatus, int]:
        rows = await self._session.execute(
            select(TaskRow.status, func.count())
            .where(TaskRow.project_id == project_id)
            .group_by(TaskRow.status)
        )
        return {status: count for status, count in rows}
