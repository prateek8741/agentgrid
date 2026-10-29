"""Consistent JSON error envelope: {"error": {"code", "message", "request_id", "details"}}."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_logger, request_id_var
from app.core.tenancy import TenantIsolationError

log = get_logger(__name__)


class AppError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(self, message: str, *, details: Any = None, status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details
        if status_code is not None:
            self.status_code = status_code


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"


class ForbiddenError(AppError):
    status_code = 403
    code = "forbidden"


class AuthError(AppError):
    status_code = 401
    code = "unauthorized"


class RateLimitedError(AppError):
    status_code = 429
    code = "rate_limited"

    def __init__(self, message: str, *, retry_after: int) -> None:
        super().__init__(message, details={"retry_after": retry_after})
        self.retry_after = retry_after


class QuotaExceededError(AppError):
    status_code = 429
    code = "quota_exceeded"


_HTTP_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    415: "unsupported_media_type",
    422: "validation_error",
    429: "rate_limited",
}


def error_response(
    status: int, code: str, message: str, details: Any = None, headers: dict[str, str] | None = None
) -> JSONResponse:
    body: dict[str, Any] = {"error": {"code": code, "message": message, "request_id": request_id_var.get()}}
    if details is not None:
        body["error"]["details"] = details
    return JSONResponse(status_code=status, content=body, headers=headers)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_req: Request, exc: AppError) -> JSONResponse:
        headers = {"Retry-After": str(exc.retry_after)} if isinstance(exc, RateLimitedError) else None
        return error_response(exc.status_code, exc.code, exc.message, exc.details, headers)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_req: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_CODES.get(exc.status_code, "error")
        return error_response(exc.status_code, code, str(exc.detail), headers=getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_req: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {"loc": list(err.get("loc", [])), "msg": err.get("msg", ""), "type": err.get("type", "")}
            for err in exc.errors()
        ]
        return error_response(422, "validation_error", "Request validation failed", details)

    @app.exception_handler(TenantIsolationError)
    async def _tenant_error(_req: Request, exc: TenantIsolationError) -> JSONResponse:
        log.error("tenant isolation violation blocked: %s", exc)
        return error_response(403, "forbidden", "Cross-tenant access denied")

    @app.exception_handler(Exception)
    async def _unhandled(_req: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error: %s", type(exc).__name__)
        return error_response(500, "internal_error", "An unexpected error occurred")
