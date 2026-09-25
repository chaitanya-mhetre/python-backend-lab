from __future__ import annotations

from httpx import AsyncClient

from tests.integration.conftest import MakeUser

API = "/api/v1"


async def test_register_login_me(client: AsyncClient, make_user: MakeUser) -> None:
    headers = await make_user("Alice@Example.com")
    r = await client.get(f"{API}/me", headers=headers)
    assert r.status_code == 200
    assert r.json()["email"] == "Alice@example.com"  # EmailStr lowercases the domain
    assert "password_hash" not in r.json()


async def test_email_is_case_insensitive_unique(client: AsyncClient, make_user: MakeUser) -> None:
    await make_user("bob@example.com")
    r = await client.post(
        f"{API}/auth/register",
        json={"email": "BOB@example.com", "password": "another-long-pw", "full_name": "B"},
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "conflict"


async def test_wrong_password_and_unknown_email_look_the_same(
    client: AsyncClient, make_user: MakeUser
) -> None:
    await make_user("carol@example.com")
    wrong = await client.post(
        f"{API}/auth/login", json={"email": "carol@example.com", "password": "nope-nope-nope"}
    )
    unknown = await client.post(
        f"{API}/auth/login", json={"email": "nobody@example.com", "password": "nope-nope-nope"}
    )
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["error"]["message"] == unknown.json()["error"]["message"]


async def test_protected_route_requires_valid_token(client: AsyncClient) -> None:
    assert (await client.get(f"{API}/me")).status_code == 401
    r = await client.get(f"{API}/me", headers={"Authorization": "Bearer garbage"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "authentication_failed"
    assert r.headers["WWW-Authenticate"] == "Bearer"


async def test_short_password_rejected_with_error_format(client: AsyncClient) -> None:
    r = await client.post(
        f"{API}/auth/register",
        json={"email": "d@example.com", "password": "short", "full_name": "D"},
        headers={"X-Request-ID": "req-123"},
    )
    assert r.status_code == 422
    body = r.json()["error"]
    assert body["code"] == "validation_error"
    assert body["request_id"] == "req-123"
    assert r.headers["X-Request-ID"] == "req-123"


async def test_health_endpoints(client: AsyncClient) -> None:
    assert (await client.get("/healthz")).json() == {"status": "ok"}
    assert (await client.get("/readyz")).status_code == 200


async def test_metrics_endpoint_exposes_prometheus_text(client: AsyncClient) -> None:
    await client.get("/api/v1/me")
    r = await client.get("/metrics")
    assert r.status_code == 200
    assert "http_requests_total" in r.text or "http_request_duration" in r.text
