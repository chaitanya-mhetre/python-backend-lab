"""Refresh-token rotation and reuse detection (issue #1)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select

from flowforge.config import Settings
from flowforge.db.models import RefreshToken
from flowforge.domain.errors import AuthenticationError
from flowforge.services.auth import AuthService
from tests.integration.conftest import MakeUser

API = "/api/v1"


async def _login(client: AsyncClient, make_user: MakeUser, email: str) -> dict[str, Any]:
    await make_user(email)
    r = await client.post(
        f"{API}/auth/login", json={"email": email, "password": "correct-horse-battery"}
    )
    assert r.status_code == 200, r.text
    body: dict[str, Any] = r.json()
    return body


async def _refresh(client: AsyncClient, token: str) -> Any:
    return await client.post(f"{API}/auth/refresh", json={"refresh_token": token})


async def test_login_returns_refresh_token_and_ttl(
    client: AsyncClient, make_user: MakeUser
) -> None:
    body = await _login(client, make_user, "rt1@example.com")
    assert body["refresh_token"] and body["access_token"]
    assert body["expires_in"] == 15 * 60


async def test_refresh_rotates_and_new_access_token_works(
    client: AsyncClient, make_user: MakeUser
) -> None:
    first = await _login(client, make_user, "rt2@example.com")
    r = await _refresh(client, first["refresh_token"])
    assert r.status_code == 200, r.text
    second = r.json()
    assert second["refresh_token"] != first["refresh_token"]
    me = await client.get(
        f"{API}/me", headers={"Authorization": f"Bearer {second['access_token']}"}
    )
    assert me.status_code == 200
    # the child can be refreshed in turn
    assert (await _refresh(client, second["refresh_token"])).status_code == 200


async def test_reused_token_revokes_the_whole_family(
    client: AsyncClient, make_user: MakeUser, app: FastAPI
) -> None:
    first = await _login(client, make_user, "rt3@example.com")
    second = (await _refresh(client, first["refresh_token"])).json()

    # Replaying the spent token (what an attacker with a stolen copy would do)...
    replay = await _refresh(client, first["refresh_token"])
    assert replay.status_code == 401
    assert "reuse" in replay.json()["error"]["message"]

    # ...kills the legitimate client's newer token too: the whole family is revoked.
    assert (await _refresh(client, second["refresh_token"])).status_code == 401
    async with app.state.sessionmaker() as session:
        rows = (await session.scalars(select(RefreshToken))).all()
    families: dict[object, list[RefreshToken]] = {}
    for row in rows:
        families.setdefault(row.family_id, []).append(row)
    rotated = [f for f in families.values() if len(f) == 2]
    assert len(rotated) == 1 and all(row.revoked_at is not None for row in rotated[0])
    # the separate login done by the make_user fixture is a different family: untouched
    others = [row for f in families.values() if len(f) == 1 for row in f]
    assert others and all(row.revoked_at is None for row in others)


async def test_reuse_only_revokes_that_family(client: AsyncClient, make_user: MakeUser) -> None:
    laptop = await _login(client, make_user, "rt4@example.com")
    phone = (
        await client.post(
            f"{API}/auth/login",
            json={"email": "rt4@example.com", "password": "correct-horse-battery"},
        )
    ).json()
    await _refresh(client, laptop["refresh_token"])
    await _refresh(client, laptop["refresh_token"])  # replay -> laptop family revoked
    assert (await _refresh(client, phone["refresh_token"])).status_code == 200


async def test_logout_revokes_and_is_idempotent(client: AsyncClient, make_user: MakeUser) -> None:
    body = await _login(client, make_user, "rt5@example.com")
    for _ in range(2):
        r = await client.post(f"{API}/auth/logout", json={"refresh_token": body["refresh_token"]})
        assert r.status_code == 204
    assert (await _refresh(client, body["refresh_token"])).status_code == 401


async def test_unknown_and_expired_tokens_rejected(
    client: AsyncClient, make_user: MakeUser, app: FastAPI, settings: Settings
) -> None:
    assert (await _refresh(client, "x" * 43)).status_code == 401
    body = await _login(client, make_user, "rt6@example.com")
    later = datetime.now(UTC) + timedelta(days=settings.refresh_ttl_days, seconds=1)
    async with app.state.sessionmaker() as session:
        service = AuthService(session, settings, clock=lambda: later)
        try:
            await service.refresh(body["refresh_token"])
        except AuthenticationError as exc:
            assert "expired" in str(exc)
        else:
            raise AssertionError("expired refresh token was accepted")


async def test_concurrent_refresh_with_same_token_cannot_fork_the_family(
    client: AsyncClient, make_user: MakeUser, app: FastAPI
) -> None:
    body = await _login(client, make_user, "rt7@example.com")
    results = await asyncio.gather(*(_refresh(client, body["refresh_token"]) for _ in range(2)))
    codes = sorted(r.status_code for r in results)
    # SELECT ... FOR UPDATE serialises them: one wins, the other is a replay.
    assert codes == [200, 401]
