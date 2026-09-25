# ADR 0005: Authorization as a permission table + decorator

**Status:** accepted (m3), extended in m5 (API key scopes)

## Decision
- `PERMISSIONS: dict[Role, frozenset[Action]]` is the whole policy, reviewed in one screen and
  tested exhaustively (every role × every action) in `tests/unit/test_permissions.py`.
- Service methods declare what they need: `@requires(Action.TASK_UPDATE)`. The decorator reads the
  `OrgAccess` argument and raises `PermissionDeniedError` (403).
- Non-members get **404**, not 403, so ids can't be probed across organizations.
- Relationship rules that aren't simple role checks (only owners can grant owner; the last
  owner can't leave) live in `OrgService`.
- API keys carry explicit scopes (a subset of their creator's permissions) instead of a role.

## Consequences
- Adding a permission = one line in the table + one line in the test.
- Service-internal lookups use undecorated helpers (`ProjectService.load_in_org`) so a key scoped
  to `task:create` doesn't also need `project:read`.
