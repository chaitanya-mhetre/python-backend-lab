from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from flowforge.db.models import Project
from flowforge.domain.errors import NotFoundError
from flowforge.domain.task import TaskStatus
from flowforge.repositories.audit import AuditRepository
from flowforge.repositories.projects import ProjectRepository
from flowforge.security.permissions import Action
from flowforge.services.access import OrgAccess, requires
from flowforge.services.cache import ProjectStatsCache


class ProjectService:
    def __init__(self, session: AsyncSession, cache: ProjectStatsCache | None = None) -> None:
        self._session = session
        self._projects = ProjectRepository(session)
        self._audit = AuditRepository(session)
        self._cache = cache or ProjectStatsCache(None, ttl_seconds=1)

    @requires(Action.PROJECT_CREATE)
    async def create(self, access: OrgAccess, name: str) -> Project:
        project = await self._projects.add(Project(org_id=access.org_id, name=name.strip()))
        self._audit.record(
            org_id=access.org_id,
            action="project.created",
            entity_type="project",
            entity_id=project.id,
            after={"name": project.name},
        )
        await self._session.commit()
        return project

    @requires(Action.PROJECT_READ)
    async def list(self, access: OrgAccess) -> list[Project]:
        return await self._projects.list_active(access.org_id)

    @requires(Action.PROJECT_READ)
    async def get(self, access: OrgAccess, project_id: uuid.UUID) -> Project:
        return await self.load_in_org(access.org_id, project_id)

    async def load_in_org(self, org_id: uuid.UUID, project_id: uuid.UUID) -> Project:
        """Internal lookup without a permission check: callers have already authorized.

        Other services use this so that e.g. an API key scoped to ``task:create`` doesn't
        also need ``project:read`` just because creating a task looks the project up.
        """
        project = await self._projects.get(project_id)
        if project is None or project.org_id != org_id:
            raise NotFoundError("project", project_id)
        return project

    @requires(Action.PROJECT_READ)
    async def stats(self, access: OrgAccess, project_id: uuid.UUID) -> tuple[dict[str, int], bool]:
        """Task counts per status. Returns (stats, served_from_cache)."""
        await self.get(access, project_id)  # authorization is never skipped, even on a cache hit
        cached = await self._cache.get(project_id)
        if cached is not None:
            return {k: int(v) for k, v in cached.items()}, True
        counts = await self._projects.task_counts(project_id)
        stats = {status.value: counts.get(status, 0) for status in TaskStatus}
        await self._cache.set(project_id, stats)
        return stats, False

    @requires(Action.PROJECT_CREATE)
    async def update(
        self, access: OrgAccess, project_id: uuid.UUID, *, name: str | None, archived: bool | None
    ) -> Project:
        project = await self.get(access, project_id)
        before = {"name": project.name, "archived": project.archived_at is not None}
        if name is not None:
            project.name = name.strip()
        if archived is not None:
            project.archived_at = datetime.now(UTC) if archived else None
        self._audit.record(
            org_id=access.org_id,
            action="project.updated",
            entity_type="project",
            entity_id=project.id,
            before=before,
            after={"name": project.name, "archived": project.archived_at is not None},
        )
        await self._session.commit()
        await self._cache.invalidate(project_id)
        return project
