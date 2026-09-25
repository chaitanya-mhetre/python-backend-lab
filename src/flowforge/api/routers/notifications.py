from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from flowforge.api.deps import CurrentUser, SessionDep
from flowforge.api.schemas import NotificationOut
from flowforge.services.notifications import NotificationService

router = APIRouter(tags=["notifications"])


@router.get("/notifications", response_model=list[NotificationOut])
async def list_notifications(
    user: CurrentUser,
    session: SessionDep,
    unread: bool = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> object:
    return await NotificationService(session).list_for(user, unread_only=unread, limit=limit)


@router.post("/notifications/{notification_id}:read", response_model=NotificationOut)
async def mark_read(notification_id: uuid.UUID, user: CurrentUser, session: SessionDep) -> object:
    return await NotificationService(session).mark_read(user, notification_id)
