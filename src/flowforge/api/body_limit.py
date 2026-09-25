"""Reject oversized request bodies early (413) instead of reading them into memory."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response

from flowforge.api.errors import error_response


def install_body_limit(app: FastAPI, max_bytes: int) -> None:
    @app.middleware("http")
    async def body_limit(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        declared = request.headers.get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > max_bytes:
            return error_response(413, "payload_too_large", f"body exceeds {max_bytes} bytes")
        return await call_next(request)
