from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from tests.integration.conftest import MakeUser, post_json
from tests.integration.test_projects_tasks import setup_project

API = "/api/v1"


async def collect_all(
    client: AsyncClient, url: str, headers: dict[str, str], **params: Any
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    cursor = None
    while True:
        query = {**params, **({"cursor": cursor} if cursor else {})}
        r = await client.get(url, params=query, headers=headers)
        assert r.status_code == 200, r.text
        body = r.json()
        items.extend(body["items"])
        cursor = body["next_cursor"]
        if cursor is None:
            return items


@pytest.mark.parametrize("sort", ["created_at", "-created_at", "priority", "-priority", "title"])
async def test_keyset_pages_cover_every_row_exactly_once(
    client: AsyncClient, make_user: MakeUser, sort: str
) -> None:
    headers = await make_user()
    project = await setup_project(client, headers)
    url = f"{API}/projects/{project['id']}/tasks"
    for i in range(23):
        # many equal priorities: the id tie-breaker must keep the order total
        await post_json(client, url, headers, {"title": f"task {i:02d}", "priority": i % 3 + 1})

    items = await collect_all(client, url, headers, sort=sort, limit=5)

    ids = [t["id"] for t in items]
    assert len(ids) == 23 and len(set(ids)) == 23
    field = sort.removeprefix("-")
    values = [t[field] for t in items]
    assert values == sorted(values, reverse=sort.startswith("-"))


async def test_rows_inserted_while_paging_do_not_cause_duplicates(
    client: AsyncClient, make_user: MakeUser
) -> None:
    headers = await make_user()
    project = await setup_project(client, headers)
    url = f"{API}/projects/{project['id']}/tasks"
    for i in range(6):
        await post_json(client, url, headers, {"title": f"t{i}"})

    first = (await client.get(url, params={"limit": 3}, headers=headers)).json()
    await post_json(client, url, headers, {"title": "inserted later"})
    second = (
        await client.get(url, params={"limit": 10, "cursor": first["next_cursor"]}, headers=headers)
    ).json()

    first_ids = {t["id"] for t in first["items"]}
    assert first_ids.isdisjoint(t["id"] for t in second["items"])
    assert [t["title"] for t in second["items"]] == ["t3", "t4", "t5", "inserted later"]


async def test_filters(client: AsyncClient, make_user: MakeUser) -> None:
    headers = await make_user()
    me = (await client.get(f"{API}/me", headers=headers)).json()
    project = await setup_project(client, headers)
    url = f"{API}/projects/{project['id']}/tasks"
    mine = await post_json(client, url, headers, {"title": "mine", "assignee_id": me["id"]})
    await post_json(client, url, headers, {"title": "unassigned"})
    await client.patch(f"{API}/tasks/{mine['id']}", json={"status": "in_progress"}, headers=headers)

    r = await client.get(url, params={"assignee": "me"}, headers=headers)
    assert [t["title"] for t in r.json()["items"]] == ["mine"]
    r = await client.get(url, params={"status": "todo"}, headers=headers)
    assert [t["title"] for t in r.json()["items"]] == ["unassigned"]


async def test_bad_sort_and_cursor_are_rejected(client: AsyncClient, make_user: MakeUser) -> None:
    headers = await make_user()
    project = await setup_project(client, headers)
    url = f"{API}/projects/{project['id']}/tasks"
    r = await client.get(url, params={"sort": "password"}, headers=headers)
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_sort"
    r = await client.get(url, params={"cursor": "!!!notbase64"}, headers=headers)
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_cursor"
