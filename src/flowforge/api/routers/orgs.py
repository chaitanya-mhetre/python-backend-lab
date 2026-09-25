from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from flowforge.api.deps import CurrentUser, OrgAccessDep, SessionDep
from flowforge.api.schemas import (
    AuditLogOut,
    MemberIn,
    MemberOut,
    MemberRoleIn,
    OrgIn,
    OrgOut,
    OrgWithRoleOut,
)
from flowforge.repositories.users import UserRepository
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


@router.get("/orgs/{org_id}/members", response_model=list[MemberOut])
async def list_members(access: OrgAccessDep, session: SessionDep) -> list[MemberOut]:
    rows = await OrgService(session).list_members(access)
    return [MemberOut(user_id=u.id, email=u.email, full_name=u.full_name, role=r) for u, r in rows]


@router.post(
    "/orgs/{org_id}/members", response_model=MemberOut, status_code=status.HTTP_201_CREATED
)
async def add_member(body: MemberIn, access: OrgAccessDep, session: SessionDep) -> MemberOut:
    membership = await OrgService(session).add_member(access, str(body.email), body.role)
    user = await UserRepository(session).get(membership.user_id)
    assert user is not None
    return MemberOut(
        user_id=user.id, email=user.email, full_name=user.full_name, role=membership.role
    )


@router.patch("/orgs/{org_id}/members/{user_id}", response_model=MemberOut)
async def change_member_role(
    user_id: uuid.UUID, body: MemberRoleIn, access: OrgAccessDep, session: SessionDep
) -> MemberOut:
    membership = await OrgService(session).change_role(access, user_id, body.role)
    user = await UserRepository(session).get(user_id)
    assert user is not None
    return MemberOut(
        user_id=user.id, email=user.email, full_name=user.full_name, role=membership.role
    )


@router.delete("/orgs/{org_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(user_id: uuid.UUID, access: OrgAccessDep, session: SessionDep) -> None:
    await OrgService(session).remove_member(access, user_id)


@router.get("/orgs/{org_id}/audit-logs", response_model=list[AuditLogOut])
async def audit_logs(
    access: OrgAccessDep,
    session: SessionDep,
    entity_type: str | None = None,
    entity_id: str | None = None,
    before_id: int | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> object:
    return await OrgService(session).audit_logs(
        access, entity_type=entity_type, entity_id=entity_id, before_id=before_id, limit=limit
    )
