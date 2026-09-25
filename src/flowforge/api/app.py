"""Application factory. ``create_app()`` builds a fresh app so tests can pass their own settings."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import cast

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings
from fastapi import FastAPI, Request, Response
from redis.asyncio import Redis

from flowforge.api.body_limit import install_body_limit
from flowforge.api.errors import install_error_handlers
from flowforge.api.rate_limit import install_rate_limit
from flowforge.api.routers import (
    api_keys,
    auth,
    health,
    notifications,
    orgs,
    projects,
    tasks,
    webhooks,
    workflows,
)
from flowforge.config import Settings, get_settings
from flowforge.context import request_id_var
from flowforge.db.session import make_engine, make_sessionmaker
from flowforge.jobs.queue import ArqJobQueue, JobQueue
from flowforge.security.rate_limit import TokenBucketLimiter
from flowforge.security.webhooks import Resolver, SecretBox, system_resolver

_USE_SETTINGS = object()


def create_app(
    settings: Settings | None = None,
    *,
    pooled: bool = True,
    queue: JobQueue | None = None,
    redis: Redis | None | object = _USE_SETTINGS,
    resolver: Resolver = system_resolver,
) -> FastAPI:
    """``redis``: omit to connect using settings; pass a client (tests) or ``None`` to disable
    caching and rate limiting."""
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = make_engine(settings.database_url, echo=settings.sql_echo, pooled=pooled)
        app.state.engine = engine
        app.state.sessionmaker = make_sessionmaker(engine)
        arq_redis: ArqRedis | None = None
        if queue is None:
            arq_redis = await create_pool(RedisSettings.from_dsn(settings.redis_url))
            app.state.queue = ArqJobQueue(arq_redis)
        else:
            app.state.queue = queue
        own_cache_redis = redis is _USE_SETTINGS
        cache_redis = (
            Redis.from_url(settings.redis_url) if own_cache_redis else cast("Redis | None", redis)
        )
        app.state.redis = cache_redis
        app.state.limiter = (
            TokenBucketLimiter(
                cache_redis, settings.rate_limit_capacity, settings.rate_limit_refill_per_sec
            )
            if cache_redis is not None
            else None
        )
        yield
        if own_cache_redis and cache_redis is not None:
            await cache_redis.aclose()
        if arq_redis is not None:
            await arq_redis.aclose()
        await engine.dispose()

    app = FastAPI(title="Flowforge", version="0.2.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.secret_box = SecretBox(settings.secret_encryption_key.get_secret_value())
    app.state.resolver = resolver

    @app.middleware("http")
    async def request_id_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        token = request_id_var.set(request_id)
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers["X-Request-ID"] = request_id
        return response

    install_error_handlers(app)
    install_body_limit(app, settings.max_request_body_bytes)
    install_rate_limit(app)  # added last = runs first (outermost), before auth
    app.include_router(health.router)
    for module in (auth, orgs, projects, tasks, workflows, api_keys, webhooks, notifications):
        app.include_router(module.router, prefix="/api/v1")
    return app
