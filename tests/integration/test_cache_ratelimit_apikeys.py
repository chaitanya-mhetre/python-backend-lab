from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis

from flowforge.api.app import create_app
from flowforge.config import Settings
from flowforge.jobs.queue import RecordingJobQueue
from tests.integration.conftest import Headers, MakeUser, post_json

API = "/api/v1"


@pytest.fixture
def redis_required(cache_redis: Redis | None) -> Redis:
    if cache_redis is None:
        pytest.skip("Redis not reachable; run `make up`")
    return cache_redis


async def workspace(client: AsyncClient, make_user: MakeUser) -> tuple[Headers, str, str]:
    headers = await make_user()
    org = await post_json(client, f"{API}/orgs", headers, {"name": "Acme"})
    project = await post_json(client, f"{API}/orgs/{org['id']}/projects", headers, {"name": "P"})
    return headers, org["id"], project["id"]


# --- cache-aside ----------------------------------------------------------------------------


async def test_project_stats_cached_and_invalidated_on_write(
    client: AsyncClient, make_user: MakeUser, redis_required: Redis
) -> None:
    headers, _, project_id = await workspace(client, make_user)
    url = f"{API}/projects/{project_id}"

    first = await client.get(url, headers=headers)
    second = await client.get(url, headers=headers)
    assert first.headers["X-Cache"] == "MISS" and second.headers["X-Cache"] == "HIT"
    assert second.json()["task_counts"]["todo"] == 0

    await post_json(client, f"{url}/tasks", headers, {"title": "new"})  # must invalidate
    third = await client.get(url, headers=headers)
    assert third.headers["X-Cache"] == "MISS"
    assert third.json()["task_counts"]["todo"] == 1


async def test_cache_hit_still_checks_authorization(
    client: AsyncClient, make_user: MakeUser, redis_required: Redis
) -> None:
    headers, _, project_id = await workspace(client, make_user)
    await client.get(f"{API}/projects/{project_id}", headers=headers)  # warm the cache
    stranger = await make_user()
    r = await client.get(f"{API}/projects/{project_id}", headers=stranger)
    assert r.status_code == 404


async def test_archiving_project_invalidates_and_hides_it(
    client: AsyncClient, make_user: MakeUser, redis_required: Redis
) -> None:
    headers, org_id, project_id = await workspace(client, make_user)
    await client.get(f"{API}/projects/{project_id}", headers=headers)
    r = await client.patch(f"{API}/projects/{project_id}", json={"archived": True}, headers=headers)
    assert r.status_code == 200 and r.json()["archived"] is True
    projects = (await client.get(f"{API}/orgs/{org_id}/projects", headers=headers)).json()
    assert projects == []


# --- rate limiting ------------------------------------------------------------------------


@pytest.fixture
async def limited_client(settings: Settings, redis_required: Redis) -> AsyncIterator[AsyncClient]:
    limited = settings.model_copy(
        update={"rate_limit_capacity": 5, "rate_limit_refill_per_sec": 0.01}
    )
    app = create_app(limited, pooled=False, queue=RecordingJobQueue(), redis=redis_required)
    transport = ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=transport, base_url="http://t") as client,
    ):
        yield client


async def test_rate_limit_is_exact_under_concurrency(limited_client: AsyncClient) -> None:
    body = {"email": "x@example.com", "password": "whatever-password"}
    responses = await asyncio.gather(
        *(limited_client.post(f"{API}/auth/login", json=body) for _ in range(20))
    )
    limited = [r for r in responses if r.status_code == 429]
    assert len(responses) - len(limited) == 5  # exactly the bucket size got through
    r = limited[0]
    assert r.json()["error"]["code"] == "rate_limited"
    assert int(r.headers["Retry-After"]) >= 1
    assert r.headers["X-RateLimit-Remaining"] == "0"


async def test_rate_limit_is_per_identity(limited_client: AsyncClient) -> None:
    for _ in range(5):
        await limited_client.get(f"{API}/me", headers={"Authorization": "Bearer a"})
    blocked = await limited_client.get(f"{API}/me", headers={"Authorization": "Bearer a"})
    other = await limited_client.get(f"{API}/me", headers={"Authorization": "Bearer b"})
    assert blocked.status_code == 429
    assert other.status_code == 401  # separate bucket; fails auth, not the limiter


