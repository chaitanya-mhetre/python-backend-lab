"""Authorization at the service layer.

1. ``AccessResolver`` turns "user + some resource id" into an :class:`OrgAccess`
   (which org, which role). Non-members get 404 so strangers can't probe for ids.
2. ``@requires(Action.X)`` guards service methods whose first argument is an ``OrgAccess``.

``requires`` is a *decorator factory*: ``requires(action)`` returns a decorator, which
returns the wrapper. ``ParamSpec`` + ``Concatenate`` keep the wrapped method's exact signature
visible to mypy, so callers still get full type checking.
"""

from __future__ import annotations

import functools
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Concatenate

from sqlalchemy.ext.asyncio import AsyncSession

from flowforge.db.models import (
    Project,
    Role,
    TaskRow,
    Webhook,
    WorkflowDefinitionRow,
    WorkflowExecutionRow,
)
from flowforge.domain.errors import NotFoundError, PermissionDeniedError
from flowforge.repositories.orgs import OrgRepository
from flowforge.security.permissions import Action, can
from flowforge.security.principal import Principal


@dataclass(frozen=True, slots=True)
class OrgAccess:
    user_id: uuid.UUID
    org_id: uuid.UUID
    role: Role
    # API keys are limited to explicit scopes instead of a role's full permission set.
    scopes: frozenset[Action] | None = None

    def require(self, action: Action) -> None:
        if self.scopes is not None:
            if action not in self.scopes:
                raise PermissionDeniedError(f"API key lacks scope {action.value}")
            return
        if not can(self.role, action):
            raise PermissionDeniedError(f"role {self.role.value!r} may not perform {action.value}")


def requires[S, **P, R](
    action: Action,
) -> Callable[
    [Callable[Concatenate[S, OrgAccess, P], Awaitable[R]]],
    Callable[Concatenate[S, OrgAccess, P], Awaitable[R]],
]:
    def decorator(
        method: Callable[Concatenate[S, OrgAccess, P], Awaitable[R]],
    ) -> Callable[Concatenate[S, OrgAccess, P], Awaitable[R]]:
        @functools.wraps(method)  # keeps __name__/__doc__ for logs and OpenAPI
        async def wrapper(self: S, access: OrgAccess, /, *args: P.args, **kwargs: P.kwargs) -> R:
            access.require(action)
            return await method(self, access, *args, **kwargs)

        wrapper.required_action = action  # type: ignore[attr-defined]  # introspection in tests
        return wrapper

    return decorator


class AccessResolver:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def for_org(self, principal: Principal, org_id: uuid.UUID) -> OrgAccess:
        key = principal.api_key
        if key is not None:
            if key.org_id != org_id:  # a key only ever sees its own org
                raise NotFoundError("organization", org_id)
            scopes = frozenset(Action(s) for s in key.scopes)
            return OrgAccess(key.created_by, org_id, Role.VIEWER, scopes=scopes)
        assert principal.user is not None
        membership = await OrgRepository(self._session).get_membership(org_id, principal.user.id)
        if membership is None:
            raise NotFoundError("organization", org_id)
        return OrgAccess(user_id=principal.user.id, org_id=org_id, role=membership.role)

    async def for_project(self, principal: Principal, project_id: uuid.UUID) -> OrgAccess:
        project = await self._session.get(Project, project_id)
        if project is None:
            raise NotFoundError("project", project_id)
        try:
            return await self.for_org(principal, project.org_id)
        except NotFoundError:
            raise NotFoundError("project", project_id) from None

    async def for_task(self, principal: Principal, task_id: uuid.UUID) -> OrgAccess:
        task = await self._session.get(TaskRow, task_id)
        if task is None:
            raise NotFoundError("task", task_id)
        try:
            return await self.for_project(principal, task.project_id)
        except NotFoundError:
            raise NotFoundError("task", task_id) from None

    async def for_workflow(self, principal: Principal, definition_id: uuid.UUID) -> OrgAccess:
        row = await self._session.get(WorkflowDefinitionRow, definition_id)
        if row is None:
            raise NotFoundError("workflow", definition_id)
        try:
            return await self.for_org(principal, row.org_id)
        except NotFoundError:
            raise NotFoundError("workflow", definition_id) from None

    async def for_execution(self, principal: Principal, execution_id: uuid.UUID) -> OrgAccess:
        row = await self._session.get(WorkflowExecutionRow, execution_id)
        if row is None:
            raise NotFoundError("execution", execution_id)
        try:
            return await self.for_org(principal, row.org_id)
        except NotFoundError:
            raise NotFoundError("execution", execution_id) from None

    async def for_webhook(self, principal: Principal, webhook_id: uuid.UUID) -> OrgAccess:
        row = await self._session.get(Webhook, webhook_id)
        if row is None:
            raise NotFoundError("webhook", webhook_id)
        try:
            return await self.for_org(principal, row.org_id)
        except NotFoundError:
            raise NotFoundError("webhook", webhook_id) from None
