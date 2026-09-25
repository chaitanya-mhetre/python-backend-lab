from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowforge.db.models import User


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, user_id: uuid.UUID) -> User | None:
        return await self._session.get(User, user_id)

    async def get_by_email(self, email: str) -> User | None:
        # email is CITEXT, so this comparison is case-insensitive in Postgres.
        return await self._session.scalar(select(User).where(User.email == email))

    async def add(self, user: User) -> User:
        self._session.add(user)
        await self._session.flush()  # sends INSERT now so unique violations surface here
        return user
