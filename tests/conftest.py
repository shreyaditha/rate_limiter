from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from tests.fake_redis import FakeRedis


class FakeUpstream:
    """Stand-in for mock microservices during gateway tests."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        self.calls.append((method, url))
        request = httpx.Request(method, url)
        if "/users" in url:
            payload = {"users": [{"id": "usr_alice", "username": "alice"}]}
        elif "/inventory" in url:
            payload = {"items": [{"sku": "SKU-WIDGET", "on_hand": 42}]}
        else:
            payload = {"orders": [{"id": "ord_1001", "status": "shipped"}]}
        return httpx.Response(200, json=payload, request=request)

    async def aclose(self) -> None:
        return None


@pytest.fixture
def settings() -> Settings:
    return Settings(
        jwt_secret="test-secret-please-use-at-least-32b",
        redis_url="redis://localhost:6379/0",
        rate_limit_requests=5,
        rate_limit_window_seconds=60,
        rate_limit_fail_mode="closed",
        orders_upstream="http://orders",
        inventory_upstream="http://inventory",
        users_upstream="http://users",
    )


@pytest.fixture
def fake_redis() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
def fake_http() -> FakeUpstream:
    return FakeUpstream()


@pytest.fixture
async def client(settings: Settings, fake_redis: FakeRedis, fake_http: FakeUpstream) -> AsyncIterator[AsyncClient]:
    app = create_app(settings, redis=fake_redis, http_client=fake_http)  # type: ignore[arg-type]
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
