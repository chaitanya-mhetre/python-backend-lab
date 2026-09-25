from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool


def make_engine(url: str, *, echo: bool = False, pooled: bool = True) -> AsyncEngine:
    if pooled:
        return create_async_engine(url, echo=echo, pool_size=10, max_overflow=5, pool_pre_ping=True)
    return create_async_engine(url, echo=echo, poolclass=NullPool)


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False: objects stay readable after commit (we return them in responses).
    return async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def session_scope(maker: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    """Commit on success, roll back on any exception. Used by the worker and scripts."""
    async with maker() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise
