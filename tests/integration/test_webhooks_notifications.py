from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import httpx
import respx
from httpx import AsyncClient
from sqlalchemy import select

from flowforge.db.models import DeliveryStatus, Webhook, WebhookDelivery
from flowforge.jobs.queue import RecordingJobQueue
from flowforge.security.webhooks import verify
from flowforge.worker.runner import WorkerDeps
from tests.integration.conftest import FROZEN_NOW, Headers, MakeUser, drain, post_json

API = "/api/v1"
HOOK_URL = "https://hooks.example.com/flowforge"


async def workspace(client: AsyncClient, make_user: MakeUser) -> tuple[Headers, str, str]:
    headers = await make_user()
    org = await post_json(client, f"{API}/orgs", headers, {"name": "Acme"})
    project = await post_json(client, f"{API}/orgs/{org['id']}/projects", headers, {"name": "P"})
    return headers, org["id"], project["id"]


async def create_hook(
    client: AsyncClient, headers: Headers, org_id: str, events: list[str] | None = None
) -> dict[str, Any]:
    created: dict[str, Any] = await post_json(
        client,
        f"{API}/orgs/{org_id}/webhooks",
        headers,
        {"url": HOOK_URL, "events": events or ["task.created"]},
    )
    return created


async def deliveries(deps: WorkerDeps) -> list[WebhookDelivery]:
    async with deps.sessionmaker() as s:
        return list(await s.scalars(select(WebhookDelivery).order_by(WebhookDelivery.created_at)))


async def test_secret_shown_once_and_encrypted_at_rest(
    client: AsyncClient, make_user: MakeUser, worker_deps: WorkerDeps
) -> None:
    headers, org_id, _ = await workspace(client, make_user)
    created = await create_hook(client, headers, org_id)
    assert created["secret"].startswith("whsec_")
    listed = (await client.get(f"{API}/orgs/{org_id}/webhooks", headers=headers)).json()
    assert "secret" not in listed[0]
    async with worker_deps.sessionmaker() as s:
        stored = await s.scalar(select(Webhook.secret_encrypted))
    assert stored and created["secret"] not in stored


