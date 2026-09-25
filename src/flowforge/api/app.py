"""Application factory. ``create_app()`` builds a fresh app so tests can pass their own settings."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response

from flowforge.api.errors import install_error_handlers
from flowforge.api.routers import auth, health, orgs, projects, tasks
from flowforge.config import Settings, get_settings
from flowforge.context import request_id_var
from flowforge.db.session import make_engine, make_sessionmaker


def create_app(settings: Settings | None = None, *, pooled: bool = True) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = make_engine(settings.database_url, echo=settings.sql_echo, pooled=pooled)
        app.state.engine = engine
        app.state.sessionmaker = make_sessionmaker(engine)
        yield
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
    for module in (auth, orgs, projects, tasks):
        app.include_router(module.router, prefix="/api/v1")
    return app
