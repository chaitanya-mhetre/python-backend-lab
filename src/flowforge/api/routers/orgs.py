from __future__ import annotations

from fastapi import APIRouter, status

from flowforge.api.deps import CurrentUser, SessionDep
from flowforge.api.schemas import OrgIn, OrgOut, OrgWithRoleOut
from flowforge.services.orgs import OrgService

router = APIRouter(tags=["organizations"])


@router.post("/orgs", response_model=OrgOut, status_code=status.HTTP_201_CREATED)
async def create_org(body: OrgIn, user: CurrentUser, session: SessionDep) -> object:
    return await OrgService(session).create(user, body.name)


@router.get("/orgs", response_model=list[OrgWithRoleOut])
async def list_orgs(user: CurrentUser, session: SessionDep) -> list[OrgWithRoleOut]:
    rows = await OrgService(session).list_for(user)
    return [
        OrgWithRoleOut(id=o.id, name=o.name, slug=o.slug, created_at=o.created_at, role=role)
        for o, role in rows
    ]
