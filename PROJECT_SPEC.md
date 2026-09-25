# Python Backend Lab — "Flowforge"
> A workflow/task automation backend (FastAPI + PostgreSQL + Redis) built milestone by milestone, so that every milestone forces a new layer of Python depth.

## 1. Problem & why it exists
Teams automate repeating work: "when a task moves to Done, notify the channel, create a follow-up task, and call a webhook."
Flowforge is a small but realistic backend for that. It covers organizations, projects, tasks, workflow definitions
(trigger → steps), workflow executions, permissions, audit logs, API keys, webhooks, and notifications.

**Why this project, first:** my production experience is mostly Flutter/TypeScript/Go, plus AI-assisted code.
Interviewers for Python backend roles (Zeko AI, EaseOps) check Python fundamentals: typing, OOP, async, generators,
context managers, testing. This repo is where I learn those **by hand**, in a domain that isn't a todo app.
It's the learning ground. `production-fastapi` is the hardened successor.

## 2. What this proves to an employer
| Skill | Target requirement it maps to |
|---|---|
| Idiomatic, typed Python (mypy strict) | Zeko: "strong Python, OOP"; EaseOps: "Python" |
| FastAPI + Pydantic v2 | Zeko, EaseOps: "FastAPI" |
| PostgreSQL schema design, indexes, transactions | Zeko: "schema design, indexing"; EaseOps: "PostgreSQL" |
| async/await, concurrency | Zeko: "async/await" |
| Redis caching + rate limiting | EaseOps: "Redis" |
| Background jobs | EaseOps: "async processing" |
| JWT + RBAC | EaseOps: "authentication" |
| pytest unit + integration tests | EaseOps: "testing" |
| Docker, Compose, GitHub Actions | all backend roles |

## 3. Scope
### In scope (v1)
- Users, organizations (membership + roles), projects, tasks (CRUD, pagination, filtering, sorting)
- Workflow definitions: trigger (`task.created`, `task.status_changed`, `schedule.cron`) + ordered steps
  (`create_task`, `update_field`, `send_notification`, `call_webhook`, `delay`)
- Workflow engine: executes steps in a background worker, records step results, retries failed steps
- RBAC: `owner`, `admin`, `member`, `viewer` per organization; permission checks at the service layer
- API keys (hashed, scoped) for machine access
- Outgoing webhooks with HMAC signatures, plus a delivery log
- In-app notifications
- Audit log for every mutating action
- Redis: response caching for hot reads, a token-bucket rate limiter, a job queue (arq)
- OpenAPI docs, pytest suite, Docker Compose, GitHub Actions CI

