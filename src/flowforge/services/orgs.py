from __future__ import annotations

import re
import secrets
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from flowforge.db.models import AuditLog, Membership, Organization, Role, User
from flowforge.domain.errors import ConflictError, NotFoundError, PermissionDeniedError
from flowforge.repositories.audit import AuditRepository
from flowforge.repositories.orgs import OrgRepository
from flowforge.repositories.users import UserRepository
from flowforge.security.permissions import Action
from flowforge.services.access import OrgAccess, requires


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug[:80] or "org"


class OrgService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._orgs = OrgRepository(session)
        self._audit = AuditRepository(session)

    async def create(self, owner: User, name: str) -> Organization:
        slug = slugify(name)
        while await self._orgs.slug_exists(slug):
            slug = f"{slugify(name)}-{secrets.token_hex(2)}"
        org = await self._orgs.add(Organization(name=name, slug=slug))
        await self._orgs.add_membership(
            Membership(user_id=owner.id, org_id=org.id, role=Role.OWNER)
        )
        self._audit.record(
            org_id=org.id,
            action="org.created",
            entity_type="organization",
            entity_id=org.id,
            after={"name": name, "slug": slug},
        )
        await self._session.commit()
        return org

    async def list_for(self, user: User) -> list[tuple[Organization, Role]]:
        return await self._orgs.list_for_user(user.id)

    # --- members --------------------------------------------------------------

    @requires(Action.ORG_READ)
    async def list_members(self, access: OrgAccess) -> list[tuple[User, Role]]:
        return await self._orgs.list_members(access.org_id)

    def _check_can_assign(self, access: OrgAccess, role: Role) -> None:
        # Admins manage members, but only owners can create or change owners.
        if role is Role.OWNER and access.role is not Role.OWNER:
            raise PermissionDeniedError("only owners can grant the owner role")

    @requires(Action.MEMBER_MANAGE)
    async def add_member(self, access: OrgAccess, email: str, role: Role) -> Membership:
        self._check_can_assign(access, role)
        user = await UserRepository(self._session).get_by_email(email)
        if user is None:
            raise NotFoundError("user", email)
        if await self._orgs.get_membership(access.org_id, user.id) is not None:
            raise ConflictError("user is already a member")
        membership = await self._orgs.add_membership(
            Membership(user_id=user.id, org_id=access.org_id, role=role)
        )
        self._audit.record(
            org_id=access.org_id,
            action="member.added",
            entity_type="membership",
            entity_id=user.id,
            after={"role": role.value},
        )
        await self._session.commit()
        return membership

    async def _get_membership(self, access: OrgAccess, user_id: uuid.UUID) -> Membership:
        membership = await self._orgs.get_membership(access.org_id, user_id)
        if membership is None:
            raise NotFoundError("member", user_id)
        if membership.role is Role.OWNER and access.role is not Role.OWNER:
            raise PermissionDeniedError("only owners can modify owners")
        return membership

    async def _ensure_not_last_owner(self, access: OrgAccess, membership: Membership) -> None:
        if membership.role is Role.OWNER and await self._orgs.count_owners(access.org_id) <= 1:
            raise ConflictError("an organization must keep at least one owner")

    @requires(Action.MEMBER_MANAGE)
    async def change_role(self, access: OrgAccess, user_id: uuid.UUID, role: Role) -> Membership:
        self._check_can_assign(access, role)
        membership = await self._get_membership(access, user_id)
        if role is not Role.OWNER:
            await self._ensure_not_last_owner(access, membership)
        before = membership.role
        membership.role = role
        self._audit.record(
            org_id=access.org_id,
            action="member.role_changed",
            entity_type="membership",
            entity_id=user_id,
            before={"role": before.value},
            after={"role": role.value},
        )
        await self._session.commit()
        return membership

    @requires(Action.MEMBER_MANAGE)
    async def remove_member(self, access: OrgAccess, user_id: uuid.UUID) -> None:
        membership = await self._get_membership(access, user_id)
        await self._ensure_not_last_owner(access, membership)
        before = membership.role
        await self._orgs.remove_membership(membership)
        self._audit.record(
            org_id=access.org_id,
            action="member.removed",
            entity_type="membership",
            entity_id=user_id,
            before={"role": before.value},
        )
        await self._session.commit()

    # --- audit ------------------------------------------------------------------

    @requires(Action.AUDIT_READ)
    async def audit_logs(
        self,
        access: OrgAccess,
        *,
        entity_type: str | None,
        entity_id: str | None,
        before_id: int | None,
        limit: int,
    ) -> list[AuditLog]:
        return await self._audit.list(
            access.org_id,
            entity_type=entity_type,
            entity_id=entity_id,
            before_id=before_id,
            limit=limit,
        )
