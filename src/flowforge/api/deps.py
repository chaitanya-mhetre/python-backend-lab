"""FastAPI dependencies: the DI wiring between HTTP and services.

``Depends`` builds a small graph per request: settings -> DB session -> current user -> service.
Tests swap any node with ``app.dependency_overrides``.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from flowforge.config import Settings
from flowforge.context import Actor, ActorType, actor_var
from flowforge.db.models import User
from flowforge.domain.errors import AuthenticationError, PermissionDeniedError
from flowforge.jobs.queue import JobQueue
from flowforge.repositories.users import UserRepository
from flowforge.security.api_keys import looks_like_api_key
from flowforge.security.principal import Principal
from flowforge.security.tokens import decode_access_token
from flowforge.services.access import AccessResolver, OrgAccess
from flowforge.services.api_keys import ApiKeyService
from flowforge.services.cache import ProjectStatsCache
from flowforge.services.webhooks import WebhookService

_bearer = HTTPBearer(auto_error=False)


def get_settings_dep(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """One session per request. Services commit; anything uncommitted is rolled back on close."""
    maker: async_sessionmaker[AsyncSession] = request.app.state.sessionmaker
    async with maker() as session:
        yield session


def get_queue(request: Request) -> JobQueue:
    queue: JobQueue = request.app.state.queue
    return queue


def get_cache(request: Request) -> ProjectStatsCache:
    settings: Settings = request.app.state.settings
    return ProjectStatsCache(request.app.state.redis, settings.cache_ttl_seconds)


SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
CacheDep = Annotated[ProjectStatsCache, Depends(get_cache)]
QueueDep = Annotated[JobQueue, Depends(get_queue)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def get_principal(
    session: SessionDep,
    settings: SettingsDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal:
    """Authenticates either a JWT (user) or an ``ff_live_...`` API key (machine)."""
    if credentials is None:
        raise AuthenticationError("missing bearer token")
    token = credentials.credentials
    if looks_like_api_key(token):
        key = await ApiKeyService(session).authenticate(token)
        actor_var.set(Actor(ActorType.API_KEY, key.id))
        return Principal(api_key=key)
    user_id = decode_access_token(token, settings)
    user = await UserRepository(session).get(user_id)
    if user is None:
        raise AuthenticationError("user no longer exists")
    actor_var.set(Actor(ActorType.USER, user.id))
    return Principal(user=user)


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]


async def get_current_user(principal: CurrentPrincipal) -> User:
    """For endpoints that only make sense for humans (/me, creating orgs, managing keys)."""
    if principal.user is None:
        raise PermissionDeniedError("this endpoint requires a user login, not an API key")
    return principal.user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_org_access(
    org_id: uuid.UUID, principal: CurrentPrincipal, session: SessionDep
) -> OrgAccess:
    """For routes under ``/orgs/{org_id}/...``: resolves the caller's role in that org."""
    return await AccessResolver(session).for_org(principal, org_id)


OrgAccessDep = Annotated[OrgAccess, Depends(get_org_access)]


def get_webhook_service(request: Request, session: SessionDep) -> WebhookService:
    settings: Settings = request.app.state.settings
    return WebhookService(
        session,
        secret_box=request.app.state.secret_box,
        resolver=request.app.state.resolver,
        allow_private=settings.webhook_allow_private_targets,
        queue=request.app.state.queue,
    )


WebhookServiceDep = Annotated[WebhookService, Depends(get_webhook_service)]
