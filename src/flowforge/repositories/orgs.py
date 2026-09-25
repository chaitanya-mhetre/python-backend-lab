from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowforge.db.models import Membership, Organization, Role, User


class OrgRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, org_id: uuid.UUID) -> Organization | None:
        return await self._session.get(Organization, org_id)

    async def slug_exists(self, slug: str) -> bool:
        found = await self._session.scalar(select(Organization.id).where(Organization.slug == slug))
        return found is not None

    async def add(self, org: Organization) -> Organization:
        self._session.add(org)
        await self._session.flush()
        return org

    async def list_for_user(self, user_id: uuid.UUID) -> list[tuple[Organization, Role]]:
        rows = await self._session.execute(
            select(Organization, Membership.role)
            .join(Membership, Membership.org_id == Organization.id)
            .where(Membership.user_id == user_id)
            .order_by(Organization.created_at, Organization.id)
        )
        return [(org, role) for org, role in rows]

    async def get_membership(self, org_id: uuid.UUID, user_id: uuid.UUID) -> Membership | None:
        return await self._session.get(Membership, (user_id, org_id))

    async def add_membership(self, membership: Membership) -> Membership:
        self._session.add(membership)
        await self._session.flush()
        return membership

    async def remove_membership(self, membership: Membership) -> None:
        await self._session.delete(membership)
        await self._session.flush()

    async def list_members(self, org_id: uuid.UUID) -> list[tuple[User, Role]]:
        rows = await self._session.execute(
            select(User, Membership.role)
            .join(Membership, Membership.user_id == User.id)
            .where(Membership.org_id == org_id)
            .order_by(User.email)
        )
        return [(user, role) for user, role in rows]

    async def count_owners(self, org_id: uuid.UUID) -> int:
        rows = await self._session.scalars(
            select(Membership.user_id).where(
                Membership.org_id == org_id, Membership.role == Role.OWNER
            )
        )
        return len(rows.all())
