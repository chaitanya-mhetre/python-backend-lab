from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from flowforge.db.models import Project, User
from flowforge.domain.errors import NotFoundError
from flowforge.repositories.projects import ProjectRepository
from flowforge.services.access import membership_role


class ProjectService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._projects = ProjectRepository(session)

    async def create(self, user: User, org_id: uuid.UUID, name: str) -> Project:
        await membership_role(self._session, org_id, user.id)
        project = await self._projects.add(Project(org_id=org_id, name=name.strip()))
        await self._session.commit()
        return project

    async def list(self, user: User, org_id: uuid.UUID) -> list[Project]:
        await membership_role(self._session, org_id, user.id)
        return await self._projects.list_active(org_id)

    async def get(self, user: User, project_id: uuid.UUID) -> Project:
        project = await self._projects.get(project_id)
        if project is None:
            raise NotFoundError("project", project_id)
        try:
            await membership_role(self._session, project.org_id, user.id)
        except NotFoundError:
            raise NotFoundError("project", project_id) from None
        return project