async def test_health_endpoints_are_not_rate_limited(limited_client: AsyncClient) -> None:
    codes = {(await limited_client.get("/healthz")).status_code for _ in range(10)}
    assert codes == {200}


# --- API keys -------------------------------------------------------------------------------


async def create_key(
    client: AsyncClient, headers: Headers, org_id: str, scopes: list[str]
) -> dict[str, Any]:
    created: dict[str, Any] = await post_json(
        client, f"{API}/orgs/{org_id}/api-keys", headers, {"name": "ci bot", "scopes": scopes}
    )
    return created


async def test_api_key_is_shown_once_and_stored_hashed(
    client: AsyncClient, make_user: MakeUser
) -> None:
    headers, org_id, _ = await workspace(client, make_user)
    created = await create_key(client, headers, org_id, ["task:read"])
    assert created["key"].startswith(f"ff_live_{created['prefix']}_")
    listed = (await client.get(f"{API}/orgs/{org_id}/api-keys", headers=headers)).json()
    assert "key" not in listed[0] and "key_hash" not in listed[0]


async def test_api_key_respects_scopes_and_org(client: AsyncClient, make_user: MakeUser) -> None:
    headers, org_id, project_id = await workspace(client, make_user)
    created = await create_key(client, headers, org_id, ["task:read", "project:read"])
    bot = {"Authorization": f"Bearer {created['key']}"}

    assert (await client.get(f"{API}/projects/{project_id}/tasks", headers=bot)).status_code == 200
    r = await client.post(f"{API}/projects/{project_id}/tasks", json={"title": "x"}, headers=bot)
    assert r.status_code == 403 and "scope" in r.json()["error"]["message"]

    other_headers, _, other_project = await workspace(client, make_user)
    r = await client.get(f"{API}/projects/{other_project}/tasks", headers=bot)
    assert r.status_code == 404  # keys can't see other orgs at all

    assert (await client.get(f"{API}/me", headers=bot)).status_code == 403  # humans only


async def test_api_key_actions_are_audited_as_api_key(
    client: AsyncClient, make_user: MakeUser
) -> None:
    headers, org_id, project_id = await workspace(client, make_user)
    created = await create_key(client, headers, org_id, ["task:create"])
    bot = {"Authorization": f"Bearer {created['key']}"}
    task = await post_json(client, f"{API}/projects/{project_id}/tasks", bot, {"title": "by bot"})
    logs = (
        await client.get(
            f"{API}/orgs/{org_id}/audit-logs", params={"entity_id": task["id"]}, headers=headers
        )
    ).json()
    assert logs[0]["actor_type"] == "api_key" and logs[0]["actor_id"] == created["id"]


async def test_revoked_and_tampered_keys_are_rejected(
    client: AsyncClient, make_user: MakeUser
) -> None:
    headers, org_id, project_id = await workspace(client, make_user)
    created = await create_key(client, headers, org_id, ["task:read"])
    url = f"{API}/projects/{project_id}/tasks"

    tampered = created["key"][:-2] + ("aa" if not created["key"].endswith("aa") else "bb")
    r = await client.get(url, headers={"Authorization": f"Bearer {tampered}"})
    assert r.status_code == 401

    assert (
        await client.delete(f"{API}/api-keys/{created['id']}", headers=headers)
    ).status_code == 204
    r = await client.get(url, headers={"Authorization": f"Bearer {created['key']}"})
    assert r.status_code == 401


async def test_members_cannot_manage_api_keys(client: AsyncClient, make_user: MakeUser) -> None:
    owner, org_id, _ = await workspace(client, make_user)
    member = await make_user()
    member_email = (await client.get(f"{API}/me", headers=member)).json()["email"]
    await post_json(
        client, f"{API}/orgs/{org_id}/members", owner, {"email": member_email, "role": "member"}
    )
    r = await client.post(
        f"{API}/orgs/{org_id}/api-keys", json={"name": "x", "scopes": ["task:read"]}, headers=member
    )
    assert r.status_code == 403
