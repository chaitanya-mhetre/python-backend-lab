from __future__ import annotations

from typing import Any

from httpx import AsyncClient

from tests.integration.conftest import Headers, MakeUser, post_json

API = "/api/v1"


async def setup_project(client: AsyncClient, headers: Headers) -> dict[str, Any]:
    org = await post_json(client, f"{API}/orgs", headers, {"name": "Acme Inc"})
    project: dict[str, Any] = await post_json(
        client, f"{API}/orgs/{org['id']}/projects", headers, {"name": "Launch"}
    )
    return project


async def test_org_creator_is_owner(client: AsyncClient, make_user: MakeUser) -> None:
    headers = await make_user()
    org = await post_json(client, f"{API}/orgs", headers, {"name": "Acme Inc"})
    assert org["slug"] == "acme-inc"
    orgs = (await client.get(f"{API}/orgs", headers=headers)).json()
    assert [(o["id"], o["role"]) for o in orgs] == [(org["id"], "owner")]


async def test_duplicate_org_names_get_unique_slugs(
    client: AsyncClient, make_user: MakeUser
) -> None:
    headers = await make_user()
    a = await post_json(client, f"{API}/orgs", headers, {"name": "Acme"})
    b = await post_json(client, f"{API}/orgs", headers, {"name": "Acme"})
    assert a["slug"] != b["slug"]


async def test_task_crud_and_status_rules(client: AsyncClient, make_user: MakeUser) -> None:
    headers = await make_user()
    project = await setup_project(client, headers)

    task = await post_json(
        client, f"{API}/projects/{project['id']}/tasks", headers, {"title": "  Write docs  "}
    )
    assert task["title"] == "Write docs"
    assert task["status"] == "todo" and task["version"] == 0

    # todo -> done is not allowed by the domain state machine
    r = await client.patch(f"{API}/tasks/{task['id']}", json={"status": "done"}, headers=headers)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "invalid_status_transition"

    r = await client.patch(
        f"{API}/tasks/{task['id']}", json={"status": "in_progress"}, headers=headers
    )
    assert r.status_code == 200
    assert r.json()["status"] == "in_progress" and r.json()["version"] == 1
    assert r.headers["ETag"] == '"1"'

    # PATCH only touches sent fields
    r = await client.patch(f"{API}/tasks/{task['id']}", json={"priority": 5}, headers=headers)
    assert r.json()["status"] == "in_progress" and r.json()["priority"] == 5

    assert (await client.delete(f"{API}/tasks/{task['id']}", headers=headers)).status_code == 204
    assert (await client.get(f"{API}/tasks/{task['id']}", headers=headers)).status_code == 404


async def test_non_members_get_404_not_403(client: AsyncClient, make_user: MakeUser) -> None:
    owner = await make_user()
    stranger = await make_user()
    project = await setup_project(client, owner)
    task = await post_json(client, f"{API}/projects/{project['id']}/tasks", owner, {"title": "t"})

    for url in (f"{API}/projects/{project['id']}", f"{API}/tasks/{task['id']}"):
        r = await client.get(url, headers=stranger)
        assert r.status_code == 404, url


async def test_assignee_must_be_org_member(client: AsyncClient, make_user: MakeUser) -> None:
    owner = await make_user()
    outsider_headers = await make_user()
    outsider = (await client.get(f"{API}/me", headers=outsider_headers)).json()
    project = await setup_project(client, owner)
    r = await client.post(
        f"{API}/projects/{project['id']}/tasks",
        json={"title": "t", "assignee_id": outsider["id"]},
        headers=owner,
    )
    assert r.status_code == 409


async def test_invalid_priority_is_422(client: AsyncClient, make_user: MakeUser) -> None:
    headers = await make_user()
    project = await setup_project(client, headers)
    r = await client.post(
        f"{API}/projects/{project['id']}/tasks", json={"title": "t", "priority": 9}, headers=headers
    )
    assert r.status_code == 422
