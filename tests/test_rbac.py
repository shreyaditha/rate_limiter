import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def _token(client: AsyncClient, username: str, password: str) -> str:
    resp = await client.post("/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200
    return resp.json()["access_token"]


async def test_user_cannot_access_admin_route(client: AsyncClient) -> None:
    token = await _token(client, "bob", "bobpass")
    resp = await client.get("/admin/metrics", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403
    body = resp.json()
    assert body["error"] == "forbidden"
    assert body["status_code"] == 403


async def test_user_cannot_write_items(client: AsyncClient) -> None:
    token = await _token(client, "bob", "bobpass")
    resp = await client.post(
        "/items",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Widget", "category": "hardware", "price": 10.0},
    )
    assert resp.status_code == 403
    body = resp.json()
    assert body["error"] == "forbidden"


async def test_user_can_read_items(client: AsyncClient) -> None:
    token = await _token(client, "bob", "bobpass")
    resp = await client.get("/items", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert "items" in resp.json()


async def test_admin_can_access_admin_route(client: AsyncClient) -> None:
    token = await _token(client, "alice", "alicepass")
    resp = await client.get("/admin/metrics", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert "metrics" in resp.json()


async def test_admin_can_write_items(client: AsyncClient) -> None:
    token = await _token(client, "alice", "alicepass")
    resp = await client.post(
        "/items",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Widget", "category": "hardware", "price": 10.0},
    )
    assert resp.status_code == 200
