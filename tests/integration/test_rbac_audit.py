from __future__ import annotations

import asyncio
from typing import Any

import pytest
from httpx import AsyncClient

from tests.integration.conftest import Headers, MakeUser, post_json

API = "/api/v1"


async def org_with_member(
    client: AsyncClient, make_user: MakeUser, role: str
) -> tuple[Headers, Headers, dict[str, Any], dict[str, Any]]:
    owner = await make_user()
    member = await make_user()
    member_email = (await client.get(f"{API}/me", headers=member)).json()["email"]
    org = await post_json(client, f"{API}/orgs", owner, {"name": "Acme"})
    await post_json(
        client, f"{API}/orgs/{org['id']}/members", owner, {"email": member_email, "role": role}
    )
    project = await post_json(client, f"{API}/orgs/{org['id']}/projects", owner, {"name": "P"})
    return owner, member, org, project


@pytest.mark.parametrize(
    ("role", "can_create", "can_delete", "can_manage_members", "can_read_audit"),
    [
        ("viewer", False, False, False, False),
        ("member", True, False, False, False),
        ("admin", True, True, True, True),
    ],
)
async def test_role_action_matrix_over_http(
    client: AsyncClient,
    make_user: MakeUser,
    role: str,
    can_create: bool,
    can_delete: bool,
    can_manage_members: bool,
    can_read_audit: bool,
) -> None:
    owner, member, org, project = await org_with_member(client, make_user, role)
    task = await post_json(client, f"{API}/projects/{project['id']}/tasks", owner, {"title": "t"})

    def expect(allowed: bool, ok: int) -> int:
        return ok if allowed else 403

    # everyone in the org can read
    r = await client.get(f"{API}/tasks/{task['id']}", headers=member)
    assert r.status_code == 200

    r = await client.post(
        f"{API}/projects/{project['id']}/tasks", json={"title": "x"}, headers=member
    )
    assert r.status_code == expect(can_create, 201)

    third = await make_user()
    third_email = (await client.get(f"{API}/me", headers=third)).json()["email"]
    r = await client.post(
        f"{API}/orgs/{org['id']}/members", json={"email": third_email}, headers=member
    )
    assert r.status_code == expect(can_manage_members, 201)

    r = await client.get(f"{API}/orgs/{org['id']}/audit-logs", headers=member)
    assert r.status_code == expect(can_read_audit, 200)

    r = await client.delete(f"{API}/tasks/{task['id']}", headers=member)
    assert r.status_code == expect(can_delete, 204)
    if r.status_code == 403:
        assert r.json()["error"]["code"] == "permission_denied"


async def test_admin_cannot_grant_owner_and_last_owner_is_protected(
    client: AsyncClient, make_user: MakeUser
) -> None:
    owner, admin, org, _ = await org_with_member(client, make_user, "admin")
    me_owner = (await client.get(f"{API}/me", headers=owner)).json()
    me_admin = (await client.get(f"{API}/me", headers=admin)).json()

    r = await client.patch(
        f"{API}/orgs/{org['id']}/members/{me_admin['id']}", json={"role": "owner"}, headers=admin
    )
    assert r.status_code == 403

    r = await client.delete(f"{API}/orgs/{org['id']}/members/{me_owner['id']}", headers=owner)
    assert r.status_code == 409  # the only owner can't leave


async def test_audit_log_written_with_actor_and_request_id(
    client: AsyncClient, make_user: MakeUser
) -> None:
    owner = await make_user()
    me = (await client.get(f"{API}/me", headers=owner)).json()
    org = await post_json(client, f"{API}/orgs", owner, {"name": "Acme"})
    project = await post_json(client, f"{API}/orgs/{org['id']}/projects", owner, {"name": "P"})
    task = await post_json(client, f"{API}/projects/{project['id']}/tasks", owner, {"title": "t"})
    await client.patch(
        f"{API}/tasks/{task['id']}",
        json={"status": "in_progress"},
        headers={**owner, "X-Request-ID": "audit-req-1"},
    )

    r = await client.get(
        f"{API}/orgs/{org['id']}/audit-logs",
        params={"entity_type": "task", "entity_id": task["id"]},
        headers=owner,
    )
    logs = r.json()
    assert [entry["action"] for entry in logs] == ["task.updated", "task.created"]  # newest first
    update = logs[0]
    assert update["actor_type"] == "user" and update["actor_id"] == me["id"]
    assert update["request_id"] == "audit-req-1"
    assert update["before"]["status"] == "todo" and update["after"]["status"] == "in_progress"


async def test_failed_change_leaves_no_audit_row(client: AsyncClient, make_user: MakeUser) -> None:
    owner = await make_user()
    org = await post_json(client, f"{API}/orgs", owner, {"name": "Acme"})
    project = await post_json(client, f"{API}/orgs/{org['id']}/projects", owner, {"name": "P"})
    task = await post_json(client, f"{API}/projects/{project['id']}/tasks", owner, {"title": "t"})
    r = await client.patch(f"{API}/tasks/{task['id']}", json={"status": "done"}, headers=owner)
    assert r.status_code == 409
    logs = (
        await client.get(
            f"{API}/orgs/{org['id']}/audit-logs",
            params={"entity_id": task["id"]},
            headers=owner,
        )
    ).json()
    assert [entry["action"] for entry in logs] == ["task.created"]


async def test_stale_if_match_gets_409(client: AsyncClient, make_user: MakeUser) -> None:
    owner = await make_user()
    org = await post_json(client, f"{API}/orgs", owner, {"name": "Acme"})
    project = await post_json(client, f"{API}/orgs/{org['id']}/projects", owner, {"name": "P"})
    task = await post_json(client, f"{API}/projects/{project['id']}/tasks", owner, {"title": "t"})
    url = f"{API}/tasks/{task['id']}"

    assert (
        await client.patch(url, json={"priority": 4}, headers={**owner, "If-Match": '"0"'})
    ).status_code == 200
    r = await client.patch(url, json={"priority": 5}, headers={**owner, "If-Match": '"0"'})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "task_version_conflict"


async def test_concurrent_updates_one_wins_one_conflicts(
    client: AsyncClient, make_user: MakeUser
) -> None:
    owner = await make_user()
    org = await post_json(client, f"{API}/orgs", owner, {"name": "Acme"})
    project = await post_json(client, f"{API}/orgs/{org['id']}/projects", owner, {"name": "P"})
    task = await post_json(client, f"{API}/projects/{project['id']}/tasks", owner, {"title": "t"})
    url = f"{API}/tasks/{task['id']}"
    headers = {**owner, "If-Match": '"0"'}

    results = await asyncio.gather(
        *(client.patch(url, json={"title": f"edit {i}"}, headers=headers) for i in range(5))
    )

    codes = sorted(r.status_code for r in results)
    assert codes == [200, 409, 409, 409, 409]
    final = (await client.get(url, headers=owner)).json()
    assert final["version"] == 1
