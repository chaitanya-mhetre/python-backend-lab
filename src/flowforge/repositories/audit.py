"""Audit log writer/reader.

``record`` only *adds* the row to the current session: it commits together with the change
it describes (same transaction), so the log can never claim something happened that was
rolled back, nor miss something that committed.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowforge.context import actor_var, request_id_var
from flowforge.db.models import AuditLog


def _jsonable(data: dict[str, Any] | None) -> dict[str, Any] | None:
    if data is None:
        return None
    result: dict[str, Any] = json.loads(json.dumps(data, default=str))
    return result


class AuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def record(
        self,
        *,
        org_id: uuid.UUID,
        action: str,
        entity_type: str,
        entity_id: object,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
    ) -> None:
        actor = actor_var.get()
        self._session.add(
            AuditLog(
                org_id=org_id,
                actor_type=actor.type.value,
                actor_id=actor.id,
                action=action,
                entity_type=entity_type,
                entity_id=str(entity_id),
                before=_jsonable(before),
                after=_jsonable(after),
                request_id=request_id_var.get(),
            )
        )

    async def list(
        self,
        org_id: uuid.UUID,
        *,
        entity_type: str | None = None,
        entity_id: str | None = None,
        before_id: int | None = None,
        limit: int = 50,
    ) -> list[AuditLog]:
        query = select(AuditLog).where(AuditLog.org_id == org_id)
        if entity_type:
            query = query.where(AuditLog.entity_type == entity_type)
        if entity_id:
            query = query.where(AuditLog.entity_id == entity_id)
        if before_id is not None:
            query = query.where(AuditLog.id < before_id)
        rows = await self._session.scalars(query.order_by(AuditLog.id.desc()).limit(limit))
        return list(rows)
