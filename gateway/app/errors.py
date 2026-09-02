"""Consistent JSON error envelope used by handlers and middleware."""

from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


def error_body(error: str, detail: str, status_code: int) -> dict[str, Any]:
    return {"error": error, "detail": detail, "status_code": status_code}


def error_response(error: str, detail: str, status_code: int, headers: dict | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=error_body(error, detail, status_code),
        headers=headers,
    )


async def http_exception_handler(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
    detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
    error = {
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
        429: "rate_limited",
        503: "service_unavailable",
    }.get(exc.status_code, "http_error")
    headers = dict(exc.headers) if exc.headers else None
    return error_response(error, detail, exc.status_code, headers=headers)


async def validation_exception_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
    return error_response("validation_error", str(exc.errors()), 422)
