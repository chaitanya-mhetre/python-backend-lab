"""Membership lookup shared by services.

Non-members get *404*, not 403: telling a stranger "forbidden" would confirm the org exists.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from flowforge.db.models import Role
from flowforge.domain.errors import NotFoundError
from flowforge.repositories.orgs import OrgRepository


async def membership_role(session: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID) -> Role:
    membership = await OrgRepository(session).get_membership(org_id, user_id)
    if membership is None:
        raise NotFoundError("organization", org_id)
    return membership.role
