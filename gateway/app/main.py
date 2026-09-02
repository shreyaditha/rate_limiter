"""
API Gateway entrypoint.

Middleware registration order (Starlette runs last-added first on the request):
    app.add_middleware(RateLimitMiddleware)  # inner — runs second
    app.add_middleware(AuthMiddleware)       # outer — runs first

Inbound path:  Auth/RBAC → Rate limit → route handler
So 401/403 never increment the Redis sliding window.
"""

from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from redis.asyncio import Redis
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.auth.jwt import create_access_token
from app.auth.users import authenticate
from app.config import Settings, get_settings
from app.errors import http_exception_handler, validation_exception_handler
from app.limiter.sliding_window import SlidingWindowRateLimiter
from app.middleware.auth import AuthMiddleware
from app.middleware.ratelimit import RateLimitMiddleware
from app.proxy.router import build_proxy_router
from app.schemas import LoginRequest, TokenResponse, UserClaims


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    redis = getattr(app.state, "redis_override", None)
    http_client = getattr(app.state, "http_client_override", None)
    owns_redis = redis is None
    owns_http = http_client is None
    if redis is None:
        redis = Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            protocol=2,  # Redis 5 (Windows portable) has no RESP3 HELLO
        )
    if http_client is None:
        http_client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))

    app.state.redis = redis
    app.state.limiter = SlidingWindowRateLimiter(
        redis,
        limit=settings.rate_limit_requests,
        window_ms=settings.rate_limit_window_ms,
    )
    app.state.http_client = http_client
    try:
        yield
    finally:
        if owns_http:
            await http_client.aclose()
        if owns_redis:
            await redis.aclose()


def create_app(
    settings: Settings | None = None,
    *,
    redis: Redis | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(
        title="Distributed Rate Limiter / API Gateway",
        description=(
            "JWT + API-key auth, role-based access control, and a Redis "
            "sliding-window rate limiter in front of mock microservices."
        ),
        version="1.0.0",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.redis_override = redis
    app.state.http_client_override = http_client
    # Tests inject FakeRedis before lifespan runs; attach the limiter immediately
    # so ASGITransport (lifespan off) still exercises the real middleware path.
    if redis is not None:
        app.state.redis = redis
        app.state.limiter = SlidingWindowRateLimiter(
            redis,
            limit=settings.rate_limit_requests,
            window_ms=settings.rate_limit_window_ms,
        )
    if http_client is not None:
        app.state.http_client = http_client

    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)

    # Inner middleware added first, outer last — see module docstring.
    app.add_middleware(RateLimitMiddleware, settings=settings)
    app.add_middleware(AuthMiddleware, settings=settings)

    @app.get("/health", tags=["meta"])
    async def health() -> dict:
        redis_ok = False
        try:
            redis_ok = bool(await app.state.redis.ping())
        except Exception:
            redis_ok = False
        status = "ok" if redis_ok or not settings.fail_closed else "degraded"
        return {
            "status": status,
            "redis": "up" if redis_ok else "down",
            "rate_limit": {
                "requests": settings.rate_limit_requests,
                "window_seconds": settings.rate_limit_window_seconds,
                "fail_mode": settings.rate_limit_fail_mode,
            },
        }

    @app.post("/auth/login", response_model=TokenResponse, tags=["auth"])
    async def login(body: LoginRequest) -> TokenResponse:
        user = authenticate(body.username, body.password)
        if user is None:
            raise HTTPException(status_code=401, detail="Invalid username or password")
        token = create_access_token(user, settings)
        return TokenResponse(
            access_token=token,
            expires_in=settings.jwt_expire_minutes * 60,
            role=user.role,
            username=user.username,
        )

    @app.get("/auth/me", tags=["auth"])
    async def me(request: Request) -> UserClaims:
        return request.state.user

    app.include_router(build_proxy_router(settings), tags=["proxy"])
    return app


app = create_app()
