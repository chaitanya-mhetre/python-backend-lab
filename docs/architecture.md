# Architecture

## Components
```
                 ┌───────────────────────── FastAPI process ─────────────────────────┐
 HTTP client ──▶ │ middleware: request-id · rate limit (Redis Lua) · body limit        │
                 │ routers (HTTP only) ─▶ services (rules, @requires, audit) ─▶ repos │
                 └──────────┬───────────────────────┬──────────────────────┬─────────┘
                            │ SQL (asyncpg)         │ cache / limiter      │ enqueue after commit
                      ┌─────▼──────┐          ┌─────▼─────┐          ┌─────▼─────┐
                      │ PostgreSQL │◀─────────│   Redis   │─────────▶│ arq worker │──▶ webhook receivers
                      └────────────┘   SQL    └───────────┘   jobs   └───────────┘     (HMAC-signed)
```

| Component | Why it exists |
|---|---|
| Request-id middleware | Every log line, audit row and error body carries the same id, so one request can be traced end to end. |
| Rate limiter (outermost) | Runs before auth so login brute-force is throttled too. Atomic via Lua. |
| Routers | Keep HTTP (status codes, headers, Pydantic) out of business logic. |
| Services | One place for rules + authorization, reused by API and worker. |
| Repositories | All SQL in one layer: easy to optimise and to reason about queries. |
| Domain (`flowforge.domain`) | Framework-free rules (task state machine, workflow matching, loop limit). |
| PostgreSQL | Source of truth, transactions, constraints (unique success per step, version column). |
| Redis | Three jobs: cache, rate-limit buckets, arq queue. Losing it loses no committed data. |
| arq worker | Runs workflows and webhooks outside the request path so slow receivers never block the API. |

## Request: `PATCH /api/v1/tasks/{id}` with `If-Match: "3"`
1. Middleware assigns `X-Request-ID`, checks the rate limit.
2. `get_principal` authenticates the JWT or API key and sets the actor contextvar.
3. `AccessResolver.for_task` finds the org and the caller's role (404 for non-members).
4. `TaskService.update` (decorated `@requires(TASK_UPDATE)`):
   version check → domain `transition_to` (state machine) → `UPDATE … WHERE version = 3 RETURNING`
   → audit row → notification (if reassigned) → **commit** → invalidate the stats cache →
   enqueue `dispatch_event(task.status_changed)`.
5. Response with `ETag: "4"`.

## Event → workflow → webhook
```
TaskService ──commit──▶ enqueue dispatch_event(event)
dispatch_event: match enabled definitions → INSERT executions (pinned version + steps snapshot)
                + INSERT webhook_deliveries for subscribers → commit → enqueue run_execution / deliver_webhook
run_execution:  loop { lock execution row → next step without success → SAVEPOINT + timeout → handler
                       → INSERT step_run → commit → enqueue emitted events }  (retry w/ backoff on failure)
deliver_webhook: lock delivery → SSRF re-check → sign → POST → success | schedule retry (1m…12h) | fail
```

## Cron schedules
```
arq cron (every worker, second 0 of each minute) → schedule_tick
schedule_tick: for each enabled schedule.cron workflow → latest fire time ≤ now (skip if older than
               the lookback or before the workflow existed) → INSERT execution … ON CONFLICT
               (definition_id, scheduled_for) DO NOTHING RETURNING id → commit → enqueue run_execution
```
Several workers tick at once; the partial unique index `uq_workflow_executions_schedule` makes
the database pick exactly one winner per scheduled time, so no leader election or Redis lock is needed.

Loop protection: events carry `depth`; executions triggered at depth > 5 fail with
`workflow_loop_detected`. Webhook subscribers only receive depth-0 events.

See `docs/erd.md` for the schema and `docs/adr/` for the reasoning behind each choice.
