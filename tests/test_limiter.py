import pytest

from app.limiter.sliding_window import SlidingWindowRateLimiter, identity_key
from tests.fake_redis import FakeRedis


@pytest.mark.asyncio
async def test_allows_requests_under_limit() -> None:
    redis = FakeRedis()
    limiter = SlidingWindowRateLimiter(redis, limit=3, window_ms=1000)
    now = 10_000
    for i in range(3):
        result = await limiter.hit("rl:user:a", now_ms=now + i)
        assert result.allowed is True
        assert result.remaining == 3 - (i + 1)


@pytest.mark.asyncio
async def test_rejects_request_over_limit() -> None:
    redis = FakeRedis()
    limiter = SlidingWindowRateLimiter(redis, limit=3, window_ms=1000)
    now = 10_000
    for _ in range(3):
        await limiter.hit("rl:user:a", now_ms=now)
    denied = await limiter.hit("rl:user:a", now_ms=now + 1)
    assert denied.allowed is False
    assert denied.remaining == 0
    assert denied.retry_after >= 1


@pytest.mark.asyncio
async def test_window_slides_and_frees_quota() -> None:
    redis = FakeRedis()
    limiter = SlidingWindowRateLimiter(redis, limit=2, window_ms=1000)
    await limiter.hit("rl:user:a", now_ms=1000)
    await limiter.hit("rl:user:a", now_ms=1100)
    denied = await limiter.hit("rl:user:a", now_ms=1500)
    assert denied.allowed is False
    # Oldest score=1000 expires at 2000; at now=2001 that entry is outside the window.
    allowed = await limiter.hit("rl:user:a", now_ms=2001)
    assert allowed.allowed is True


@pytest.mark.asyncio
async def test_identities_are_isolated() -> None:
    redis = FakeRedis()
    limiter = SlidingWindowRateLimiter(redis, limit=1, window_ms=1000)
    a = await limiter.hit("rl:user:a", now_ms=1)
    b = await limiter.hit("rl:user:b", now_ms=1)
    assert a.allowed and b.allowed
    a2 = await limiter.hit("rl:user:a", now_ms=2)
    assert a2.allowed is False


def test_identity_prefers_hashed_api_key() -> None:
    user_only = identity_key(user_id="usr_alice", api_key=None)
    with_key = identity_key(user_id="usr_alice", api_key="alice-admin-key")
    assert user_only.startswith("rl:user:")
    assert with_key.startswith("rl:apikey:")
    assert user_only != with_key
    assert "alice-admin-key" not in with_key