async def test_private_targets_rejected(client: AsyncClient, make_user: MakeUser) -> None:
    headers, org_id, _ = await workspace(client, make_user)
    r = await client.post(
        f"{API}/orgs/{org_id}/webhooks",
        json={"url": "https://internal.example.com/x", "events": ["task.created"]},
        headers=headers,
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "unsafe_webhook_url"


@respx.mock
async def test_event_delivered_with_valid_signature(
    client: AsyncClient, make_user: MakeUser, queue: RecordingJobQueue, worker_deps: WorkerDeps
) -> None:
    route = respx.post(HOOK_URL).mock(return_value=httpx.Response(204))
    headers, org_id, project_id = await workspace(client, make_user)
    hook = await create_hook(client, headers, org_id)
    task = await post_json(client, f"{API}/projects/{project_id}/tasks", headers, {"title": "t"})

    await drain(worker_deps, queue)

    assert route.call_count == 1
    request = route.calls.last.request
    body = request.content
    ts = int(request.headers["X-Flowforge-Timestamp"])
    assert ts == int(FROZEN_NOW.timestamp())
    assert verify(hook["secret"], ts, body, request.headers["X-Flowforge-Signature"], now=ts)
    payload = json.loads(body)
    assert payload["type"] == "task.created" and payload["data"]["task_id"] == task["id"]
    [delivery] = await deliveries(worker_deps)
    assert delivery.status is DeliveryStatus.SUCCEEDED and delivery.status_code == 204


@respx.mock
async def test_unsubscribed_events_are_not_sent(
    client: AsyncClient, make_user: MakeUser, queue: RecordingJobQueue, worker_deps: WorkerDeps
) -> None:
    route = respx.post(HOOK_URL).mock(return_value=httpx.Response(200))
    headers, org_id, project_id = await workspace(client, make_user)
    await create_hook(client, headers, org_id, events=["task.status_changed"])
    await post_json(client, f"{API}/projects/{project_id}/tasks", headers, {"title": "t"})
    await drain(worker_deps, queue)
    assert route.call_count == 0


@respx.mock
async def test_failing_receiver_follows_retry_schedule_then_gives_up(
    client: AsyncClient, make_user: MakeUser, queue: RecordingJobQueue, worker_deps: WorkerDeps
) -> None:
    route = respx.post(HOOK_URL).mock(return_value=httpx.Response(500))
    headers, org_id, project_id = await workspace(client, make_user)
    await create_hook(client, headers, org_id)
    await post_json(client, f"{API}/projects/{project_id}/tasks", headers, {"title": "t"})

    await drain(worker_deps, queue, run_deferred=False)
    [delivery] = await deliveries(worker_deps)
    assert delivery.attempt == 1 and delivery.status is DeliveryStatus.PENDING
    assert delivery.next_retry_at == FROZEN_NOW + timedelta(minutes=1)
    assert [j.defer_by for j in queue.jobs] == [60]

    await drain(worker_deps, queue)  # run every scheduled retry
    [delivery] = await deliveries(worker_deps)
    assert route.call_count == 6  # 1 try + 5 retries
    assert delivery.status is DeliveryStatus.FAILED and delivery.last_error == "HTTP 500"


@respx.mock
async def test_network_error_is_retried(
    client: AsyncClient, make_user: MakeUser, queue: RecordingJobQueue, worker_deps: WorkerDeps
) -> None:
    respx.post(HOOK_URL).mock(side_effect=[httpx.ConnectTimeout("slow"), httpx.Response(200)])
    headers, org_id, project_id = await workspace(client, make_user)
    await create_hook(client, headers, org_id)
    await post_json(client, f"{API}/projects/{project_id}/tasks", headers, {"title": "t"})
    await drain(worker_deps, queue)
    [delivery] = await deliveries(worker_deps)
    assert delivery.status is DeliveryStatus.SUCCEEDED and delivery.attempt == 2


@respx.mock
async def test_call_webhook_step_and_redeliver(
    client: AsyncClient, make_user: MakeUser, queue: RecordingJobQueue, worker_deps: WorkerDeps
) -> None:
    route = respx.post(HOOK_URL).mock(return_value=httpx.Response(200))
    headers, org_id, project_id = await workspace(client, make_user)
    hook = await create_hook(client, headers, org_id, events=["task.status_changed"])
    await post_json(
        client,
        f"{API}/orgs/{org_id}/workflows",
        headers,
        {
            "name": "ping",
            "trigger_type": "task.created",
            "steps": [{"type": "call_webhook", "config": {"webhook_id": hook["id"]}}],
        },
    )
    await post_json(client, f"{API}/projects/{project_id}/tasks", headers, {"title": "t"})
    await drain(worker_deps, queue)

    [delivery] = await deliveries(worker_deps)
    assert delivery.event_type == "workflow.task.created"
    assert route.call_count == 1

    r = await client.post(f"{API}/webhook-deliveries/{delivery.id}:redeliver", headers=headers)
    assert r.status_code == 200
    await drain(worker_deps, queue)
    assert route.call_count == 2
    sent_ids = {json.loads(c.request.content)["id"] for c in route.calls}
    assert len(sent_ids) == 1  # same event id: receivers can deduplicate

    listed = (await client.get(f"{API}/webhooks/{hook['id']}/deliveries", headers=headers)).json()
    assert len(listed) == 2


async def test_assignment_creates_notification_for_assignee_only(
    client: AsyncClient, make_user: MakeUser
) -> None:
    owner, org_id, project_id = await workspace(client, make_user)
    member = await make_user()
    me = (await client.get(f"{API}/me", headers=member)).json()
    await post_json(
        client, f"{API}/orgs/{org_id}/members", owner, {"email": me["email"], "role": "member"}
    )
    task = await post_json(client, f"{API}/projects/{project_id}/tasks", owner, {"title": "Fix"})
    await client.patch(f"{API}/tasks/{task['id']}", json={"assignee_id": me["id"]}, headers=owner)

    unread = (await client.get(f"{API}/notifications?unread=true", headers=member)).json()
    assert [(n["kind"], n["payload"]["title"]) for n in unread] == [("task_assigned", "Fix")]
    assert (await client.get(f"{API}/notifications", headers=owner)).json() == []

    nid = unread[0]["id"]
    assert (await client.post(f"{API}/notifications/{nid}:read", headers=owner)).status_code == 404
    r = await client.post(f"{API}/notifications/{nid}:read", headers=member)
    assert r.json()["read_at"] is not None
    assert (await client.get(f"{API}/notifications?unread=true", headers=member)).json() == []


async def test_oversized_body_rejected(client: AsyncClient) -> None:
    r = await client.post(
        f"{API}/auth/register",
        content=b"x" * 1_000_001,
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 413
    assert r.json()["error"]["code"] == "payload_too_large"
