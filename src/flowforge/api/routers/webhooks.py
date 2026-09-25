from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from flowforge.api.deps import CurrentPrincipal, OrgAccessDep, SessionDep, WebhookServiceDep
from flowforge.api.schemas import DeliveryOut, WebhookCreatedOut, WebhookIn, WebhookOut
from flowforge.db.models import WebhookDelivery
from flowforge.domain.errors import NotFoundError
from flowforge.services.access import AccessResolver

router = APIRouter(tags=["webhooks"])


@router.post(
    "/orgs/{org_id}/webhooks", response_model=WebhookCreatedOut, status_code=status.HTTP_201_CREATED
)
async def create_webhook(
    body: WebhookIn, access: OrgAccessDep, service: WebhookServiceDep
) -> WebhookCreatedOut:
    webhook, secret = await service.create(access, body.url, list(body.events))
    return WebhookCreatedOut(**WebhookOut.model_validate(webhook).model_dump(), secret=secret)


@router.get("/orgs/{org_id}/webhooks", response_model=list[WebhookOut])
async def list_webhooks(access: OrgAccessDep, service: WebhookServiceDep) -> object:
    return await service.list_all(access)


@router.delete("/webhooks/{webhook_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_webhook(
    webhook_id: uuid.UUID,
    principal: CurrentPrincipal,
    session: SessionDep,
    service: WebhookServiceDep,
) -> None:
    access = await AccessResolver(session).for_webhook(principal, webhook_id)
    await service.deactivate(access, webhook_id)


@router.get("/webhooks/{webhook_id}/deliveries", response_model=list[DeliveryOut])
async def list_deliveries(
    webhook_id: uuid.UUID,
    principal: CurrentPrincipal,
    session: SessionDep,
    service: WebhookServiceDep,
) -> object:
    access = await AccessResolver(session).for_webhook(principal, webhook_id)
    return await service.deliveries(access, webhook_id)


@router.post("/webhook-deliveries/{delivery_id}:redeliver", response_model=DeliveryOut)
async def redeliver(
    delivery_id: uuid.UUID,
    principal: CurrentPrincipal,
    session: SessionDep,
    service: WebhookServiceDep,
) -> object:
    delivery = await session.get(WebhookDelivery, delivery_id)
    if delivery is None:
        raise NotFoundError("delivery", delivery_id)
    access = await AccessResolver(session).for_webhook(principal, delivery.webhook_id)
    return await service.redeliver(access, delivery_id)