### Out of scope (explicitly)
- Frontend UI (Swagger UI is the client)
- Multi-tenancy isolation guarantees beyond org scoping (that's `production-fastapi`)
- Email delivery (notifications are in-app only)
- Cloud deployment (done later in `cloud-infra-lab`)
- A visual workflow builder

## 4. Architecture
```
            ┌──────────────┐
 client ──▶ │  FastAPI app │  routers → dependencies (auth, db session, rate limit)
            └──────┬───────┘
                   │ calls
            ┌──────▼───────┐
            │  services/   │  business rules, permission checks, emits domain events
            └──────┬───────┘
                   │
      ┌────────────┼──────────────┐
┌─────▼─────┐ ┌────▼────┐   ┌─────▼──────┐
│repositories│ │ Redis   │   │ event bus  │ (in-process dispatcher → enqueues jobs)
│(SQLAlchemy │ │ cache / │   └─────┬──────┘
│ async)     │ │ limiter │         │ arq job
└─────┬─────┘ └─────────┘   ┌──────▼──────┐
┌─────▼─────┐               │ worker (arq)│ runs workflow steps, webhook deliveries
│PostgreSQL │◀──────────────┤             │
└───────────┘               └─────────────┘
```
- **Routers**: HTTP only. Parse and validate with Pydantic, then call a service. No SQL here.
  They exist to keep transport separate from logic.
- **Dependencies** (`Depends`): the current user, the DB session per request, rate limiting.
  This is where I learn dependency injection the FastAPI way.
- **Services**: own the business rules and the permission checks. Unit-testable with fake repositories.
- **Repositories**: the only place SQL lives. That makes queries easy to optimise and test in isolation.
- **Event dispatcher**: services emit `TaskStatusChanged` and similar events. The dispatcher finds matching workflow
  definitions and enqueues executions. This decouples "a task changed" from "automation runs".
- **Worker (arq)**: runs executions outside the request path, so a slow webhook never blocks an API call.
- **Redis**: the rate-limiter state, the cache, and the arq queue in one dependency. Its limits are discussed in §8.

## 5. Tech stack & justification
| Choice | Why | Alternatives considered |
|---|---|---|
| Python 3.12, uv | modern typing (`type` aliases, generics syntax), fast installs | poetry |
| FastAPI + Pydantic v2 | async-native, OpenAPI for free, the stack in my target JDs | Django REST (heavier), Flask (no async by default) |
| SQLAlchemy 2.0 (async) + Alembic | industry standard; forces me to understand sessions and transactions | SQLModel (hides too much), raw asyncpg (I already did raw SQL in NestJS) |
| PostgreSQL 16 | JSONB for step configs, partial indexes, strong transactions | — |
| Redis 7 + arq | arq is small, asyncio-native, and readable (I can read its source) | Celery (used in `production-fastapi`), RQ |
| pytest, pytest-asyncio, httpx, testcontainers | real Postgres/Redis in tests | mocking the DB (catches fewer bugs) |
| ruff, mypy --strict | enforce typing discipline | — |

## 6. Data model
```
users(id uuid pk, email citext unique, password_hash, full_name, created_at)
organizations(id uuid pk, name, slug unique, created_at)
memberships(user_id fk, org_id fk, role enum(owner,admin,member,viewer), pk(user_id, org_id))
projects(id uuid pk, org_id fk, name, archived_at null, created_at)
    index (org_id) where archived_at is null
tasks(id uuid pk, project_id fk, title, description, status enum(todo,in_progress,done,cancelled),
      priority smallint, assignee_id fk null, due_at null, created_by, created_at, updated_at, version int)
    index (project_id, status), index (assignee_id, status), index (due_at) where status <> 'done'
workflow_definitions(id uuid pk, org_id fk, name, trigger_type, trigger_filter jsonb, enabled bool, version int)
workflow_steps(id uuid pk, definition_id fk, position int, step_type, config jsonb, unique(definition_id, position))
workflow_executions(id uuid pk, definition_id fk, definition_version int, trigger_event jsonb,
      status enum(pending,running,succeeded,failed,cancelled), started_at, finished_at, error text)
    index (definition_id, status), index (status) where status in ('pending','running')
step_runs(id uuid pk, execution_id fk, step_id fk, attempt int, status, output jsonb, error, started_at, finished_at)
api_keys(id uuid pk, org_id fk, name, prefix char(8), key_hash, scopes text[], last_used_at, revoked_at)
    unique(prefix)
webhooks(id uuid pk, org_id fk, url, secret_encrypted, events text[], active bool)
webhook_deliveries(id uuid pk, webhook_id fk, event_type, payload jsonb, status_code, attempt, next_retry_at, delivered_at)
notifications(id uuid pk, user_id fk, kind, payload jsonb, read_at null, created_at)
    index (user_id, created_at desc) where read_at is null
audit_logs(id bigserial pk, org_id, actor_type enum(user,api_key,system), actor_id, action, entity_type,
      entity_id, before jsonb, after jsonb, request_id, created_at)
    index (org_id, created_at desc), index (entity_type, entity_id)
```
- `tasks.version` gives optimistic locking (`UPDATE ... WHERE id=? AND version=?`), so concurrent edits don't overwrite each other silently.
- Executions pin `definition_version`, so editing a workflow doesn't change runs already in flight.

## 7. API / interface design
All under `/api/v1`. List endpoints take `?limit=&cursor=&sort=&filter[...]=`.
```
POST   /auth/register            POST /auth/login → {access_token}
GET    /me
POST   /orgs                     GET /orgs
POST   /orgs/{org}/members       PATCH /orgs/{org}/members/{user}   DELETE ...
POST   /orgs/{org}/projects      GET /orgs/{org}/projects
POST   /projects/{p}/tasks       GET /projects/{p}/tasks?status=todo&assignee=me&sort=-due_at&limit=50&cursor=...
GET    /tasks/{id}               PATCH /tasks/{id}  (If-Match: <version>)   DELETE /tasks/{id}
POST   /orgs/{org}/workflows     GET /orgs/{org}/workflows   PUT /workflows/{id}   POST /workflows/{id}:enable
GET    /workflows/{id}/executions   GET /executions/{id}  (with step_runs)   POST /executions/{id}:cancel
POST   /orgs/{org}/api-keys → {key: "ff_live_ab12cd34_<secret>"} (shown once)   DELETE /api-keys/{id}
POST   /orgs/{org}/webhooks      GET /webhooks/{id}/deliveries   POST /webhook-deliveries/{id}:redeliver
GET    /notifications?unread=true   POST /notifications/{id}:read
GET    /orgs/{org}/audit-logs?entity_type=task&entity_id=...
GET    /healthz   GET /readyz
```
Error format (every endpoint): `{"error": {"code": "task_version_conflict", "message": "...", "request_id": "..."}}`

## 8. Key engineering problems
1. **Transaction boundaries.** A task update, its audit log row, and the "enqueue workflow" intent must all commit together.
   If I enqueue to Redis before the DB commit and the commit then fails, a phantom execution runs.
   M4 solves it the simple way (enqueue after commit, accept the rare loss) and documents the trade-off.
   `production-fastapi` does it properly with an outbox.
2. **Idempotent step execution.** A worker crash mid-step causes a retry. Steps like `create_task` must not create duplicates,
   so each one uses an idempotency key `(execution_id, step_id)`.
3. **Workflow loops.** Workflow A creates a task, which triggers workflow B, which creates a task, which triggers A...
   Fix: a depth counter carried in the event, capped at 5, plus a loop error.
4. **Pagination correctness.** Offset pagination skips or duplicates rows while data changes, so I use keyset (cursor) pagination on `(sort_col, id)`.
5. **Rate limiting.** A token bucket in Redis, made atomic with a Lua script. Otherwise two concurrent requests can both read "1 token left".
6. **N+1 queries.** Task lists that include assignees need `selectinload`, verified with the SQL echo log in a test.
7. **Webhook security.** Sign payloads with HMAC-SHA256 plus a timestamp header to stop replays. Block private IP ranges to stop SSRF.

## 9. Milestones
Each milestone names the **Python concepts** it forces me to learn.

**M1: Core Python domain, no web (week 1–2)** ← *the first thing I build*
- Goal: model the domain in pure Python: `Task`, `Project`, `WorkflowDefinition`, `Step` as dataclasses/Pydantic models.
  Add an in-memory repository behind a `Protocol`, and a `WorkflowEngine` that runs steps synchronously.
- Python: type hints, `dataclasses`, `enum`, `Protocol`, ABCs vs Protocols, generics (`Repository[T]`), custom exceptions,
  `__repr__`/`__eq__`, iterators (a `paginate()` generator), `functools`.
- Acceptance: `pytest` covers engine happy path, step failure, loop detection; `mypy --strict` clean; no framework imports.

**M2: FastAPI + PostgreSQL CRUD (week 3–4)**
- Users, auth (JWT via `pyjwt`, argon2 hashing), orgs, projects, tasks; SQLAlchemy async + Alembic migrations.
- Python: `async`/`await`, async context managers (the DB session), dependency injection through `Depends`, decorators.
- Acceptance: integration tests against Postgres in testcontainers; OpenAPI renders; keyset pagination tests.

**M3: RBAC, audit log, optimistic locking (week 5)**
- A permission matrix as data (`PERMISSIONS: dict[Role, set[Action]]`); an `@requires(Action.TASK_UPDATE)` decorator; an audit log written in the same transaction.
- Python: decorators with arguments, `contextvars` (request id / actor), `ParamSpec` for typed decorators.
- Acceptance: a test matrix of role × action → 403/200; concurrent updates produce a 409.

**M4: Workflow engine as async background jobs (week 6–7)**
- Event dispatcher → arq queue → worker runs steps; `step_runs` with retries + exponential backoff; cancellation.
- Python: `asyncio` (gather, timeouts, `TaskGroup`, cancellation semantics), `asyncio.Semaphore` for per-org concurrency, generators for step pipelines.
- Acceptance: kill the worker mid-execution → on restart the execution resumes or fails cleanly (test documented); no duplicate `create_task`.

**M5: Redis caching, rate limiting, API keys (week 8)**
- Cache-aside on `GET /projects/{p}` with invalidation on write; Lua token bucket; hashed, scoped API keys.
- Python: `hashlib`/`secrets`, a `lru_cache` vs Redis discussion, typed settings (pydantic-settings).
- Acceptance: a rate-limit test (429 + `Retry-After`); a cache-invalidation test; an API key authenticates and respects its scope.

**M6: Webhooks + notifications (week 9)**
- HMAC-signed delivery, a retry schedule (1m, 5m, 30m, 2h, 12h), SSRF guard, redelivery endpoint.
- Python: `httpx.AsyncClient` with timeouts, the `ipaddress` module, testing with `respx`.
- Acceptance: signature verification example in `examples/`; private-IP URLs rejected; retry schedule tested with a frozen clock.

**M7: Packaging, Docker, CI, docs (week 10)**
- Multi-stage Dockerfile (non-root), Compose (api, worker, postgres, redis), GitHub Actions (ruff, mypy, pytest with service containers, docker build).
- Docs: architecture, ERD, API guide, decisions (`docs/adr/`), testing strategy, security notes.
- Acceptance: `make up && make test` works from a fresh clone; CI green; README complete.

## 10. Testing strategy
- **Unit**: services with fake repositories (no I/O), the engine, the permission matrix, the backoff schedule.
- **Integration**: repositories and routes against real Postgres/Redis (testcontainers); the transaction rollback fixture per test.
- **Concurrency**: optimistic-lock conflicts; rate-limiter races (fire 50 concurrent requests, assert exactly N succeed).
- **Contract**: snapshot of the OpenAPI schema, so an accidental breaking change fails CI.
- Coverage is a signal, not a goal. Target meaningful coverage of services.

## 11. Observability
- Structured JSON logs (structlog) with `request_id`, `org_id`, `user_id`, `execution_id`.
- `/healthz` (process up), `/readyz` (DB + Redis reachable).
- Basic Prometheus metrics via `prometheus-fastapi-instrumentator` (request rate/latency) plus custom `workflow_executions_total{status}`.

## 12. Security
- argon2 password hashing; short-lived JWT (15 min). Refresh tokens are deferred to `production-fastapi`, and I say so in the docs.
- API keys: store only a SHA-256 hash plus a prefix for lookup; show the key once.
- Authorization at the service layer, not only the router (defence in depth).
- Webhook secrets encrypted at rest (Fernet); SSRF protection; request body size limits.
- Parameterised queries only (the ORM); no string-formatted SQL.

## 13. Deployment
Local only via Docker Compose. Cloud deployment is demonstrated in `cloud-infra-lab` using `production-fastapi`.

## 14. Evaluation / measurements to collect
All TBD until measured:
- p50/p95 latency of `GET /projects/{p}/tasks` with 100k tasks, with vs without the composite index: `TBD — measure with scripts/bench_tasks.py`
- Cache hit ratio under a scripted read workload: `TBD`
- Workflow execution throughput per worker: `TBD — measure with scripts/bench_workflows.py`

## 15. Prerequisite learning
- `learning/python/` (types, OOP, iterators/generators, decorators, context managers, async)
- `learning/backend/http-rest`, `learning/backend/fastapi`, `learning/databases/postgresql`, `learning/databases/transactions`
- `learning/backend/redis`

## 16. Interview talking points
- "Walk me through what happens when a task status changes." (the event → queue → worker → steps → audit trail)
- "How do you prevent lost updates?" (optimistic locking with a version column)
- "Why cursor pagination?"
- "Protocol vs ABC? When do you use a generator?"
- "How does your rate limiter stay correct under concurrency?" (the Lua script is atomic)
- "What happens if the worker crashes mid-step?" (idempotency keys, retries, and the enqueue-after-commit trade-off)

## 17. Resume bullet templates
- Built a workflow-automation backend in Python (FastAPI, PostgreSQL, Redis) with RBAC, audit logging, HMAC-signed webhooks, and an async job engine with retries and idempotent steps.
- Cut task-list p95 latency from [MEASURED_VALUE] to [MEASURED_VALUE] ms on [MEASURED_VALUE] rows with composite + partial indexes and keyset pagination.

## 18. Open questions / uncertainties
- arq vs Celery here: arq is chosen for readability. If arq's maintenance status is poor when I start, switch to Celery/RQ and note the change.
- Whether to publish this repo publicly or keep it as the private learning repo and publish only `production-fastapi`. Decide after M7.
- Timeline estimates assume ~10–12 h/week alongside a full-time job. Unverified; adjust after M1.
