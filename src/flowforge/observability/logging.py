"""Structured JSON logging with request/actor context attached to every line.

``merge_contextvars`` + our own processor pull ``request_id`` and the actor from the
contextvars set by the middleware, so a log line written deep inside a service still says
which request and which user caused it, without passing them around.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

from flowforge.context import actor_var, request_id_var


def _add_request_context(_: Any, __: str, event: dict[str, Any]) -> dict[str, Any]:
    request_id = request_id_var.get()
    if request_id:
        event.setdefault("request_id", request_id)
    actor = actor_var.get()
    event.setdefault("actor", f"{actor.type.value}:{actor.id}" if actor.id else actor.type.value)
    return event


def configure_logging(level: str = "INFO", *, json: bool = True) -> None:
    shared: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _add_request_context,
    ]
    renderer: Any = structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer()
    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
    handler = logging.StreamHandler(sys.stdout)
    # stdlib loggers (uvicorn, sqlalchemy, our `logging.getLogger`) go through the same pipeline
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                renderer,
            ],
        )
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
