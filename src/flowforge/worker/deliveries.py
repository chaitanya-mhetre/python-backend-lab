"""Creating webhook delivery rows (used by the event dispatcher and the call_webhook step).

Deliveries are written in the *same transaction* as whatever caused them, then a
``deliver_webhook`` job is enqueued after commit. If that enqueue is lost, the row still
exists with status 'pending': `scripts/requeue_pending.py`-style recovery can find it.
That is a mini outbox: the database is the source of truth, the queue is just a trigger.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowforge.db.models import DeliveryStatus, Webhook, WebhookDelivery


async def create_deliveries(
    session: AsyncSession,
    org_id: uuid.UUID,
    event_type: str,
    data: dict[str, Any],
    *,
    webhook_id: uuid.UUID | None = None,
) -> list[WebhookDelivery]:
    query = select(Webhook).where(Webhook.org_id == org_id, Webhook.active.is_(True))
    if webhook_id is not None:
        query = query.where(Webhook.id == webhook_id)
    else:
        query = query.where(Webhook.events.contains([event_type]))  # events @> ARRAY[type]
    deliveries = []
    for webhook in await session.scalars(query):
        delivery_id = uuid.uuid4()
        delivery = WebhookDelivery(
            id=delivery_id,
            webhook_id=webhook.id,
            event_type=event_type,
            payload={"id": str(delivery_id), "type": event_type, "data": data},
            status=DeliveryStatus.PENDING,
            attempt=0,
        )
        session.add(delivery)
        deliveries.append(delivery)
    await session.flush()
    return deliveries
