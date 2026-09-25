from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from flowforge.db.models import Project
from flowforge.domain.errors import NotFoundError
from flowforge.repositories.audit import AuditRepository
from flowforge.repositories.projects import ProjectRepository
from flowforge.security.permissions import Action
from flowforge.services.access import OrgAccess, requires


class ProjectService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._projects = ProjectRepository(session)
        self._audit = AuditRepository(session)

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
        project = await self._projects.get(project_id)
        if project is None or project.org_id != access.org_id:
            raise NotFoundError("project", project_id)
        return project
