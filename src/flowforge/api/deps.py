"""FastAPI dependencies: the DI wiring between HTTP and services.

``Depends`` builds a small graph per request: settings -> DB session -> current user -> service.
Tests swap any node with ``app.dependency_overrides``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from flowforge.config import Settings
from flowforge.context import Actor, ActorType, actor_var
from flowforge.db.models import User
from flowforge.domain.errors import AuthenticationError
from flowforge.repositories.users import UserRepository
from flowforge.security.tokens import decode_access_token

_bearer = HTTPBearer(auto_error=False)


def get_settings_dep(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """One session per request. Services commit; anything uncommitted is rolled back on close."""
    maker: async_sessionmaker[AsyncSession] = request.app.state.sessionmaker
    async with maker() as session:
        yield session


SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def get_current_user(
    session: SessionDep,
    settings: SettingsDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    if credentials is None:
        raise AuthenticationError("missing bearer token")
    user_id = decode_access_token(credentials.credentials, settings)
    user = await UserRepository(session).get(user_id)
    if user is None:
        raise AuthenticationError("user no longer exists")
    actor_var.set(Actor(ActorType.USER, user.id))
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
