from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from flowforge.api.deps import CurrentUser, OrgAccessDep, SessionDep
from flowforge.api.schemas import ProjectIn, ProjectOut
from flowforge.services.access import AccessResolver
from flowforge.services.projects import ProjectService

router = APIRouter(tags=["projects"])


@router.post(
    "/orgs/{org_id}/projects", response_model=ProjectOut, status_code=status.HTTP_201_CREATED
)
async def create_project(body: ProjectIn, access: OrgAccessDep, session: SessionDep) -> object:
    return await ProjectService(session).create(access, body.name)


@router.get("/orgs/{org_id}/projects", response_model=list[ProjectOut])
async def list_projects(access: OrgAccessDep, session: SessionDep) -> object:
    return await ProjectService(session).list(access)


@router.get("/projects/{project_id}", response_model=ProjectOut)
async def get_project(project_id: uuid.UUID, user: CurrentUser, session: SessionDep) -> object:
    access = await AccessResolver(session).for_project(user, project_id)
    return await ProjectService(session).get(access, project_id)
