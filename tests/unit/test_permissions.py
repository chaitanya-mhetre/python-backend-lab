from __future__ import annotations

import uuid

import pytest

from flowforge.db.models import Role
from flowforge.domain.errors import PermissionDeniedError
from flowforge.security.permissions import PERMISSIONS, Action, can
from flowforge.services.access import OrgAccess, requires
from flowforge.services.orgs import OrgService
from flowforge.services.tasks import TaskService

# The full expected matrix, written out by hand. If the policy changes, this test must change
# too: that's the point (a reviewer sees the exact behaviour difference).
EXPECTED: dict[Action, set[Role]] = {
    Action.ORG_READ: {Role.OWNER, Role.ADMIN, Role.MEMBER, Role.VIEWER},
    Action.PROJECT_READ: {Role.OWNER, Role.ADMIN, Role.MEMBER, Role.VIEWER},
    Action.TASK_READ: {Role.OWNER, Role.ADMIN, Role.MEMBER, Role.VIEWER},
    Action.WORKFLOW_READ: {Role.OWNER, Role.ADMIN, Role.MEMBER, Role.VIEWER},
    Action.PROJECT_CREATE: {Role.OWNER, Role.ADMIN, Role.MEMBER},
    Action.TASK_CREATE: {Role.OWNER, Role.ADMIN, Role.MEMBER},
    Action.TASK_UPDATE: {Role.OWNER, Role.ADMIN, Role.MEMBER},
    Action.TASK_DELETE: {Role.OWNER, Role.ADMIN},
    Action.MEMBER_MANAGE: {Role.OWNER, Role.ADMIN},
    Action.WORKFLOW_MANAGE: {Role.OWNER, Role.ADMIN},
    Action.API_KEY_MANAGE: {Role.OWNER, Role.ADMIN},
    Action.WEBHOOK_MANAGE: {Role.OWNER, Role.ADMIN},
    Action.AUDIT_READ: {Role.OWNER, Role.ADMIN},
}


def test_every_action_is_covered_by_the_expected_matrix() -> None:
    assert set(EXPECTED) == set(Action)


@pytest.mark.parametrize("role", list(Role))
@pytest.mark.parametrize("action", list(Action))
def test_matrix(role: Role, action: Action) -> None:
    assert can(role, action) is (role in EXPECTED[action])


def test_roles_are_nested() -> None:
    # viewer < member < admin <= owner. Owner-only powers (granting/removing owners) are
    # relationship rules enforced in OrgService, not extra actions.
    assert PERMISSIONS[Role.VIEWER] < PERMISSIONS[Role.MEMBER] < PERMISSIONS[Role.ADMIN]
    assert PERMISSIONS[Role.ADMIN] <= PERMISSIONS[Role.OWNER]


class _Svc:
    @requires(Action.TASK_DELETE)
    async def delete(self, access: OrgAccess, name: str) -> str:
        return f"deleted {name}"


async def test_requires_decorator_allows_and_denies() -> None:
    org, user = uuid.uuid4(), uuid.uuid4()
    svc = _Svc()
    assert await svc.delete(OrgAccess(user, org, Role.ADMIN), "x") == "deleted x"
    with pytest.raises(PermissionDeniedError):
        await svc.delete(OrgAccess(user, org, Role.MEMBER), "x")
    assert svc.delete.__name__ == "delete"  # functools.wraps preserved metadata


def test_service_methods_declare_their_action() -> None:
    assert TaskService.delete.required_action is Action.TASK_DELETE  # type: ignore[attr-defined]
    assert OrgService.add_member.required_action is Action.MEMBER_MANAGE  # type: ignore[attr-defined]
