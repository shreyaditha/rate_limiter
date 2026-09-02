"""
Rate-limit middleware. Must run AFTER auth (see main.py registration order).

On Redis errors: RATE_LIMIT_FAIL_MODE=closed returns 503; open lets the
request through without incrementing a counter.
"""

import logging

from redis.exceptions import RedisError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.config import Settings
from app.errors import error_response
from app.limiter.sliding_window import SlidingWindowRateLimiter, identity_key
from app.middleware.auth import _is_public
from app.schemas import UserClaims

logger = logging.getLogger(__name__)

RATE_LIMIT_HEADERS = ("X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset")


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, settings: Settings, limiter: SlidingWindowRateLimiter | None = None) -> None:
        super().__init__(app)
        self._settings = settings
        self._limiter = limiter

    def set_limiter(self, limiter: SlidingWindowRateLimiter) -> None:
        self._limiter = limiter

    def _resolve_limiter(self, request: Request) -> SlidingWindowRateLimiter | None:
        if self._limiter is not None:
            return self._limiter
        # request.app may be a middleware wrapper, not the FastAPI instance.
        seen: set[int] = set()
        current = request.app
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            limiter = getattr(getattr(current, "state", None), "limiter", None)
            if limiter is not None:
                return limiter
            current = getattr(current, "app", None)
        return None

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.method == "OPTIONS" or _is_public(request.url.path):
            return await call_next(request)

        user: UserClaims | None = getattr(request.state, "user", None)
        if user is None:
            # Auth middleware should have rejected already; do not count this.
            return await call_next(request)

        limiter = self._resolve_limiter(request)
        if limiter is None:
            logger.error("Rate limiter is not initialized")
            if self._settings.fail_closed:
                return error_response("service_unavailable", "Rate limiter unavailable", 503)
            return await call_next(request)

        api_key = getattr(request.state, "api_key", None)
        key = identity_key(user_id=user.user_id, api_key=api_key)

        try:
            result = await limiter.hit(key)
        except (RedisError, OSError) as exc:
            logger.warning("Redis rate-limit check failed: %s", exc)
            if self._settings.fail_closed:
                return error_response(
                    "service_unavailable",
                    "Rate limiter backend unavailable",
                    503,
                )
            return await call_next(request)

        headers = {
            "X-RateLimit-Limit": str(result.limit),
            "X-RateLimit-Remaining": str(result.remaining),
            "X-RateLimit-Reset": str(result.reset_at),
        }

        if not result.allowed:
            headers["Retry-After"] = str(result.retry_after)
            return error_response(
                "rate_limited",
                f"Rate limit exceeded: {result.limit} requests per "
                f"{self._settings.rate_limit_window_seconds} seconds",
                429,
                headers=headers,
            )

        response = await call_next(request)
        for name, value in headers.items():
            response.headers[name] = value
        return response
