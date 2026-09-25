"""arq worker entrypoint: ``arq flowforge.worker.settings.WorkerSettings``."""

from __future__ import annotations

from typing import Any, ClassVar

from arq.connections import RedisSettings
from prometheus_client import start_http_server

from flowforge.config import get_settings
from flowforge.db.session import make_engine, make_sessionmaker
from flowforge.jobs.queue import ArqJobQueue
from flowforge.observability.logging import configure_logging
from flowforge.security.webhooks import SecretBox
from flowforge.worker.runner import WorkerDeps, dispatch_event, run_execution
from flowforge.worker.webhooks import deliver_webhook


async def startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    configure_logging(settings.log_level, json=settings.log_json)
    start_http_server(settings.worker_metrics_port)  # Prometheus scrapes the worker here
    engine = make_engine(settings.database_url)
    ctx["engine"] = engine
    ctx["deps"] = WorkerDeps(
        sessionmaker=make_sessionmaker(engine),
        queue=ArqJobQueue(ctx["redis"]),
        secret_box=SecretBox(settings.secret_encryption_key.get_secret_value()),
        allow_private_targets=settings.webhook_allow_private_targets,
    )


async def shutdown(ctx: dict[str, Any]) -> None:
    await ctx["engine"].dispose()


class WorkerSettings:
    functions: ClassVar[list[Any]] = [dispatch_event, run_execution, deliver_webhook]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    max_jobs = 20
    job_timeout = 300
