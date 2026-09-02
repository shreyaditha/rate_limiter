import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def test_login_success(client: AsyncClient) -> None:
    resp = await client.post("/auth/login", json={"username": "alice", "password": "alicepass"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["role"] == "admin"
    assert body["access_token"]


async def test_login_rejects_bad_password(client: AsyncClient) -> None:
    resp = await client.post("/auth/login", json={"username": "alice", "password": "nope"})
    assert resp.status_code == 401
    body = resp.json()
    assert body["error"] == "unauthorized"
    assert body["status_code"] == 401


async def test_protected_route_requires_auth(client: AsyncClient) -> None:
    resp = await client.get("/orders")
    assert resp.status_code == 401
    assert resp.json()["error"] == "unauthorized"


async def test_login_then_access_me(client: AsyncClient) -> None:
    login = await client.post("/auth/login", json={"username": "bob", "password": "bobpass"})
    token = login.json()["access_token"]
    resp = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["username"] == "bob"
    assert body["role"] == "user"


async def test_api_key_authenticates(client: AsyncClient) -> None:
    resp = await client.get("/auth/me", headers={"X-API-Key": "bob-user-key"})
    assert resp.status_code == 200
    assert resp.json()["username"] == "bob"


async def test_expired_or_garbage_jwt_is_rejected(client: AsyncClient) -> None:
    resp = await client.get("/auth/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert resp.status_code == 401
