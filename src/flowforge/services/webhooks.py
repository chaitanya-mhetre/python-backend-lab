from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowforge.db.models import DeliveryStatus, Webhook, WebhookDelivery
from flowforge.domain.errors import NotFoundError
from flowforge.domain.workflow import TriggerType
from flowforge.jobs.queue import JobQueue
from flowforge.repositories.audit import AuditRepository
from flowforge.security.permissions import Action
from flowforge.security.webhooks import Resolver, SecretBox, check_url, new_signing_secret
from flowforge.services.access import OrgAccess, requires

SUBSCRIBABLE_EVENTS = frozenset(t.value for t in TriggerType if t is not TriggerType.SCHEDULE_CRON)


class WebhookService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        secret_box: SecretBox,
        resolver: Resolver,
        allow_private: bool,
        queue: JobQueue | None = None,
    ) -> None:
        self._session = session
        self._box = secret_box
        self._resolver = resolver
        self._allow_private = allow_private
        self._queue = queue
        self._audit = AuditRepository(session)

    async def _load(self, access: OrgAccess, webhook_id: uuid.UUID) -> Webhook:
        webhook = await self._session.get(Webhook, webhook_id)
        if webhook is None or webhook.org_id != access.org_id:
            raise NotFoundError("webhook", webhook_id)
        return webhook

    @requires(Action.WEBHOOK_MANAGE)
    async def create(self, access: OrgAccess, url: str, events: list[str]) -> tuple[Webhook, str]:
        await check_url(url, resolver=self._resolver, allow_private=self._allow_private)
        secret = new_signing_secret()
        webhook = Webhook(
            org_id=access.org_id,
            url=url,
            secret_encrypted=self._box.encrypt(secret),
            events=sorted(set(events)),
            active=True,
        )
        self._session.add(webhook)
        await self._session.flush()
        self._audit.record(
            org_id=access.org_id,
            action="webhook.created",
            entity_type="webhook",
            entity_id=webhook.id,
            after={"url": url, "events": webhook.events},  # never the secret
        )
        await self._session.commit()
        return webhook, secret

    @requires(Action.WEBHOOK_MANAGE)
    async def list_all(self, access: OrgAccess) -> list[Webhook]:
        rows = await self._session.scalars(
            select(Webhook).where(Webhook.org_id == access.org_id).order_by(Webhook.created_at)
        )
        return list(rows)

    @requires(Action.WEBHOOK_MANAGE)
    async def deactivate(self, access: OrgAccess, webhook_id: uuid.UUID) -> None:
        webhook = await self._load(access, webhook_id)
        webhook.active = False
        self._audit.record(
            org_id=access.org_id,
            action="webhook.deactivated",
            entity_type="webhook",
            entity_id=webhook.id,
        )
        await self._session.commit()

    @requires(Action.WEBHOOK_MANAGE)
    async def deliveries(
        self, access: OrgAccess, webhook_id: uuid.UUID, limit: int = 50
    ) -> list[WebhookDelivery]:
        await self._load(access, webhook_id)
        rows = await self._session.scalars(
            select(WebhookDelivery)
            .where(WebhookDelivery.webhook_id == webhook_id)
            .order_by(WebhookDelivery.created_at.desc())
            .limit(limit)
        )
        return list(rows)

    @requires(Action.WEBHOOK_MANAGE)
    async def redeliver(self, access: OrgAccess, delivery_id: uuid.UUID) -> WebhookDelivery:
        """Send the same payload again as a fresh delivery (same event id for receiver dedup)."""
        original = await self._session.get(WebhookDelivery, delivery_id)
        if original is None:
            raise NotFoundError("delivery", delivery_id)
        await self._load(access, original.webhook_id)
        copy = WebhookDelivery(
            webhook_id=original.webhook_id,
            event_type=original.event_type,
            payload=original.payload,
            status=DeliveryStatus.PENDING,
            attempt=0,
        )
        self._session.add(copy)
        await self._session.flush()
        self._audit.record(
            org_id=access.org_id,
            action="webhook.redelivered",
            entity_type="webhook_delivery",
            entity_id=copy.id,
            after={"original": str(original.id), "at": datetime.now(UTC)},
        )
        await self._session.commit()
        if self._queue is not None:
            await self._queue.enqueue("deliver_webhook", str(copy.id), job_id=f"whd:{copy.id}:0")
        return copy
