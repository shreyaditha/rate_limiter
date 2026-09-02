"""Reverse-proxy hop from the gateway to a mock upstream service."""

from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, Request, Response

from app.config import Settings
from app.errors import error_response
from app.schemas import UserClaims

logger = logging.getLogger(__name__)

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
    router = APIRouter()
    mounts = (
        ("/orders", settings.orders_upstream),
        ("/inventory", settings.inventory_upstream),
        ("/users", settings.users_upstream),
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
    url = f"{upstream.rstrip('/')}{suffix}"
    if request.url.query:
        url = f"{url}?{request.url.query}"

    body = await request.body()
    headers = {
        k: v
        for k, v in request.headers.items()
        if k.lower() not in HOP_BY_HOP and k.lower() not in {"authorization"}
    }

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
