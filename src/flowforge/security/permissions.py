"""Role-based access control as *data*.

The whole policy is one dictionary, so it can be reviewed at a glance, tested exhaustively
(role x action matrix), and changed without touching business logic.
"""

from __future__ import annotations

from enum import StrEnum

from flowforge.db.models import Role


class Action(StrEnum):
    ORG_READ = "org:read"
    MEMBER_MANAGE = "member:manage"
    PROJECT_READ = "project:read"
    PROJECT_CREATE = "project:create"
    TASK_READ = "task:read"
    TASK_CREATE = "task:create"
    TASK_UPDATE = "task:update"
    TASK_DELETE = "task:delete"
    WORKFLOW_READ = "workflow:read"
    WORKFLOW_MANAGE = "workflow:manage"
    API_KEY_MANAGE = "api_key:manage"
    WEBHOOK_MANAGE = "webhook:manage"
    AUDIT_READ = "audit:read"


_READ = frozenset({Action.ORG_READ, Action.PROJECT_READ, Action.TASK_READ, Action.WORKFLOW_READ})
_WRITE = _READ | {Action.TASK_CREATE, Action.TASK_UPDATE, Action.PROJECT_CREATE}
_ADMIN = (
    _WRITE
    | {Action.TASK_DELETE, Action.MEMBER_MANAGE, Action.WORKFLOW_MANAGE}
    | {Action.API_KEY_MANAGE, Action.WEBHOOK_MANAGE, Action.AUDIT_READ}
)

PERMISSIONS: dict[Role, frozenset[Action]] = {
    Role.VIEWER: _READ,
    Role.MEMBER: frozenset(_WRITE),
    Role.ADMIN: frozenset(_ADMIN),
    Role.OWNER: frozenset(Action),  # everything, including future actions
}


def can(role: Role, action: Action) -> bool:
    return action in PERMISSIONS[role]
