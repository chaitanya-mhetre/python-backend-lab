# ADR 0003: Keyset (cursor) pagination

**Status:** accepted (m2)

## Context
`LIMIT 50 OFFSET 5000` makes Postgres read and throw away 5000 rows, and returns duplicates or
skips rows when tasks are inserted or deleted while a client pages.

## Decision
Cursor = base64(JSON `[sort, last_value, last_id]`). The next page is
`WHERE (sort_col, id) > (:last_value, :last_id) ORDER BY sort_col, id LIMIT n+1`.
The `id` tie-breaker makes the order total. Fetching `n+1` rows tells us whether a next page
exists without a `COUNT(*)`. A composite index `(project_id, created_at, id)` makes the default
sort an index range scan.

Only non-null columns are sortable (`created_at`, `updated_at`, `priority`, `title`). `due_at`
is nullable, so it's a filter (`due_before`), not a sort key. Supporting NULLs needs a
`COALESCE` expression index; not worth it for v1.

## Consequences
- Stable, fast pages at any depth (measured: `docs/benchmarks/`).
- No "jump to page 37". Clients can only go forward (fine for APIs and infinite scroll).
- A cursor is tied to its sort order; mixing them is rejected with `invalid_cursor`.
