"""
Authentication + RBAC middleware.

Runs *before* rate limiting (registered last — Starlette executes last-added
middleware first) so missing/invalid tokens never consume Redis quota.

Identity can be established in two ways:
  1. Authorization: Bearer <jwt>  (issued by POST /auth/login)
  2. X-API-Key: <key>             (demo keys mapped in the in-memory user store)

JWT is the primary auth mechanism. X-API-Key is an alternative identification
path that also authenticates in this demo so either header is sufficient.
"""

from jwt import ExpiredSignatureError, InvalidTokenError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.auth.jwt import decode_access_token
from app.auth.rbac import is_allowed
from app.auth.users import get_user_by_api_key
from app.config import Settings
from app.errors import error_response
from app.schemas import UserClaims

PUBLIC_PATHS = {
    "/auth/login",
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
}


def _is_public(path: str) -> bool:
    if path in PUBLIC_PATHS:
        return True
    return path.startswith("/docs") or path.startswith("/redoc")


class AuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, settings: Settings) -> None:
        super().__init__(app)
        self._settings = settings

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.method == "OPTIONS" or _is_public(request.url.path):
            return await call_next(request)

        claims = self._authenticate(request)
        if claims is None:
            return error_response(
                "unauthorized",
                "Missing or invalid credentials. Provide a Bearer JWT or X-API-Key.",
                401,
            )

        request.state.user = claims
        # Preserve the raw API key (if any) so the rate limiter can key on it.
        request.state.api_key = request.headers.get("x-api-key")

        if not is_allowed(claims, request.method, request.url.path):
            return error_response(
                "forbidden",
                f"Role '{claims.role}' is not allowed to {request.method} {request.url.path}",
                403,
            )

        return await call_next(request)

    def _authenticate(self, request: Request) -> UserClaims | None:
        api_key = request.headers.get("x-api-key")
        if api_key:
            user = get_user_by_api_key(api_key)
            if user is not None:
                return UserClaims(
                    user_id=user.user_id,
                    username=user.username,
                    role=user.role,
                    api_key=user.api_key,
                )
            # Unknown key is not a valid identity.
            return None

        auth = request.headers.get("authorization")
        if not auth or not auth.lower().startswith("bearer "):
            return None
        token = auth.split(" ", 1)[1].strip()
        if not token:
            return None
        try:
            return decode_access_token(token, self._settings)
        except ExpiredSignatureError:
            return None
        except InvalidTokenError:
            return None
