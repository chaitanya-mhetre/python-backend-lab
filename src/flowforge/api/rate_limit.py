"""Rate-limit middleware for ``/api/*``.

Identity: a hash of the bearer credential (so each user / API key gets its own bucket), or the
client IP for anonymous calls (login, register). Runs before authentication on purpose:
brute-forcing the login endpoint must be throttled too.
"""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response

from flowforge.api.errors import error_response
from flowforge.security.rate_limit import TokenBucketLimiter


def client_identity(request: Request) -> str:
    auth = request.headers.get("Authorization")
    if auth:
        return "cred:" + hashlib.sha256(auth.encode()).hexdigest()[:24]
    host = request.client.host if request.client else "unknown"
    return f"ip:{host}"


def install_rate_limit(app: FastAPI) -> None:
    @app.middleware("http")
    async def rate_limit(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        limiter: TokenBucketLimiter | None = getattr(request.app.state, "limiter", None)
        if limiter is None or not request.url.path.startswith("/api/"):
            return await call_next(request)
        result = await limiter.hit(client_identity(request))
        if not result.allowed:
            response: Response = error_response(429, "rate_limited", "too many requests")
            response.headers["Retry-After"] = str(max(1, result.retry_after_seconds))
        else:
            response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(limiter.capacity)
        response.headers["X-RateLimit-Remaining"] = str(result.remaining)
        return response
