from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowforge.db.models import ApiKey
from flowforge.domain.errors import AuthenticationError, NotFoundError, PermissionDeniedError
from flowforge.repositories.audit import AuditRepository
from flowforge.security.api_keys import generate_key, parse_prefix, verify_key
from flowforge.security.permissions import PERMISSIONS, Action
from flowforge.services.access import OrgAccess, requires

LAST_USED_WRITE_INTERVAL = timedelta(minutes=1)


class ApiKeyService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._audit = AuditRepository(session)

    @requires(Action.API_KEY_MANAGE)
    async def create(
        self, access: OrgAccess, name: str, scopes: list[Action]
    ) -> tuple[ApiKey, str]:
        """Returns the row and the full key. The full key is never retrievable again."""
        if access.scopes is not None:
            raise PermissionDeniedError("API keys cannot create other API keys")
        # A key can never grant more than its creator holds (no privilege escalation).
        excess = set(scopes) - PERMISSIONS[access.role]
        if excess:
            raise PermissionDeniedError(
                f"cannot grant scopes you don't have: {sorted(a.value for a in excess)}"
            )
        generated = generate_key()
        key = ApiKey(
            org_id=access.org_id,
            name=name,
            prefix=generated.prefix,
            key_hash=generated.key_hash,
            scopes=sorted(a.value for a in set(scopes)),
            created_by=access.user_id,
        )
        self._session.add(key)
        await self._session.flush()
        self._audit.record(
            org_id=access.org_id,
            action="api_key.created",
            entity_type="api_key",
            entity_id=key.id,
            after={"name": name, "prefix": key.prefix, "scopes": key.scopes},
        )
        await self._session.commit()
        return key, generated.full_key

    @requires(Action.API_KEY_MANAGE)
    async def list_all(self, access: OrgAccess) -> list[ApiKey]:
        rows = await self._session.scalars(
            select(ApiKey).where(ApiKey.org_id == access.org_id).order_by(ApiKey.created_at)
        )
        return list(rows)

    @requires(Action.API_KEY_MANAGE)
    async def revoke(self, access: OrgAccess, key_id: uuid.UUID) -> None:
        key = await self._session.get(ApiKey, key_id)
        if key is None or key.org_id != access.org_id:
            raise NotFoundError("api_key", key_id)
        if key.revoked_at is None:
            key.revoked_at = datetime.now(UTC)
            self._audit.record(
                org_id=access.org_id,
                action="api_key.revoked",
                entity_type="api_key",
                entity_id=key.id,
            )
            await self._session.commit()

    async def authenticate(self, full_key: str) -> ApiKey:
        prefix = parse_prefix(full_key)
        key = None
        if prefix is not None:
            key = await self._session.scalar(select(ApiKey).where(ApiKey.prefix == prefix))
        if key is None or not verify_key(full_key, key.key_hash) or key.revoked_at is not None:
            raise AuthenticationError("invalid or revoked API key")
        now = datetime.now(UTC)
        # Don't write on every request: at most once a minute per key.
        if key.last_used_at is None or now - key.last_used_at > LAST_USED_WRITE_INTERVAL:
            key.last_used_at = now
            await self._session.commit()
        return key
