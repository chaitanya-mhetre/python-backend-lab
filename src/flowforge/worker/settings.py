"""arq worker entrypoint: ``arq flowforge.worker.settings.WorkerSettings``."""

from __future__ import annotations

from typing import Any, ClassVar

from arq.connections import RedisSettings

from flowforge.config import get_settings
from flowforge.db.session import make_engine, make_sessionmaker
from flowforge.jobs.queue import ArqJobQueue
from flowforge.worker.runner import WorkerDeps, dispatch_event, run_execution


async def startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    engine = make_engine(settings.database_url)
    ctx["engine"] = engine
    ctx["deps"] = WorkerDeps(
        sessionmaker=make_sessionmaker(engine),
        queue=ArqJobQueue(ctx["redis"]),
    )


async def shutdown(ctx: dict[str, Any]) -> None:
    await ctx["engine"].dispose()


class WorkerSettings:
    functions: ClassVar[list[Any]] = [dispatch_event, run_execution]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    max_jobs = 20
    job_timeout = 300
