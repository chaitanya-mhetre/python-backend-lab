from __future__ import annotations

import uuid

from fastapi import APIRouter, Response, status

from flowforge.api.deps import CacheDep, CurrentPrincipal, OrgAccessDep, SessionDep
from flowforge.api.schemas import ProjectDetailOut, ProjectIn, ProjectOut, ProjectPatch
from flowforge.db.models import Project
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


def detail(project: Project, counts: dict[str, int]) -> ProjectDetailOut:
    return ProjectDetailOut(
        id=project.id,
        org_id=project.org_id,
        name=project.name,
        created_at=project.created_at,
        archived=project.archived_at is not None,
        task_counts=counts,
    )


@router.get("/projects/{project_id}", response_model=ProjectDetailOut)
async def get_project(
    project_id: uuid.UUID,
    principal: CurrentPrincipal,
    session: SessionDep,
    cache: CacheDep,
    response: Response,
) -> ProjectDetailOut:
    access = await AccessResolver(session).for_project(principal, project_id)
    service = ProjectService(session, cache)
    project = await service.get(access, project_id)
    counts, hit = await service.stats(access, project_id)
    response.headers["X-Cache"] = "HIT" if hit else "MISS"
    return detail(project, counts)


@router.patch("/projects/{project_id}", response_model=ProjectDetailOut)
async def update_project(
    project_id: uuid.UUID,
    body: ProjectPatch,
    principal: CurrentPrincipal,
    session: SessionDep,
    cache: CacheDep,
) -> ProjectDetailOut:
    access = await AccessResolver(session).for_project(principal, project_id)
    service = ProjectService(session, cache)
    project = await service.update(access, project_id, name=body.name, archived=body.archived)
    counts, _ = await service.stats(access, project_id)
    return detail(project, counts)
