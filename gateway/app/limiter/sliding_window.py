"""
Atomic sliding-window rate limiter backed by a Redis sorted set.

Each identity maps to one ZSET. Member = unique request id, score = unix ms.
A Lua script performs evict + count + (optional) increment in one EVAL so
concurrent gateway workers cannot over-admit.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from hashlib import sha256

from redis.asyncio import Redis

# KEYS[1]  rate-limit key
# ARGV[1]  now_ms
# ARGV[2]  window_ms
# ARGV[3]  max_requests
# ARGV[4]  unique member (request id)
#
# Returns: {allowed, current_count, remaining, reset_at_ms}
#   allowed = 1 if the request is admitted, else 0
SLIDING_WINDOW_LUA = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local max_requests = tonumber(ARGV[3])
local request_id = ARGV[4]
local cutoff = now - window

-- Drop timestamps that have fallen out of the sliding window.
redis.call('ZREMRANGEBYSCORE', key, 0, cutoff)

local current = redis.call('ZCARD', key)

if current >= max_requests then
    local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
    local reset_at = now + window
    if #oldest >= 2 then
        reset_at = tonumber(oldest[2]) + window
    end
    return {0, current, 0, reset_at}
end

redis.call('ZADD', key, now, request_id)
redis.call('PEXPIRE', key, window)

local new_count = current + 1
local remaining = max_requests - new_count
local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
local reset_at = now + window
if #oldest >= 2 then
    reset_at = tonumber(oldest[2]) + window
end

return {1, new_count, remaining, reset_at}
"""


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    limit: int
    remaining: int
    # Unix epoch seconds when the oldest request in the window expires.
    reset_at: int
    retry_after: int


def identity_key(*, user_id: str, api_key: str | None) -> str:
    """
    Rate-limit identity.

    X-API-Key, when present, is hashed so raw secrets never land in Redis.
    Otherwise the JWT subject (user id) is used. Both are namespaced so a
    user id cannot collide with an API-key hash.
    """
    if api_key:
        digest = sha256(api_key.encode("utf-8")).hexdigest()[:24]
        return f"rl:apikey:{digest}"
    return f"rl:user:{user_id}"


def _to_int(value: object) -> int:
    if isinstance(value, (bytes, bytearray)):
        return int(value.decode())
    return int(value)


class SlidingWindowRateLimiter:
    def __init__(self, redis: Redis, *, limit: int, window_ms: int) -> None:
        self._redis = redis
        self.limit = limit
        self.window_ms = window_ms
        self._script = SLIDING_WINDOW_LUA

    async def hit(self, identity: str, *, now_ms: int | None = None) -> RateLimitResult:
        now = now_ms if now_ms is not None else int(time.time() * 1000)
        request_id = f"{now}:{uuid.uuid4().hex}"
        raw = await self._redis.eval(
            self._script,
            1,
            identity,
            now,
            self.window_ms,
            self.limit,
            request_id,
        )
        allowed = _to_int(raw[0]) == 1
        remaining = max(0, _to_int(raw[2]))
        reset_at_ms = _to_int(raw[3])
        reset_at = reset_at_ms // 1000
        retry_after = max(1, (reset_at_ms - now + 999) // 1000)
        return RateLimitResult(
            allowed=allowed,
            limit=self.limit,
            remaining=remaining,
            reset_at=reset_at,
            retry_after=retry_after,
        )
