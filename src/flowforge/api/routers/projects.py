from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from flowforge.api.deps import CurrentUser, SessionDep
from flowforge.api.schemas import ProjectIn, ProjectOut
from flowforge.services.projects import ProjectService

router = APIRouter(tags=["projects"])


@router.post(
    "/orgs/{org_id}/projects", response_model=ProjectOut, status_code=status.HTTP_201_CREATED
)
async def create_project(
    org_id: uuid.UUID, body: ProjectIn, user: CurrentUser, session: SessionDep
) -> object:
    return await ProjectService(session).create(user, org_id, body.name)


@router.get("/orgs/{org_id}/projects", response_model=list[ProjectOut])
async def list_projects(org_id: uuid.UUID, user: CurrentUser, session: SessionDep) -> object:
    return await ProjectService(session).list(user, org_id)


@router.get("/projects/{project_id}", response_model=ProjectOut)
async def get_project(project_id: uuid.UUID, user: CurrentUser, session: SessionDep) -> object:
    return await ProjectService(session).get(user, project_id)
