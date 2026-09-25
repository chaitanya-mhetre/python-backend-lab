"""Webhook delivery job with a fixed retry schedule.

Schedule after each failed attempt: 1m, 5m, 30m, 2h, 12h, then give up (6 attempts total).
Any non-2xx response, timeout or connection error counts as a failure.
"""

from __future__ import annotations

import json
import uuid
from datetime import timedelta
from typing import Any

import httpx

from flowforge.db.models import DeliveryStatus, Webhook, WebhookDelivery
from flowforge.db.session import session_scope
from flowforge.observability.metrics import WEBHOOK_DELIVERIES
from flowforge.security.webhooks import UnsafeWebhookURLError, check_url, pinned_transport, sign
from flowforge.worker.runner import WorkerDeps

RETRY_SCHEDULE: tuple[timedelta, ...] = (
    timedelta(minutes=1),
    timedelta(minutes=5),
    timedelta(minutes=30),
    timedelta(hours=2),
    timedelta(hours=12),
)


def next_retry_delay(failed_attempts: int) -> timedelta | None:
    """Delay after the Nth failed attempt, or None when we should stop retrying."""
    if 1 <= failed_attempts <= len(RETRY_SCHEDULE):
        return RETRY_SCHEDULE[failed_attempts - 1]
    return None


async def deliver_webhook(ctx: dict[str, Any], delivery_id: str) -> str:
    deps: WorkerDeps = ctx["deps"]
    assert deps.secret_box is not None, "WorkerDeps.secret_box must be configured"
    async with session_scope(deps.sessionmaker) as session:
        delivery = await session.get(WebhookDelivery, uuid.UUID(delivery_id), with_for_update=True)
        if delivery is None or delivery.status is not DeliveryStatus.PENDING:
            return "skipped"
        webhook = await session.get(Webhook, delivery.webhook_id)
        if webhook is None or not webhook.active:
            delivery.status, delivery.last_error = DeliveryStatus.FAILED, "webhook inactive"
            return delivery.status.value

        delivery.attempt += 1
        now = deps.clock()
        body = json.dumps(delivery.payload, separators=(",", ":"), sort_keys=True).encode()
        timestamp = int(now.timestamp())
        secret = deps.secret_box.decrypt(webhook.secret_encrypted)
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Flowforge-Webhooks/1.0",
            "X-Flowforge-Event": delivery.event_type,
            "X-Flowforge-Delivery": str(delivery.id),
            "X-Flowforge-Timestamp": str(timestamp),
            "X-Flowforge-Signature": sign(secret, timestamp, body),
        }
        error: str | None = None
        try:
            # Re-check at send time (the hostname may resolve differently than at creation),
            # then connect to exactly the IP we just vetted: no second DNS lookup to rebind.
            ips = await check_url(
                webhook.url, resolver=deps.resolver, allow_private=deps.allow_private_targets
            )
            async with deps.http_client(pinned_transport(webhook.url, ips)) as client:
                response = await client.post(webhook.url, content=body, headers=headers)
            delivery.status_code = response.status_code
            if not response.is_success:
                error = f"HTTP {response.status_code}"
        except UnsafeWebhookURLError as exc:
            error = str(exc)
        except httpx.HTTPError as exc:
            error = f"{type(exc).__name__}: {exc}"

        WEBHOOK_DELIVERIES.labels("success" if error is None else "failure").inc()
        if error is None:
            delivery.status, delivery.delivered_at = DeliveryStatus.SUCCEEDED, now
            delivery.last_error = delivery.next_retry_at = None
            return delivery.status.value

        delivery.last_error = error
        delay = next_retry_delay(delivery.attempt)
        if delay is None:
            delivery.status, delivery.next_retry_at = DeliveryStatus.FAILED, None
            return delivery.status.value
        delivery.next_retry_at = now + delay
        attempt = delivery.attempt

    await deps.queue.enqueue(
        "deliver_webhook",
        delivery_id,
        job_id=f"whd:{delivery_id}:{attempt}",
        defer_by=delay.total_seconds(),
    )
    return "retry_scheduled"
