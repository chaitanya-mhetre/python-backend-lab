from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowforge.db.models import Notification, User
from flowforge.domain.errors import NotFoundError


class NotificationService:
    """Notifications belong to a user, not an org: users only ever see their own."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for(self, user: User, *, unread_only: bool, limit: int) -> list[Notification]:
        query = select(Notification).where(Notification.user_id == user.id)
        if unread_only:
            query = query.where(Notification.read_at.is_(None))  # uses the partial index
        rows = await self._session.scalars(
            query.order_by(Notification.created_at.desc()).limit(limit)
        )
        return list(rows)

    async def mark_read(self, user: User, notification_id: uuid.UUID) -> Notification:
        row = await self._session.get(Notification, notification_id)
        if row is None or row.user_id != user.id:
            raise NotFoundError("notification", notification_id)
        if row.read_at is None:
            row.read_at = datetime.now(UTC)
            await self._session.commit()
        return row
