import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from tests.conftest import FakeUpstream
from tests.fake_redis import FakeRedis

pytestmark = pytest.mark.asyncio


async def test_nth_request_returns_429() -> None:
    settings = Settings(
        jwt_secret="test-secret-please-use-at-least-32b",
        rate_limit_requests=3,
        rate_limit_window_seconds=60,
        rate_limit_fail_mode="closed",
        example_upstream="http://example",
    )
    app = create_app(settings, redis=FakeRedis(), http_client=FakeUpstream())  # type: ignore[arg-type]
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        login = await client.post("/auth/login", json={"username": "alice", "password": "alicepass"})
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        statuses = []
        last = None
        for _ in range(4):
            last = await client.get("/items", headers=headers)
            statuses.append(last.status_code)

        assert statuses[:3] == [200, 200, 200]
        assert statuses[3] == 429
        assert last is not None
        body = last.json()
        assert body["error"] == "rate_limited"
        assert last.headers["Retry-After"]
        assert last.headers["X-RateLimit-Limit"] == "3"
        assert last.headers["X-RateLimit-Remaining"] == "0"
        assert "X-RateLimit-Reset" in last.headers


async def test_unauthenticated_does_not_consume_quota() -> None:
    settings = Settings(
        jwt_secret="test-secret-please-use-at-least-32b",
        rate_limit_requests=1,
        rate_limit_window_seconds=60,
        example_upstream="http://example",
    )
    redis = FakeRedis()
    app = create_app(settings, redis=redis, http_client=FakeUpstream())  # type: ignore[arg-type]
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for _ in range(5):
            denied = await client.get("/items")
            assert denied.status_code == 401

        login = await client.post("/auth/login", json={"username": "alice", "password": "alicepass"})
        token = login.json()["access_token"]
        ok = await client.get("/items", headers={"Authorization": f"Bearer {token}"})
        assert ok.status_code == 200


async def test_fail_closed_when_redis_down() -> None:
    settings = Settings(
        jwt_secret="test-secret-please-use-at-least-32b",
        rate_limit_requests=10,
        rate_limit_fail_mode="closed",
        example_upstream="http://example",
    )
    redis = FakeRedis(fail=True)
    app = create_app(settings, redis=redis, http_client=FakeUpstream())  # type: ignore[arg-type]
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        login = await client.post("/auth/login", json={"username": "alice", "password": "alicepass"})
        token = login.json()["access_token"]
        resp = await client.get("/items", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 503
        assert resp.json()["error"] == "service_unavailable"
