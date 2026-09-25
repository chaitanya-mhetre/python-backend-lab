from __future__ import annotations

import re
import secrets

from sqlalchemy.ext.asyncio import AsyncSession

from flowforge.db.models import Membership, Organization, Role, User
from flowforge.repositories.orgs import OrgRepository


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug[:80] or "org"


class OrgService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._orgs = OrgRepository(session)

    async def create(self, owner: User, name: str) -> Organization:
        slug = slugify(name)
        while await self._orgs.slug_exists(slug):
            slug = f"{slugify(name)}-{secrets.token_hex(2)}"
        org = await self._orgs.add(Organization(name=name, slug=slug))
        await self._orgs.add_membership(
            Membership(user_id=owner.id, org_id=org.id, role=Role.OWNER)
        )
        await self._session.commit()
        return org

    async def list_for(self, user: User) -> list[tuple[Organization, Role]]:
        return await self._orgs.list_for_user(user.id)
