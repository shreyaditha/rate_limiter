"""Reverse-proxy router forwarding gateway requests to upstream services."""

from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, Request, Response

from app.config import Settings
from app.errors import error_response
from app.schemas import UserClaims

logger = logging.getLogger(__name__)

# Hop-by-hop headers that must not be forwarded by proxies according to RFC 2616 / RFC 7230
HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
}


def build_proxy_router(settings: Settings) -> APIRouter:
    """
    Builds the FastAPI router that proxies incoming paths to upstream services.

    =============================================================================
    HOW TO ADD YOUR OWN UPSTREAM SERVICE:
    -----------------------------------------------------------------------------
    1. Define your upstream URL setting in `app/config.py` (e.g. `billing_upstream: str`).
    2. Add the URL prefix and target to the `mounts` tuple below:
         mounts = (
             ("/items", settings.example_upstream),
             ("/admin", settings.example_upstream),
             ("/billing", settings.billing_upstream),  # <--- YOUR SERVICE HERE
         )
    3. (Optional) Add RBAC rules for the new prefix in `app/auth/rbac.py`.
    =============================================================================
    """
    router = APIRouter()
    mounts = (
        ("/items", settings.example_upstream),
        ("/admin", settings.example_upstream),
    )
    for prefix, upstream in mounts:
        router.add_api_route(
            f"{prefix}/{{path:path}}",
            _make_handler(prefix, upstream),
            methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
            include_in_schema=True,
        )
        router.add_api_route(
            prefix,
            _make_handler(prefix, upstream),
            methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
            include_in_schema=False,
        )
    return router


def _make_handler(prefix: str, upstream: str):
    async def handler(request: Request, path: str = "") -> Response:
        return await _proxy(request, prefix, upstream, path)

    return handler


async def _proxy(request: Request, prefix: str, upstream: str, path: str) -> Response:
    suffix = f"/{path}" if path else request.url.path[len(prefix) :] or "/"
    if not suffix.startswith("/"):
        suffix = "/" + suffix
    url = f"{upstream.rstrip('/')}{prefix}{suffix}" if not upstream.endswith(prefix) else f"{upstream.rstrip('/')}{suffix}"
    # If the upstream base URL already includes the service or is a root service:
    # We forward path preserving prefix or suffix.
    url = f"{upstream.rstrip('/')}{suffix}"
    if prefix in {"/items", "/admin"}:
        # Route directly to the upstream path
        url = f"{upstream.rstrip('/')}{prefix}{suffix}" if suffix != "/" else f"{upstream.rstrip('/')}{prefix}"
        if path:
            url = f"{upstream.rstrip('/')}{prefix}/{path}"

    if request.url.query:
        url = f"{url}?{request.url.query}"

    body = await request.body()
    headers = {
        k: v
        for k, v in request.headers.items()
        if k.lower() not in HOP_BY_HOP and k.lower() not in {"authorization"}
    }

    # Inject identity context headers to upstream
    user: UserClaims | None = getattr(request.state, "user", None)
    if user is not None:
        headers["X-Forwarded-User"] = user.username
        headers["X-Forwarded-User-Id"] = user.user_id
        headers["X-Forwarded-Role"] = user.role

    client: httpx.AsyncClient = request.app.state.http_client
    try:
        upstream_resp = await client.request(
            request.method,
            url,
            content=body if body else None,
            headers=headers,
        )
    except httpx.RequestError as exc:
        logger.warning("Upstream %s unreachable: %s", url, exc)
        return error_response(
            "bad_gateway",
            f"Upstream service for '{prefix}' is unreachable",
            502,
        )

    response_headers = {
        k: v
        for k, v in upstream_resp.headers.items()
        if k.lower() not in HOP_BY_HOP
    }
    return Response(
        content=upstream_resp.content,
        status_code=upstream_resp.status_code,
        headers=response_headers,
    )
