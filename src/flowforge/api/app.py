"""Application factory. ``create_app()`` builds a fresh app so tests can pass their own settings."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings
from fastapi import FastAPI, Request, Response

from flowforge.api.errors import install_error_handlers
from flowforge.api.routers import auth, health, orgs, projects, tasks, workflows
from flowforge.config import Settings, get_settings
from flowforge.context import request_id_var
from flowforge.db.session import make_engine, make_sessionmaker
from flowforge.jobs.queue import ArqJobQueue, JobQueue


def create_app(
    settings: Settings | None = None, *, pooled: bool = True, queue: JobQueue | None = None
) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = make_engine(settings.database_url, echo=settings.sql_echo, pooled=pooled)
        app.state.engine = engine
        app.state.sessionmaker = make_sessionmaker(engine)
        redis: ArqRedis | None = None
        if queue is None:
            redis = await create_pool(RedisSettings.from_dsn(settings.redis_url))
            app.state.queue = ArqJobQueue(redis)
        else:
            app.state.queue = queue
        yield
        if redis is not None:
            await redis.aclose()
        await engine.dispose()

    app = FastAPI(title="Flowforge", version="0.2.0", lifespan=lifespan)
    app.state.settings = settings

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
    app.include_router(health.router)
    for module in (auth, orgs, projects, tasks, workflows):
        app.include_router(module.router, prefix="/api/v1")
    return app
