"""One error format for every endpoint: ``{"error": {"code", "message", "request_id"}}``."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from flowforge.context import request_id_var
from flowforge.domain.errors import (
    AuthenticationError,
    ConflictError,
    DomainError,
    InvalidTransitionError,
    NotFoundError,
    PermissionDeniedError,
)

# Order matters: the first matching class wins, so subclasses come before their parents.
STATUS_BY_ERROR: list[tuple[type[DomainError], int]] = [
    (NotFoundError, 404),
    (AuthenticationError, 401),
    (PermissionDeniedError, 403),
    (ConflictError, 409),
    (InvalidTransitionError, 409),
]


def error_response(status: int, code: str, message: str) -> JSONResponse:
    body = {"error": {"code": code, "message": message, "request_id": request_id_var.get()}}
    headers = {"WWW-Authenticate": "Bearer"} if status == 401 else None
    return JSONResponse(body, status_code=status, headers=headers)


def status_for(exc: DomainError) -> int:
    for cls, status in STATUS_BY_ERROR:
        if isinstance(exc, cls):
            return status
    return 422


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _domain(_: Request, exc: DomainError) -> JSONResponse:
        return error_response(status_for(exc), exc.code, str(exc))

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(p) for p in first.get("loc", ()))
        return error_response(422, "validation_error", f"{where}: {first.get('msg', 'invalid')}")

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "not_found", 405: "method_not_allowed", 429: "rate_limited"}.get(
            exc.status_code, "http_error"
        )
        response = error_response(exc.status_code, code, str(exc.detail))
        for key, value in (exc.headers or {}).items():
            response.headers[key] = value
        return response
